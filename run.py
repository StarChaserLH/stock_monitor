#!/usr/bin/env python3
"""
A股量化监测与推送系统 - 主入口。

Usage:
    # 同时启动 Web + 监测（默认）
    python run.py

    # 仅启动 Web 管理界面
    python run.py web
    python run.py web-only

    # 仅运行监测（守护进程）
    python run.py monitor
    python run.py daemon

    # 单次执行
    python run.py once

    # 生成策略（交互式 / 命令行）
    python run.py strategy create --desc "当5日线上穿20日线时买入"

    # 列出策略
    python run.py strategy list

    # 查看账户状态
    python run.py account
"""

import argparse
import logging
import signal
import sys
import threading
import time
from pathlib import Path

# 确保项目根目录在 sys.path
_PROJECT_ROOT = Path(__file__).parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from app.config import AppConfig
from app.scheduler.loop import MonitorLoop
from app.web.server import run_web

logger = logging.getLogger(__name__)

_shutdown_flag = False


def setup_logging(config: AppConfig) -> None:
    """配置日志系统（5 个备份 × 1MB 轮转）。"""
    log_config = config.system
    log_dir = Path(log_config.log_file).parent
    log_dir.mkdir(parents=True, exist_ok=True)

    from logging.handlers import RotatingFileHandler
    logging.basicConfig(
        level=getattr(logging, log_config.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            RotatingFileHandler(log_config.log_file, maxBytes=1024*1024,
                               backupCount=5, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def _signal_handler(signum, frame):
    """优雅退出信号处理。"""
    global _shutdown_flag
    logger.info(f"收到信号 {signum}，正在退出...")
    _shutdown_flag = True


def cmd_start(args, config: AppConfig) -> None:
    """同时启动 Web 服务和后台监测（默认模式）。"""
    global _shutdown_flag

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    loop = MonitorLoop(config)

    # 在后台线程启动监测调度器
    def run_monitor():
        loop.run_daemon()

    monitor_thread = threading.Thread(target=run_monitor, daemon=True, name="monitor-daemon")
    monitor_thread.start()
    logger.info("后台监测线程已启动")

    # 主线程运行 Web 服务
    try:
        run_web(
            config_path=getattr(args, 'config', None),
            host=getattr(args, 'host', None) or config.web.host,
            port=getattr(args, 'port', None) or config.web.port,
            monitor_loop=loop,
            debug=getattr(args, 'debug', False),
        )
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        logger.info("系统已退出")


def cmd_web_only(args, config: AppConfig) -> None:
    """仅启动 Web 管理界面（不运行监测）。"""
    run_web(
        config_path=args.config,
        host=args.host or config.web.host,
        port=args.port or config.web.port,
        monitor_loop=None,
        debug=args.debug,
    )


def cmd_monitor_only(args, config: AppConfig) -> None:
    """仅运行监测调度器（无 Web）。"""
    loop = MonitorLoop(config)
    loop.run_daemon()


def cmd_once(args, config: AppConfig) -> None:
    """单次执行模式。"""
    loop = MonitorLoop(config)
    summary = loop.run_once()

    try:
        from rich.console import Console
        from rich.table import Table

        console = Console()
        table = Table(title="监测执行报告")
        table.add_column("指标", style="cyan")
        table.add_column("值", style="green")
        table.add_row("状态", summary.get("status", "?"))
        table.add_row("标的数", str(summary.get("symbols", 0)))
        table.add_row("策略数", str(summary.get("strategies", 0)))
        table.add_row("信号数", str(summary.get("signals", 0)))
        table.add_row("成交数", str(summary.get("trades", 0)))
        table.add_row("耗时(秒)", str(summary.get("duration", 0)))

        acc = summary.get("account", {})
        if acc:
            table.add_row("---", "---")
            table.add_row("总资产", f"{acc.get('total', 0):.2f}")
            table.add_row("可用资金", f"{acc.get('cash', 0):.2f}")
            table.add_row("持仓市值", f"{acc.get('market_value', 0):.2f}")
            table.add_row("累计盈亏", f"{acc.get('pnl', 0):.2f} ({acc.get('pnl_pct', 0):.2f}%)")

        console.print(table)
    except ImportError:
        print(f"\n状态: {summary.get('status')}")
        print(f"信号: {summary.get('signals', 0)}, 成交: {summary.get('trades', 0)}")
        acc = summary.get("account", {})
        if acc:
            print(f"总资产: {acc.get('total', 0):.2f}, 盈亏: {acc.get('pnl', 0):.2f}")


def cmd_strategy(args, config: AppConfig) -> None:
    """策略管理命令。"""
    from app.strategy.engine import StrategyEngine
    from app.strategy.llm import LLMStrategyGenerator

    engine = StrategyEngine(config)

    if args.action == "list":
        records = engine.list_all()
        if not records:
            print("暂无策略")
            return
        for r in records:
            status = "启用" if r.enabled else "禁用"
            print(f"  [{r.id}] {r.name} ({status}) - {r.description[:60]}")

    elif args.action == "create":
        desc = args.desc
        if not desc:
            desc = input("请输入策略自然语言描述: ")

        name = args.name or input("策略名称 (可选): ").strip()
        if not name:
            name = f"策略_{desc[:20]}"

        llm = LLMStrategyGenerator(config.llm)
        print(f"正在调用 DeepSeek 生成策略...")
        result = llm.generate(desc)
        code = result["code"]
        desc_short = result.get("desc", desc[:60])
        record = engine.create(name=name, description=desc_short, code=code)
        print(f"策略已创建: [{record.id}] {name}")
        print(f"源代码已保存至: strategies/{record.id}.py")

        # 验证
        error = llm.test_generated_code(code)
        if error:
            print(f"警告: 策略测试发现问题: {error}")
        else:
            print("策略测试通过")

    elif args.action == "get":
        record = engine.get(args.id)
        if record:
            print(f"名称: {record.name}")
            print(f"描述: {record.description}")
            print(f"状态: {'启用' if record.enabled else '禁用'}")
            print(f"创建: {record.created_at}")
            print(f"更新: {record.updated_at}")
            print(f"\n代码:\n{record.code}")
        else:
            print(f"策略不存在: {args.id}")

    elif args.action == "toggle":
        engine.set_enabled(args.id, not args.disable)
        action = "禁用" if args.disable else "启用"
        print(f"策略 {args.id} 已{action}")

    elif args.action == "delete":
        engine.delete(args.id)
        print(f"策略 {args.id} 已删除")


def cmd_account(args, config: AppConfig) -> None:
    """查看账户状态。"""
    from app.trade.positions import PositionStore
    from app.market.data import MarketData

    store = PositionStore(str(Path(config.system.data_dir) / "positions.json"))
    market = MarketData(config.market)
    real_pos = store.list_all()

    total_mv, total_cost, total_pnl = 0.0, 0.0, 0.0
    syms = [p["symbol"] for p in real_pos]
    price_map = {}
    if syms:
        try:
            quotes = market.get_realtime_quotes(syms)
            for _, r in quotes.iterrows():
                s = str(r.get("symbol", ""))
                if len(s) > 6:
                    s = s[2:]
                price_map[s] = float(r.get("price", 0) or 0)
        except Exception:
            pass

    print(f"\n{'='*50}")
    print(f"  持仓账户")
    print(f"{'='*50}")

    if real_pos:
        print(f"  代码        数量      成本      现价      市值        浮盈")
        for p in real_pos:
            sym = p["symbol"]
            price = price_map.get(sym, 0)
            mv = price * p["shares"] if price > 0 else 0
            pnl = (price - p["cost"]) * p["shares"] if price > 0 else 0
            total_mv += mv
            total_cost += p["cost"] * p["shares"]
            total_pnl += pnl
            sign = "+" if pnl >= 0 else ""
            print(f"  {sym:6s}  {p['shares']:>6d}   {p['cost']:.3f}   {price:.3f}   {mv:>8.2f}   {sign}{pnl:,.2f}")
        print(f"{'='*50}")
        pnl_pct = (total_pnl / total_cost * 100) if total_cost > 0 else 0
        print(f"  总持仓成本: {total_cost:>12.2f}")
        print(f"  总市值:     {total_mv:>12.2f}")
        print(f"  累计盈亏:   {total_pnl:>12.2f} ({pnl_pct:+.2f}%)")
    else:
        print(f"  暂无持仓记录")
        print(f"  请在 Web 持仓管理页面添加")
    print(f"{'='*50}")


def main():
    parser = argparse.ArgumentParser(
        description="A股量化监测与推送系统",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "-c", "--config",
        default=None,
        help="配置文件路径（默认: config.yaml）",
    )

    sub = parser.add_subparsers(dest="command", help="运行模式")

    # ---- Web 相关 ----
    web_parser = sub.add_parser("web", help="仅启动 Web 管理界面（不运行监测）")
    web_parser.add_argument("--host", default=None, help="监听地址（默认: 配置文件 web.host）")
    web_parser.add_argument("--port", type=int, default=None, help="监听端口（默认: 配置文件 web.port）")
    web_parser.add_argument("--debug", action="store_true", help="开启 Flask 调试模式")

    web_only_parser = sub.add_parser("web-only", help="仅启动 Web（同 web）")
    web_only_parser.add_argument("--host", default=None)
    web_only_parser.add_argument("--port", type=int, default=None)
    web_only_parser.add_argument("--debug", action="store_true")

    # ---- 监测相关 ----
    sub.add_parser("monitor", help="仅运行监测调度器（无 Web）")
    sub.add_parser("monitor-only", help="仅运行监测（同 monitor）")
    sub.add_parser("daemon", help="守护进程模式（同 monitor）")
    sub.add_parser("once", help="单次执行监测")

    # ---- 策略管理 ----
    strat_parser = sub.add_parser("strategy", help="策略管理")
    strat_sub = strat_parser.add_subparsers(dest="action")

    strat_sub.add_parser("list", help="列出所有策略")

    strat_create = strat_sub.add_parser("create", help="创建策略（LLM生成）")
    strat_create.add_argument("--desc", help="自然语言策略描述")
    strat_create.add_argument("--name", help="策略名称")

    strat_get = strat_sub.add_parser("get", help="查看策略详情")
    strat_get.add_argument("id", help="策略 ID")

    strat_toggle = strat_sub.add_parser("toggle", help="启用/禁用策略")
    strat_toggle.add_argument("id", help="策略 ID")
    strat_toggle.add_argument("--disable", action="store_true", help="禁用")

    strat_delete = strat_sub.add_parser("delete", help="删除策略")
    strat_delete.add_argument("id", help="策略 ID")

    # ---- 账户 ----
    sub.add_parser("account", help="查看账户状态")

    args = parser.parse_args()

    # 加载配置
    config = AppConfig(args.config)
    setup_logging(config)
    logger = logging.getLogger(__name__)

    # ---- 路由命令 ----
    if args.command is None:
        # 默认模式：Web + 监测同时启动
        logger.info("启动默认模式（Web + 监测）")
        cmd_start(args, config)

    elif args.command in ("web", "web-only"):
        logger.info("启动 Web-only 模式")
        cmd_web_only(args, config)

    elif args.command in ("monitor", "monitor-only", "daemon"):
        logger.info("启动监测-only 模式")
        cmd_monitor_only(args, config)

    elif args.command == "once":
        cmd_once(args, config)

    elif args.command == "strategy":
        # 检查 LLM API Key
        if not config.llm.api_key and getattr(args, "action", None) == "create":
            logger.warning("LLM API Key 未配置，策略生成功能不可用")
            logger.warning("请在 .env 文件中设置 DEEPSEEK_API_KEY")
        cmd_strategy(args, config)

    elif args.command == "account":
        cmd_account(args, config)


if __name__ == "__main__":
    main()

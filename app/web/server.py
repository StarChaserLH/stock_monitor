"""
Web 管理控制台。

提供完整的可视化操作界面：
  - 登录 / 会话管理
  - 仪表盘（账户、持仓、信号）
  - 标的管理（分组增删、模式切换）
  - 策略管理（LLM 生成、代码编辑、启用/禁用）
  - 交易记录（筛选、分页）
  - 系统状态（调度控制、日志查看）

所有页面使用 Jinja2 + Alpine.js + Tailwind CSS 渲染。
"""

import json
import logging
import os
from datetime import datetime
from functools import wraps
from pathlib import Path
from typing import Optional

import pandas as pd

from flask import (
    Flask,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from app.config import AppConfig
from app.market.data import MarketData
from app.market.lof_data import LOFDataProvider
from app.notify.manager import NotificationManager
from app.strategy.engine import StrategyEngine
from app.strategy.llm import LLMStrategyGenerator
from app.symbols.manager import SymbolManager
from app.trade.paper import PaperBroker
from app.trade.positions import PositionStore
from app.trade.broker import OrderStatus
from app.backtest.engine import BacktestEngine
from app.symbols.groups import GroupStore, DEFAULT_GROUPS

logger = logging.getLogger(__name__)

# 全局引用，由 run_web() 注入
_monitor_loop = None
_app_config: Optional[AppConfig] = None


_SECTOR_KNOWN: dict[str, str] = {
    # ETF
    "510050": "上证50", "510300": "沪深300", "510500": "中证500",
    "159915": "创业板", "588000": "科创50", "159949": "创业板50",
    "512880": "证券", "512010": "医药", "159995": "芯片",
    "512660": "军工", "515790": "光伏", "516510": "云计算",
    "515650": "消费50", "510900": "恒生国企", "513050": "中概互联",
    "159920": "恒生ETF", "563020": "红利低波", "512980": "传媒",
    "512760": "芯片", "515030": "新能源车", "515880": "通信",
    "515050": "5G通信", "512000": "券商", "515000": "科技",
    "159781": "科创创业", "159326": "电网设备", "159206": "卫星",
    "159934": "黄金", "159869": "游戏",
    # 个股
    "002624": "游戏", "002241": "消费电子", "601111": "航空",
    "603099": "旅游", "002261": "IT服务", "600703": "半导体",
    "000063": "通信设备", "002050": "机器人", "000938": "云计算",
}

def _get_sector(symbol: str) -> str:
    """获取标的所属板块。"""
    s = str(symbol)
    known = _SECTOR_KNOWN.get(s, "")
    if known:
        return known
    if s.startswith(("510", "512", "513", "515", "516", "588")): return "上证ETF"
    if s.startswith(("159",)): return "深证ETF"
    if s.startswith("60"): return "沪市主板"
    if s.startswith("00"): return "深市主板"
    if s.startswith("30"): return "创业板"
    if s.startswith("688"): return "科创板"
    return ""


def get_monitor_loop():
    """获取全局监测循环实例（由 run_web 注入）。"""
    return _monitor_loop


def login_required(f):
    """会话认证装饰器。"""

    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login_page"))
        return f(*args, **kwargs)

    return decorated


# ------------------------------------------------------------------
# 创建应用
# ------------------------------------------------------------------


def create_app(
    config: AppConfig,
    monitor_loop=None,
) -> Flask:
    """创建并配置 Flask 应用。

    Args:
        config: 应用配置实例。
        monitor_loop: MonitorLoop 实例（可选，用于系统控制和状态）。

    Returns:
        Flask 应用对象。
    """
    global _monitor_loop, _app_config
    _monitor_loop = monitor_loop
    _app_config = config

    template_dir = Path(__file__).parent / "templates"
    app = Flask(__name__, template_folder=str(template_dir))
    app.secret_key = config.web.secret_key

    # ---- 模块初始化 ----
    market = MarketData(config.market)
    symbols = SymbolManager(config.symbols, market)
    engine = StrategyEngine(config)
    broker = PaperBroker(config)
    llm = LLMStrategyGenerator(config.llm) if config.llm.api_key else None
    # 推送管理器：复用监测循环的实例或新建
    notify_mgr = monitor_loop._notify_mgr if (monitor_loop and hasattr(monitor_loop, '_notify_mgr')) \
        else NotificationManager(config)
    # LOF 数据提供器
    lof_provider = LOFDataProvider(config.lof)
    positions = PositionStore(str(Path(config.system.data_dir) / "positions.json"))
    group_store = GroupStore(str(Path(config.system.data_dir) / "symbol_groups.json"))
    group_store.ensure_defaults()
    backtest_engine = BacktestEngine(config, engine, market)

    # ==================================================================
    # 页面路由（返回 HTML）
    # ==================================================================

    @app.route("/")
    def index():
        if session.get("logged_in"):
            return redirect(url_for("dashboard_page"))
        return redirect(url_for("login_page"))

    @app.route("/login", methods=["GET", "POST"])
    def login_page():
        error = None
        if request.method == "POST":
            password = request.form.get("password", "")
            if password == config.web.password:
                session["logged_in"] = True
                session["login_time"] = datetime.now().isoformat()
                return redirect(url_for("dashboard_page"))
            error = "密码错误，请重试"

        if session.get("logged_in"):
            return redirect(url_for("dashboard_page"))
        return render_template("login.html", error=error)

    @app.route("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login_page"))

    @app.route("/dashboard")
    @login_required
    def dashboard_page():
        return render_template("dashboard.html", config=config)

    @app.route("/instruments")
    @login_required
    def instruments_page():
        return render_template("instruments.html", config=config)

    @app.route("/strategies")
    @login_required
    def strategies_page():
        return render_template("strategies.html", config=config)

    @app.route("/trades")
    @login_required
    def trades_page():
        return render_template("trades.html", config=config)

    @app.route("/system")
    @login_required
    def system_page():
        return render_template("system.html", config=config)

    @app.route("/push/settings")
    @login_required
    def push_settings_page():
        return render_template("push_settings.html", config=config)

    @app.route("/push/history")
    @login_required
    def push_history_page():
        return render_template("push_history.html", config=config)

    @app.route("/lof/premium")
    @login_required
    def lof_premium_page():
        return render_template("lof_premium.html", config=config)

    @app.route("/positions")
    @login_required
    def positions_page():
        return render_template("positions.html", config=config)

    @app.route("/signals")
    @login_required
    def signals_page():
        return render_template("signals.html", config=config)

    @app.route("/backtest")
    @login_required
    def backtest_page():
        return render_template("backtest.html", config=config)

    # ==================================================================
    # API 路由 - 仪表盘
    # ==================================================================

    @app.route("/api/dashboard", methods=["GET"])
    @login_required
    def api_dashboard():
        # 真实持仓数据
        real_positions = positions.list_all()
        pos_syms = [p["symbol"] for p in real_positions]

        # 信号数据
        import sqlite3
        db_path = engine._db_path
        signals = []
        signal_syms = []
        if db_path.exists():
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            held_syms = set(p["symbol"] for p in real_positions)
            total_signals = conn.execute("SELECT COUNT(*) FROM signal_log WHERE symbol IN ({})".format(
                ",".join("?" * len(held_syms))), list(held_syms)
            ).fetchone()[0] if held_syms else 0
            if held_syms:
                rows = conn.execute(
                    "SELECT * FROM signal_log WHERE symbol IN ({}) ORDER BY timestamp DESC LIMIT 10".format(
                        ",".join("?" * len(held_syms))),
                    list(held_syms)
                ).fetchall()
            else:
                rows = []
            conn.close()
            for r in rows:
                d = dict(r)
                signal_syms.append(d["symbol"])
                signals.append(d)

        # 一次行情请求拿到所有名称和价格
        all_syms = list(set(pos_syms + signal_syms))
        name_map = {}
        price_map = {}
        if all_syms:
            try:
                qs = market.get_realtime_quotes(all_syms)
                sym_c = "symbol" if "symbol" in qs.columns else "代码"
                name_c = "name" if "name" in qs.columns else "名称"
                price_c = "price" if "price" in qs.columns else "最新价"
                for _, row in qs.iterrows():
                    s = str(row.get(sym_c, ""))
                    if len(s) > 6:
                        s = s[2:]
                    name_map[s] = str(row.get(name_c, ""))
                    price_map[s] = float(row.get(price_c, 0) or 0)
            except Exception:
                pass

        # 计算真实持仓盈亏
        pos_list = []
        total_mv = 0.0
        total_cost = 0.0
        total_pnl = 0.0
        for p in real_positions:
            sym = p["symbol"]
            price = price_map.get(sym, 0)
            shares = p["shares"]
            cost = p["cost"]
            mv = price * shares if price > 0 else 0
            pnl = (price - cost) * shares if price > 0 else 0
            total_mv += mv
            total_cost += cost * shares
            total_pnl += pnl
            pos_list.append({
                "symbol": sym,
                "name": name_map.get(sym, ""),
                "shares": shares,
                "avg_cost": cost,
                "market_value": round(mv, 2),
                "unrealized_pnl": round(pnl, 2),
            })

        pnl_pct = (total_pnl / total_cost * 100) if total_cost > 0 else 0
        account_data = {
            "total_capital": round(total_cost + total_pnl, 2),
            "available_cash": 0,
            "market_value": round(total_mv, 2),
            "total_pnl": round(total_pnl, 2),
            "total_pnl_pct": round(pnl_pct, 2),
        }

        for s in signals:
            s["name"] = name_map.get(s["symbol"], "")

        return jsonify({
            "account": account_data,
            "positions": pos_list,
            "signals": signals,
            "signal_stats": engine.signal_stats(),
            "signal_total": total_signals,
            "strategies_count": len(engine.list_all(enabled_only=True)),
            "symbols_count": len(symbols.get_active_symbols()),
            "mode": config.symbols.mode,
        })

    # ==================================================================
    # API 路由 - 标的管理
    # ==================================================================

    @app.route("/api/instruments", methods=["GET"])
    @login_required
    def api_instruments():
        active = symbols.get_active_symbols()
        if not active:
            return jsonify({"list": [], "mode": config.symbols.mode})

        try:
            quotes = market.get_realtime_quotes(active)
            rows = []
            for _, r in quotes.iterrows():
                rows.append({
                    "symbol": str(r.get("symbol", "")),
                    "name": str(r.get("name", "")),
                    "price": float(r.get("price", 0) or 0),
                    "pct_change": float(r.get("pct_change", 0) or 0),
                    "amount": float(r.get("amount", 0) or 0),
                    "volume": float(r.get("volume", 0) or 0),
                    "sector": _get_sector(str(r.get("symbol", ""))),
                })
        except Exception:
            rows = [{"symbol": s, "name": s, "price": 0, "pct_change": 0, "amount": 0, "sector": ""} for s in active]

        return jsonify({"list": rows, "mode": config.symbols.mode})

    @app.route("/api/instruments/search", methods=["GET"])
    @login_required
    def api_instruments_search():
        q = request.args.get("q", "").strip()
        if len(q) < 1:
            return jsonify([])

        import requests
        results = []
        # 用 Sina 搜索接口
        try:
            import urllib.parse
            url = f"https://suggest3.sinajs.cn/suggest/type=11,12,13,14,15&key={urllib.parse.quote(q)}"
            headers = {"Referer": "https://finance.sina.com.cn"}
            resp = requests.get(url, headers=headers, timeout=5)
            resp.encoding = "gbk"
            text = resp.text
            # 解析 "var suggestvalue=\"...\""
            for line in text.split("\n"):
                if "suggestvalue" in line:
                    data = line.split('"')[1] if '"' in line else ""
                    for item in data.split(";"):
                        parts = item.split(",")
                        if len(parts) >= 5:
                            code = parts[2].split(".")[-1] if "." in parts[2] else parts[2]
                            results.append({
                                "symbol": code,
                                "name": parts[4],
                                "sector": parts[3] if len(parts) > 3 else "",
                            })
                    break
        except Exception:
            pass

        return jsonify(results[:15])

    @app.route("/api/instruments/mode", methods=["POST"])
    @login_required
    def api_instruments_mode():
        data = request.get_json()
        mode = data.get("mode", "specific")
        symbols.set_mode(mode)
        config.symbols.mode = mode
        return jsonify({"status": "ok", "mode": mode})

    @app.route("/api/instruments/add", methods=["POST"])
    @login_required
    def api_instruments_add():
        data = request.get_json()
        code = data.get("symbol", "").strip()
        if not code:
            return jsonify({"status": "error", "error": "代码不能为空"}), 400
        symbols.add_symbol(code)
        return jsonify({"status": "ok", "symbol": code})

    @app.route("/api/instruments/remove", methods=["POST"])
    @login_required
    def api_instruments_remove():
        data = request.get_json()
        code = data.get("symbol", "").strip()
        if not code:
            return jsonify({"status": "error", "error": "代码不能为空"}), 400
        symbols.remove_symbol(code)
        return jsonify({"status": "ok", "symbol": code})

    # ==================================================================
    # API 路由 - 策略管理
    # ==================================================================

    @app.route("/api/strategies", methods=["GET"])
    @login_required
    def api_strategies_list():
        records = engine.list_all()
        return jsonify([r.to_dict() for r in records])

    @app.route("/api/strategies/generate", methods=["POST"])
    @login_required
    def api_strategies_generate():
        """调用 LLM 生成策略代码（不保存）。"""
        if llm is None:
            return jsonify({"error": "LLM API Key 未配置，无法生成策略"}), 400
        data = request.get_json()
        description = data.get("description", "").strip()
        if not description:
            return jsonify({"error": "策略描述不能为空"}), 400
        if len(description) > 2000:
            return jsonify({"error": "策略描述过长（最多2000字符）"}), 400
        try:
            result = llm.generate(description)
            return jsonify({"code": result["code"], "desc": result.get("desc", ""), "status": "ok"})
        except Exception as e:
            logger.error(f"策略生成失败: {e}", exc_info=True)
            return jsonify({"error": str(e)}), 500

    @app.route("/api/strategies/save", methods=["POST"])
    @login_required
    def api_strategies_save():
        """保存策略代码（生成后确认 / 编辑后保存）。"""
        data = request.get_json()
        name = data.get("name", "未命名策略").strip()
        description = data.get("description", "").strip()
        code = data.get("code", "").strip()
        strategy_id = data.get("id", "").strip()

        if len(name) > 100:
            return jsonify({"error": "策略名称过长（最多100字符）"}), 400
        if len(description) > 2000:
            return jsonify({"error": "策略描述过长（最多2000字符）"}), 400
        if not code:
            return jsonify({"error": "代码不能为空"}), 400

        if strategy_id:
            # 更新已有策略
            existing = engine.get(strategy_id)
            if existing is None:
                return jsonify({"error": "策略不存在"}), 404
            engine.delete(strategy_id)

        try:
            record = engine.create(
                name=name,
                description=description,
                code=code,
                enabled=True,
            )
            return jsonify({"status": "ok", "id": record.id, "name": record.name})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/strategies/toggle", methods=["POST"])
    @login_required
    def api_strategies_toggle():
        """切换策略启用/禁用状态。"""
        data = request.get_json()
        strategy_id = data.get("id", "")
        if not strategy_id:
            return jsonify({"error": "策略ID不能为空"}), 400
        record = engine.get(strategy_id)
        if record is None:
            return jsonify({"error": "策略不存在"}), 404
        new_state = not record.enabled
        engine.set_enabled(strategy_id, new_state)
        return jsonify({"status": "ok", "enabled": new_state})

    @app.route("/api/strategies/<strategy_id>/code", methods=["GET"])
    @login_required
    def api_strategies_code(strategy_id):
        """获取策略源代码。"""
        record = engine.get(strategy_id)
        if record is None:
            return jsonify({"error": "策略不存在"}), 404
        return jsonify({"code": record.code, "name": record.name})

    @app.route("/api/strategies/<strategy_id>", methods=["DELETE"])
    @login_required
    def api_strategies_delete(strategy_id):
        engine.delete(strategy_id)
        return jsonify({"status": "ok"})

    @app.route("/api/strategies/templates", methods=["GET"])
    @login_required
    def api_strategies_templates():
        """获取策略模板库（含完整代码）。"""
        import yaml
        templates_path = Path(config.system.data_dir) / "strategy_templates" / "templates.yaml"
        if not templates_path.exists():
            return jsonify({"templates": []})

        try:
            raw = yaml.safe_load(templates_path.read_text(encoding="utf-8"))
            items = []
            for t in raw.get("templates", []):
                src = Path(t["source"])
                code = src.read_text(encoding="utf-8") if src.exists() else ""
                items.append({
                    "id": t["id"], "name": t["name"], "description": t["description"],
                    "category": t.get("category", ""), "difficulty": t.get("difficulty", ""),
                    "params": t.get("params", []), "code": code,
                })
            return jsonify({"templates": items})
        except Exception as e:
            logger.error(f"模板加载失败: {e}")
            return jsonify({"templates": [], "error": str(e)}), 500

    # ==================================================================
    # API 路由 - 交易记录
    # ==================================================================

    @app.route("/api/trades", methods=["GET"])
    @login_required
    def api_trades():
        symbol_filter = request.args.get("symbol", "").strip()
        date_from = request.args.get("date_from", "")
        date_to = request.args.get("date_to", "")
        limit = request.args.get("limit", 500, type=int)

        orders = broker.get_orders()
        result = []
        for o in reversed(orders):
            if symbol_filter and o.symbol != symbol_filter:
                continue
            if date_from and o.created_at[:10] < date_from:
                continue
            if date_to and o.created_at[:10] > date_to:
                continue
            result.append({
                "order_id": o.order_id,
                "symbol": o.symbol,
                "side": o.side.value,
                "price": o.price,
                "quantity": o.quantity,
                "status": o.status.value,
                "filled_price": o.filled_price,
                "filled_quantity": o.filled_quantity,
                "commission": o.commission,
                "created_at": o.created_at,
                "reason": o.reason,
            })
            if len(result) >= limit:
                break

        return jsonify(result)

    # ==================================================================
    # API 路由 - 系统状态
    # ==================================================================

    @app.route("/api/system/status", methods=["GET"])
    @login_required
    def api_system_status():
        loop = get_monitor_loop()
        today_notifications = 0
        if loop:
            today_str = datetime.now().strftime("%Y-%m-%d")
            today_notifications = sum(
                1 for n in loop.notification_history
                if n.get("timestamp", "").startswith(today_str)
            )

        return jsonify({
            "scheduler_running": loop is not None and loop.is_running,
            "scheduler_paused": loop is not None and loop.is_paused,
            "last_run": loop._last_run.isoformat() if (loop and loop._last_run) else None,
            "last_run_status": loop._last_run_status if loop else "未启动",
            "run_count": loop._run_count if loop else 0,
            "today_notifications": today_notifications,
            "config": {
                "interval": config.scheduler.interval_seconds,
                "idle_interval": config.scheduler.idle_interval_seconds,
                "mode": config.symbols.mode,
            },
        })

    @app.route("/api/system/logs", methods=["GET"])
    @login_required
    def api_system_logs():
        """从日志文件读取最新 50 行。"""
        log_file = Path(config.system.log_file)
        if not log_file.exists():
            return jsonify({"lines": ["日志文件不存在"]})
        try:
            with open(log_file, encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
                last_lines = lines[-50:] if len(lines) > 50 else lines
                return jsonify({"lines": [l.rstrip() for l in last_lines]})
        except Exception as e:
            return jsonify({"lines": [f"读取日志失败: {e}"]})

    @app.route("/api/system/control", methods=["POST"])
    @login_required
    def api_system_control():
        """控制调度器（暂停/恢复）。"""
        loop = get_monitor_loop()
        if loop is None:
            return jsonify({"status": "error", "error": "监测循环未启动"}), 400

        data = request.get_json()
        action = data.get("action", "")

        if action == "pause":
            loop.pause()
        elif action == "resume":
            loop.resume()
        else:
            return jsonify({"status": "error", "error": f"未知操作: {action}"}), 400

        return jsonify({"status": "ok", "action": action})

    # ==================================================================
    # API 路由 - 推送管理
    # ==================================================================

    @app.route("/api/push/settings", methods=["GET"])
    @login_required
    def api_push_settings_get():
        """获取当前推送配置。"""
        return jsonify(notify_mgr.get_full_config())

    @app.route("/api/push/settings", methods=["POST"])
    @login_required
    def api_push_settings_update():
        """更新推送配置并持久化到 config.yaml。"""
        data = request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "请求数据为空"}), 400
        result = notify_mgr.update_config(data)
        return jsonify(result)

    @app.route("/api/push/test", methods=["POST"])
    @login_required
    def api_push_test():
        """测试指定渠道。"""
        data = request.get_json()
        channel = data.get("channel", "")
        if not channel:
            return jsonify({"ok": False, "message": "渠道名不能为空"}), 400
        result = notify_mgr.test_channel(channel)
        return jsonify(result)

    @app.route("/api/push/history", methods=["GET"])
    @login_required
    def api_push_history():
        """获取推送历史（支持筛选和分页）。"""
        page = request.args.get("page", 1, type=int)
        page_size = request.args.get("page_size", 20, type=int)
        channel = request.args.get("channel", "").strip()
        level = request.args.get("level", "").strip()
        date_from = request.args.get("date_from", "").strip()
        date_to = request.args.get("date_to", "").strip()
        return jsonify(notify_mgr.get_history(
            page=page, page_size=page_size,
            channel=channel, level=level,
            date_from=date_from, date_to=date_to,
        ))

    @app.route("/api/push/history/clear", methods=["POST"])
    @login_required
    def api_push_history_clear():
        """清空推送历史。"""
        notify_mgr.clear_history()
        return jsonify({"status": "ok"})

    @app.route("/api/push/status", methods=["GET"])
    @login_required
    def api_push_status():
        """获取推送模块运行状态。"""
        return jsonify(notify_mgr.get_status())

    @app.route("/api/push/test-report", methods=["POST"])
    @login_required
    def api_push_test_report():
        """立即发送一封 LOF 日报测试邮件。"""
        from app.market.lof_data import LOFDataProvider
        from app.market.lof_report import build_report

        data = request.get_json() or {}
        recipient = data.get("recipient", config.lof.report.recipient)

        # 临时修改收件人配置
        orig = config.lof.report.recipient
        config.lof.report.recipient = recipient

        try:
            provider = LOFDataProvider(config.lof)
            df = provider.get_lof_premium()
            alerts = provider.check_alerts(df)
            html_body = build_report(df, len(alerts))
            title = f"[测试] LOF 溢价率日报 - {datetime.now().strftime('%Y-%m-%d %H:%M')}"
            ok = notify_mgr.send_report("email", title, html_body)
            return jsonify({
                "ok": ok,
                "message": "测试邮件已发送" if ok else "发送失败，请检查邮件配置",
                "total": len(df), "alerts": len(alerts),
            })
        except Exception as e:
            return jsonify({"ok": False, "message": str(e)}), 500
        finally:
            config.lof.report.recipient = orig

    # ==================================================================
    # API 路由 - LOF 溢价监测
    # ==================================================================

    @app.route("/api/lof/all", methods=["GET"])
    @login_required
    def api_lof_all():
        """一次返回列表 + 汇总（避免页面多次请求）。"""
        try:
            df = lof_provider.get_lof_premium()
            summary = lof_provider.get_summary()
            records = []
            for _, row in df.iterrows():
                rec = {}
                for col in df.columns:
                    val = row[col]
                    if pd.isna(val):
                        rec[col] = None
                    elif hasattr(val, "item"):
                        rec[col] = val.item()
                    else:
                        rec[col] = val
                records.append(rec)
            return jsonify({"list": records, "summary": summary})
        except Exception as e:
            logger.error(f"获取 LOF 数据失败: {e}")
            return jsonify({"error": str(e)}), 500

    @app.route("/api/lof/list", methods=["GET"])
    @login_required
    def api_lof_list():
        """获取全市场 LOF 溢价数据，支持过滤。"""
        try:
            df = lof_provider.get_lof_premium()
            if df.empty:
                return jsonify([])
            # 过滤参数
            min_amount = float(request.args.get("min_amount", 0) or 0)
            subscribe_only = request.args.get("subscribe_only", "0") == "1"
            if min_amount > 0 or subscribe_only:
                df = lof_provider.apply_filter(df, min_amount=min_amount, subscribe_only=subscribe_only)
            sort = request.args.get("sort", "premium_rate")
            if sort in df.columns:
                df = df.sort_values(sort, ascending=False, na_position="last")
            # 将 DataFrame 转为纯 Python 类型列表，避免 numpy 序列化问题
            records = []
            for _, row in df.iterrows():
                rec = {}
                for col in df.columns:
                    val = row[col]
                    if pd.isna(val):
                        rec[col] = None
                    elif hasattr(val, "item"):  # numpy scalar
                        rec[col] = val.item()
                    else:
                        rec[col] = val
                records.append(rec)
            return jsonify(records)
        except Exception as e:
            logger.error(f"获取 LOF 列表失败: {e}")
            return jsonify({"error": str(e)}), 500

    @app.route("/api/lof/alerts", methods=["GET"])
    @login_required
    def api_lof_alerts():
        """获取 LOF 预警记录。"""
        try:
            df = lof_provider.get_lof_premium()
            alerts = lof_provider.check_alerts(df)
            return jsonify(alerts)
        except Exception as e:
            logger.error(f"获取 LOF 预警失败: {e}")
            return jsonify({"error": str(e)}), 500

    @app.route("/api/lof/summary", methods=["GET"])
    @login_required
    def api_lof_summary():
        """获取 LOF 溢价汇总（Top 5）。"""
        try:
            return jsonify(lof_provider.get_summary())
        except Exception as e:
            logger.error(f"获取 LOF 汇总失败: {e}")
            return jsonify({"error": str(e)}), 500

    # ---- 兼容旧版 API（保留原有端点） ----

    @app.route("/api/status", methods=["GET"])
    def get_status():
        account = broker.get_account()
        return jsonify({
            "mode": config.symbols.mode,
            "symbols_count": len(symbols.get_active_symbols()),
            "strategies_count": len(engine.list_all(enabled_only=True)),
            "account": {
                "total_capital": account.total_capital,
                "available_cash": account.available_cash,
                "market_value": account.market_value,
                "total_pnl": account.total_pnl,
                "total_pnl_pct": account.total_pnl_pct,
            },
            "positions": [
                {
                    "symbol": p.symbol,
                    "shares": p.shares,
                    "avg_cost": round(p.avg_cost, 4),
                    "market_value": p.market_value,
                    "unrealized_pnl": p.unrealized_pnl,
                }
                for p in broker.get_positions().values()
            ],
        })

    @app.route("/api/signals", methods=["GET"])
    def get_signals():
        import sqlite3
        limit = request.args.get("limit", 50, type=int)
        db_path = engine._db_path
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM signal_log ORDER BY timestamp DESC LIMIT ?", (limit,)
        ).fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])

    @app.route("/api/positions", methods=["GET"])
    @login_required
    def api_positions():
        pos_list = positions.list_all()
        # 补充实时价格和盈亏
        syms = [p["symbol"] for p in pos_list]
        price_map = {}
        pct_map = {}
        if syms:
            try:
                quotes = market.get_realtime_quotes(syms)
                sym_col = "symbol" if "symbol" in quotes.columns else "代码"
                name_col = "name" if "name" in quotes.columns else "名称"
                price_col = "price" if "price" in quotes.columns else "最新价"
                pct_col = "pct_change" if "pct_change" in quotes.columns else "涨跌幅"
                name_map = {}
                for _, r in quotes.iterrows():
                    s = str(r.get(sym_col, ""))
                    if len(s) > 6:
                        s = s[2:]
                    name_map[s] = str(r.get(name_col, ""))
                    price_map[s] = float(r.get(price_col, 0) or 0)
                    pct_map[s] = float(r.get(pct_col, 0) or 0)
            except Exception:
                pass

        result = []
        for p in pos_list:
            sym = p["symbol"]
            price = price_map.get(sym, 0)
            pnl = (price - p["cost"]) * p["shares"] if price > 0 else 0
            pnl_pct = (price / p["cost"] - 1) * 100 if p["cost"] > 0 else 0
            result.append({**p, "price": price, "pnl": round(pnl, 2), "pnl_pct": round(pnl_pct, 2),
                           "name": name_map.get(sym, "")})
        return jsonify(result)

    @app.route("/api/positions", methods=["POST"])
    @login_required
    def api_positions_add():
        data = request.get_json()
        symbol = data.get("symbol", "").strip()
        shares = int(data.get("shares", 0))
        cost = float(data.get("cost", 0))
        date = data.get("date", "")
        if not symbol or shares <= 0 or cost <= 0:
            return jsonify({"status": "error", "message": "请完整填写代码、数量和成本"}), 400
        positions.add(symbol, shares, cost, date or None)
        return jsonify({"status": "ok"})

    @app.route("/api/positions/<symbol>", methods=["DELETE"])
    @login_required
    def api_positions_delete(symbol):
        ok = positions.remove(symbol)
        return jsonify({"status": "ok" if ok else "not_found"})

    @app.route("/api/backtest", methods=["POST"])
    @login_required
    def api_backtest_run():
        data = request.get_json()
        strategy_id = data.get("strategy_id", "")
        start_date = data.get("start_date", "")
        end_date = data.get("end_date", "")
        capital = float(data.get("capital", 100000))
        position_ratio = float(data.get("position_ratio", 0.1) or 0.1)

        if not strategy_id or not start_date or not end_date:
            return jsonify({"error": "请填写策略、起始日期和结束日期"}), 400

        active = _resolve_symbols(data, symbols, group_store)
        try:
            result = backtest_engine.run(
                strategy_id=strategy_id,
                symbols=active,
                start_date=start_date,
                end_date=end_date,
                initial_capital=capital,
                position_ratio=position_ratio,
            )
        except Exception as e:
            logger.error(f"回测异常: {e}", exc_info=True)
            return jsonify({"error": str(e)}), 500

        if "error" in result:
            return jsonify({"error": result["error"]}), 400

        return jsonify(result)

    @app.route("/api/backtest/combined", methods=["POST"])
    @login_required
    def api_backtest_combined():
        data = request.get_json()
        strategy_ids = data.get("strategy_ids", [])
        start_date = data.get("start_date", "")
        end_date = data.get("end_date", "")
        capital = float(data.get("capital", 100000))
        position_ratio = float(data.get("position_ratio", 0.1) or 0.1)

        if not strategy_ids or not start_date or not end_date:
            return jsonify({"error": "请选择至少一个策略并填写日期"}), 400

        active = _resolve_symbols(data, symbols, group_store)
        try:
            result = backtest_engine.run_combined(
                strategy_ids=strategy_ids,
                symbols=active,
                start_date=start_date,
                end_date=end_date,
                initial_capital=capital,
                position_ratio=position_ratio,
            )
        except Exception as e:
            logger.error(f"组合回测异常: {e}", exc_info=True)
            return jsonify({"error": str(e)}), 500

        if "error" in result:
            return jsonify({"error": result["error"]}), 400
        return jsonify(result)

    @app.route("/api/groups", methods=["GET"])
    @login_required
    def api_groups():
        default = list(DEFAULT_GROUPS.keys())
        custom = [{"name": n, "symbols": group_store.get_symbols(n),
                    "note": group_store.get_note(n)}
                  for n in group_store.list_names()]
        return jsonify({"default": default, "custom": custom})

    @app.route("/api/groups", methods=["POST"])
    @login_required
    def api_groups_save():
        data = request.get_json()
        name = data.get("name", "").strip()
        syms = data.get("symbols", [])
        note = data.get("note", "")
        if not name:
            return jsonify({"error": "名称不能为空"}), 400
        if name in DEFAULT_GROUPS:
            return jsonify({"error": f"'{name}' 是默认分组，不可覆盖"}), 400
        group_store.save(name, syms, note)
        return jsonify({"status": "ok"})

    @app.route("/api/groups/resolve", methods=["GET"])
    @login_required
    def api_groups_resolve():
        name = request.args.get("name", "全部")
        syms = _resolve_symbols({"group": name}, symbols, group_store)
        return jsonify({"symbols": syms})

    @app.route("/api/groups/<name>", methods=["DELETE"])
    @login_required
    def api_groups_delete(name):
        ok = group_store.delete(name)
        return jsonify({"status": "ok" if ok else "not_found"})

    @app.route("/api/backtest/compare", methods=["POST"])
    @login_required
    def api_backtest_compare():
        data = request.get_json()
        strategy_id = data.get("strategy_id", "")
        group_names = data.get("groups", [])
        start_date = data.get("start_date", "")
        end_date = data.get("end_date", "")
        capital = float(data.get("capital", 100000))
        position_ratio = float(data.get("position_ratio", 0.1) or 0.1)

        if not strategy_id or not group_names or not start_date or not end_date:
            return jsonify({"error": "请填写策略、选择分组并填写日期"}), 400

        results = []
        for gname in group_names:
            syms = _resolve_symbols({"group": gname}, symbols, group_store)
            if not syms:
                results.append({"group": gname, "error": "无有效标的"})
                continue
            try:
                r = backtest_engine.run(
                    strategy_id=strategy_id, symbols=syms,
                    start_date=start_date, end_date=end_date,
                    initial_capital=capital, skip_filter=True,
                    position_ratio=position_ratio,
                )
                m = r["metrics"]
                results.append({
                    "group": gname, "symbols_count": len(syms),
                    "total_return": m["total_return"],
                    "annual_return": m["annual_return"],
                    "max_drawdown": m["max_drawdown"],
                    "sharpe_ratio": m["sharpe_ratio"],
                    "win_rate": m["win_rate"],
                    "total_trades": m["total_trades"],
                    "final_value": m["final_value"],
                    "equity": r["equity"],
                })
            except Exception as e:
                results.append({"group": gname, "error": str(e)})

        return jsonify({"results": results})

    @app.route("/api/backtest/strategies", methods=["GET"])
    @login_required
    def api_backtest_strategies():
        records = engine.list_all(enabled_only=True)
        return jsonify([r.to_dict() for r in records])

    @app.route("/api/signals/full", methods=["GET"])
    @login_required
    def api_signals_full():
        """返回全部信号（按标的分组）。"""
        import sqlite3 as _sql
        db_path = engine._db_path
        if not db_path.exists():
            return jsonify({"held": [], "other": [], "total": 0})

        today = datetime.now().strftime("%Y-%m-%d")
        conn = _sql.connect(str(db_path))
        conn.row_factory = _sql.Row
        rows = conn.execute(
            "SELECT * FROM signal_log WHERE date(timestamp)=? ORDER BY timestamp DESC", (today,)
        ).fetchall()
        conn.close()

        held_syms = set(p["symbol"] for p in positions.list_all())

        # 从实时行情获取标的中文名
        all_signal_syms = list(set(r["symbol"] for r in rows))
        name_map: dict[str, str] = {}
        if all_signal_syms:
            try:
                qs = market.get_realtime_quotes(all_signal_syms)
                sym_c = "symbol" if "symbol" in qs.columns else "代码"
                name_c = "name" if "name" in qs.columns else "名称"
                for _, row_q in qs.iterrows():
                    s = str(row_q.get(sym_c, ""))
                    if len(s) > 6:
                        s = s[2:]
                    name_map[s] = str(row_q.get(name_c, ""))
            except Exception:
                pass

        held_groups: dict[str, list] = {}
        other_groups: dict[str, list] = {}

        for r in rows:
            d = dict(r)
            d["name"] = name_map.get(d["symbol"], d["symbol"])
            sym = d["symbol"]
            bucket = held_groups if sym in held_syms else other_groups
            bucket.setdefault(sym, []).append(d)

        def fmt_group(g):
            result = []
            for sym in sorted(g.keys(), key=lambda s: max(
                x["timestamp"] for x in g[s]), reverse=True):
                items = sorted(g[sym], key=lambda x: x["timestamp"], reverse=True)
                result.append({"symbol": sym, "name": items[0]["name"],
                               "count": len(items), "items": items})
            return result

        return jsonify({
            "held": fmt_group(held_groups),
            "other": fmt_group(other_groups),
            "total": len(rows),
        })

    @app.route("/api/account", methods=["GET"])
    def get_account():
        real_pos = positions.list_all()
        total_mv, total_cost, total_pnl = 0.0, 0.0, 0.0
        syms = [p["symbol"] for p in real_pos]
        if syms:
            try:
                quotes = market.get_realtime_quotes(syms)
                pmap = {}
                for _, r in quotes.iterrows():
                    s = str(r.get("symbol", ""))
                    if len(s) > 6:
                        s = s[2:]
                    pmap[s] = float(r.get("price", 0) or 0)
                for p in real_pos:
                    price = pmap.get(p["symbol"], 0)
                    mv = price * p["shares"] if price > 0 else 0
                    pnl = (price - p["cost"]) * p["shares"] if price > 0 else 0
                    total_mv += mv
                    total_cost += p["cost"] * p["shares"]
                    total_pnl += pnl
            except Exception:
                pass
        pnl_pct = (total_pnl / total_cost * 100) if total_cost > 0 else 0
        return jsonify({
            "total_capital": round(total_cost + total_pnl, 2),
            "available_cash": 0,
            "market_value": round(total_mv, 2),
            "total_pnl": round(total_pnl, 2),
            "total_pnl_pct": round(pnl_pct, 2),
        })

    return app


# ------------------------------------------------------------------
# 启动入口
# ------------------------------------------------------------------


def _resolve_symbols(data: dict, sym_mgr, gs: GroupStore) -> list[str]:
    """解析最终回测标的：优先用手动选择的 symbols，其次用分组。"""
    manual = data.get("symbols")
    if manual and isinstance(manual, list) and len(manual) > 0:
        all_syms = sym_mgr.get_active_symbols()
        return [s for s in manual if s in all_syms]
    group = data.get("group", "全部")
    all_syms = sym_mgr.get_active_symbols()
    if group in DEFAULT_GROUPS:
        return DEFAULT_GROUPS[group](all_syms)
    custom = gs.get_symbols(group)
    if custom:
        return [s for s in custom if s in all_syms]
    return list(all_syms)


def run_web(
    config_path: str | None = None,
    host: str | None = None,
    port: int | None = None,
    monitor_loop=None,
    debug: bool = False,
):
    """启动 Web 管理界面。

    Args:
        config_path: 配置文件路径。
        host: 监听地址。
        port: 监听端口。
        monitor_loop: MonitorLoop 实例（用于系统控制）。
        debug: 是否开启调试模式。
    """
    config = AppConfig(config_path)
    actual_host = host or config.web.host
    actual_port = port or config.web.port

    app = create_app(config, monitor_loop)

    logger.info(f"Web 管理控制台启动: http://{actual_host}:{actual_port}")
    logger.info(f"默认密码: {config.web.password}（请在 config.yaml 中修改）")

    app.run(host=actual_host, port=actual_port, debug=debug)

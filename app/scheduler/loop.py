"""
主控调度与事件驱动循环。

核心工作流：
  1. 定时拉取行情
  2. 遍历所有启用策略，传入数据获取信号
  3. 对 buy/sell 信号执行模拟交易
  4. 触发消息推送
  5. 记录日志与信号快照

支持单次运行和常驻守护进程模式。
"""

import logging
import signal
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.config import AppConfig
from app.market.data import MarketData
from app.notify.base import Notification, NotifyLevel
from app.notify.manager import NotificationManager
from app.strategy.engine import StrategyEngine
from app.symbols.manager import SymbolManager
from app.trade.broker import OrderSide
from app.trade.paper import PaperBroker
from app.trade.positions import PositionStore
from app.scheduler.summary import build_closing_summary

logger = logging.getLogger(__name__)

# 全局标志，支持优雅退出
_shutdown_flag = False


class MonitorLoop:
    """主监测循环。

    协调行情获取、策略执行、交易、推送的完整流程。

    Usage:
        loop = MonitorLoop(config)
        loop.run_once()     # 单次执行
        loop.run_daemon()   # 守护进程模式
    """

    def __init__(self, config: AppConfig):
        self._config = config

        # 初始化各模块
        self._market = MarketData(config.market)
        self._symbols = SymbolManager(config.symbols, self._market)
        self._engine = StrategyEngine(config)
        self._broker = PaperBroker(config)

        # 初始化推送管理器（统一管理多渠道）
        self._notify_mgr = NotificationManager(config)
        self._positions = PositionStore(str(
            Path(config.system.data_dir) / "positions.json"
        ))
        # 大盘择时缓存：每次 run_once 重新计算一次
        self._market_regime: dict | None = None

        # 调度器
        self._scheduler: Optional[BackgroundScheduler] = None

        # 统计
        self._run_count: int = 0
        self._last_run: Optional[datetime] = None
        self._last_run_status: str = "未运行"
        self._paused: bool = False

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    def run_once(self) -> dict:
        """执行一次完整的监测循环。

        Returns:
            执行摘要 dict。
        """
        start = time.time()
        active_symbols = self._symbols.get_active_symbols()
        strategies = self._engine.list_all(enabled_only=True)

        if not active_symbols:
            logger.warning("无活动标的")
            self._last_run_status = "无活动标的"
            self._last_run = datetime.now()
            return {"status": "no_symbols", "duration": 0}

        if not strategies:
            self._last_run_status = "无启用策略"
            self._last_run = datetime.now()
            return {"status": "no_strategies", "duration": 0}

        logger.info(f"开始监测: {len(active_symbols)} 标的, {len(strategies)} 策略")

        # Step 0: 预热 K 线缓存（首次从 API 拉取，后续从 SQLite 读）
        self._market.preload_kline_cache(active_symbols)

        # Step 1: 获取实时行情
        try:
            quotes = self._market.get_realtime_quotes(active_symbols)
        except Exception as e:
            logger.error(f"行情获取失败: {e}")
            return {"status": "market_error", "duration": time.time() - start}

        if quotes.empty:
            logger.warning("行情数据为空")
            return {"status": "empty_quotes", "duration": time.time() - start}

        # 构建 symbol -> price 映射
        price_map = _build_price_map(quotes)
        self._broker.update_market_prices(price_map)

        signals_generated = 0
        filtered_signals = 0
        trades_executed = 0

        # Step 2: 遍历策略 x 标的
        self._market_regime = None  # 每轮重新评估大盘
        context = self._broker.get_context()

        for strategy in strategies:
            symbols = active_symbols  # 全部标的不做策略级过滤

            for symbol in symbols:
                try:
                    hist_data = self._market.get_history(symbol, period="days", freq="daily")
                except Exception:
                    hist_data = pd.DataFrame()
                if hist_data.empty:
                    continue
                try:
                    result = self._engine.execute(strategy.id, context, hist_data)

                    if result["action"] == "hold":
                        continue

                    # 强度阈值过滤
                    if result["strength"] < self._config.scheduler.min_signal_strength:
                        continue

                    # 当天去重：同标的+同策略+同方向已报过则跳过
                    if self._engine.has_signal_today(strategy.id, symbol, result["action"]):
                        continue

                    # 大盘择时过滤（仅买入信号）
                    if result["action"] == "buy" and self._config.market_timing.enabled:
                        regime = self._check_market_regime()
                        if not regime["above_ma_bear"]:
                            filtered_signals += 1
                            reason = f"{result['reason']} [大盘择时拦截: MA{self._config.market_timing.ma_bear}熊市]"
                            if not self._engine.has_signal_today(strategy.id, symbol, "filtered"):
                                self._engine.log_signal(
                                    strategy_id=strategy.id, strategy_name=strategy.name,
                                    symbol=symbol, action="filtered",
                                    price=price_map.get(symbol, 0),
                                    reason=reason, strength=result["strength"],
                                )
                            continue
                        if not regime["above_ma_weak"]:
                            discount = self._config.market_timing.strength_discount
                            result["strength"] *= discount
                            result["reason"] += f" [弱市x{discount}]"
                            if result["strength"] < self._config.scheduler.min_signal_strength:
                                filtered_signals += 1
                                if not self._engine.has_signal_today(strategy.id, symbol, "filtered"):
                                    self._engine.log_signal(
                                        strategy_id=strategy.id, strategy_name=strategy.name,
                                        symbol=symbol, action="filtered",
                                        price=price_map.get(symbol, 0),
                                        reason=f"{result['reason']} [强度{result['strength']:.2f}<阈值]",
                                        strength=result["strength"],
                                    )
                                continue

                    signals_generated += 1
                    current_price = price_map.get(symbol, 0)

                    # 记录信号
                    self._engine.log_signal(
                        strategy_id=strategy.id,
                        strategy_name=strategy.name,
                        symbol=symbol,
                        action=result["action"],
                        price=current_price,
                        reason=result["reason"],
                        strength=result["strength"],
                    )

                    # Step 3: 执行交易
                    if result["action"] == "buy":
                        order = self._broker.submit_order(
                            symbol=symbol,
                            side=OrderSide.BUY,
                            price=current_price,
                            quantity=self._calc_buy_quantity(symbol, current_price),
                            reason=result["reason"],
                        )
                        if order.status.value == "filled":
                            trades_executed += 1
                            self._notify_signal(
                                strategy, symbol, result, order.filled_price,
                                order.filled_quantity, current_price,
                            )

                    elif result["action"] == "sell":
                        lot = self._calc_buy_quantity(symbol, 1)
                        # 优先查真实持仓，其次模拟持仓
                        real_pos = self._positions.get(symbol)
                        if real_pos:
                            qty = min(lot, real_pos["shares"])
                        else:
                            pos = self._broker.get_position(symbol)
                            qty = pos.shares if pos else 0
                            qty = min(lot, qty) if qty > 0 else 0
                        if qty > 0:
                            order = self._broker.submit_order(
                                symbol=symbol,
                                side=OrderSide.SELL,
                                price=current_price,
                                quantity=qty,
                                reason=result["reason"],
                            )
                            if order.status.value == "filled":
                                trades_executed += 1
                                self._notify_signal(
                                    strategy, symbol, result, order.filled_price,
                                    order.filled_quantity, current_price,
                                )
                        else:
                            # 无持仓也推信号
                            self._notify_signal(
                                strategy, symbol, result, current_price,
                                0, current_price, prefix="[自选] ",
                            )

                except Exception as e:
                    logger.error(f"处理 {strategy.name} x {symbol} 时出错: {e}", exc_info=True)

        elapsed = time.time() - start
        self._run_count += 1
        self._last_run = datetime.now()
        self._last_run_status = f"成功 (signals={signals_generated}, filtered={filtered_signals}, trades={trades_executed})"

        # 真实持仓汇总
        total_mv, total_cost, total_pnl = 0.0, 0.0, 0.0
        for p in self._positions.list_all():
            price = price_map.get(p["symbol"], 0)
            mv = price * p["shares"] if price > 0 else 0
            pnl = (price - p["cost"]) * p["shares"] if price > 0 else 0
            total_mv += mv
            total_cost += p["cost"] * p["shares"]
            total_pnl += pnl
        pnl_pct = (total_pnl / total_cost * 100) if total_cost > 0 else 0

        summary = {
            "status": "ok",
            "symbols": len(active_symbols),
            "strategies": len(strategies),
            "signals": signals_generated,
            "filtered": filtered_signals,
            "trades": trades_executed,
            "duration": round(elapsed, 2),
            "account": {
                "total": round(total_cost + total_pnl, 2),
                "cash": 0,
                "market_value": round(total_mv, 2),
                "pnl": round(total_pnl, 2),
                "pnl_pct": round(pnl_pct, 2),
            },
        }

        logger.info(f"监测完成: {summary}")
        return summary

    def run_daemon(self) -> None:
        """以守护进程模式持续运行。

        按配置的间隔定时执行监测循环。
        交易时段高频，非交易时段降频。
        """
        global _shutdown_flag

        try:
            signal.signal(signal.SIGINT, self._graceful_shutdown)
            signal.signal(signal.SIGTERM, self._graceful_shutdown)
        except ValueError:
            pass  # 非主线程调用时忽略（例如在守护线程中）

        self._scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
        self._scheduler.add_job(
            self._tick,
            IntervalTrigger(seconds=10),
            id="monitor_tick",
            name="监测心跳",
        )

        # LOF 日报定时推送（交易日 14:30，含预警）
        if self._config.lof.enabled and self._config.lof.report.enabled:
            schedule_str = self._config.lof.report.schedule  # "HH:MM"
            parts = schedule_str.split(":")
            hour, minute = int(parts[0]), int(parts[1])
            self._scheduler.add_job(
                self._send_lof_report,
                CronTrigger(day_of_week="mon-fri", hour=hour, minute=minute),
                id="lof_report",
                name="LOF 日报推送",
            )
            logger.info(f"LOF 日报已注册: 交易日 {schedule_str} → {self._config.lof.report.recipient}")

        # 收盘小结（交易日 15:00）
        self._scheduler.add_job(
            self._send_closing_summary,
            CronTrigger(day_of_week="mon-fri", hour=15, minute=1),
            id="closing_summary",
            name="收盘小结",
        )
        logger.info("收盘小结已注册: 交易日 15:01")

        self._scheduler.start()
        logger.info("守护进程模式已启动，按 Ctrl+C 退出")

        try:
            while not _shutdown_flag:
                time.sleep(1)
        except (KeyboardInterrupt, SystemExit):
            pass
        finally:
            self._scheduler.shutdown(wait=False)
            logger.info("系统已退出")

    # ------------------------------------------------------------------
    # 调度控制
    # ------------------------------------------------------------------

    def pause(self) -> None:
        """暂停监测调度。"""
        self._paused = True
        if self._scheduler:
            self._scheduler.pause()
        logger.info("监测调度已暂停")

    def resume(self) -> None:
        """恢复监测调度。"""
        self._paused = False
        if self._scheduler:
            self._scheduler.resume()
        logger.info("监测调度已恢复")

    @property
    def is_paused(self) -> bool:
        return self._paused

    @property
    def is_running(self) -> bool:
        return self._scheduler is not None and self._scheduler.running

    @property
    def notification_history(self) -> list[dict]:
        return self._notify_mgr._history if hasattr(self, '_notify_mgr') else []

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _check_market_regime(self) -> dict:
        """检查大盘环境。

        Returns:
            dict with keys: 'above_ma_bear' (是否在MA200上方),
            'above_ma_weak' (是否在MA60上方), 'price', 'ma60', 'ma200'.
        """
        if self._market_regime is not None:
            return self._market_regime

        mt = self._config.market_timing
        result = {"above_ma_bear": True, "above_ma_weak": True, "price": 0, "ma60": 0, "ma200": 0}
        try:
            hist = self._market.get_history(mt.benchmark, period="days", freq="daily")
            if hist.empty or "close" not in hist.columns or len(hist) < mt.ma_bear:
                logger.warning(f"基准 {mt.benchmark} K线数据不足，跳过大盘择时")
                self._market_regime = result
                return result
            close = hist["close"]
            price = close.iloc[-1]
            ma60 = close.rolling(mt.ma_weak).mean().iloc[-1]
            ma200 = close.rolling(mt.ma_bear).mean().iloc[-1]
            result = {
                "above_ma_bear": price > ma200,
                "above_ma_weak": price > ma60,
                "price": round(price, 2),
                "ma60": round(ma60, 2),
                "ma200": round(ma200, 2),
            }
            regime = "强势" if result["above_ma_bear"] else "熊市"
            logger.info(
                f"大盘择时: {mt.benchmark} @ {price:.2f} | "
                f"MA{mt.ma_weak}={ma60:.2f} MA{mt.ma_bear}={ma200:.2f} | {regime}"
            )
        except Exception as e:
            logger.error(f"大盘择时检查失败: {e}")

        self._market_regime = result
        return result

    def _send_closing_summary(self) -> None:
        """发送收盘小结，同时清理 90 天前旧数据。"""
        import sqlite3 as _sql

        # 清理旧信号（保留 90 天）
        try:
            db = self._engine._db_path
            if db.exists():
                conn = _sql.connect(str(db))
                cutoff = (datetime.now() - __import__('datetime').timedelta(days=90)).strftime("%Y-%m-%d")
                conn.execute("DELETE FROM signal_log WHERE date(timestamp) < ?", (cutoff,))
                conn.execute("DELETE FROM signal_review WHERE review_date < ?", (cutoff,))
                conn.commit(); conn.close()
        except Exception:
            pass

        try:
            active_symbols = self._symbols.get_active_symbols()
            quotes = self._market.get_realtime_quotes(active_symbols)

            # 查询今日信号
            signals_today = []
            try:
                db_path = self._engine._db_path
                conn = sqlite3.connect(str(db_path))
                conn.row_factory = sqlite3.Row
                today = datetime.now().strftime("%Y-%m-%d")
                rows = conn.execute(
                    "SELECT * FROM signal_log WHERE timestamp >= ? ORDER BY timestamp",
                    (today,),
                ).fetchall()
                conn.close()
                signals_today = [dict(r) for r in rows]
            except Exception:
                pass

            # 持仓 + 实时价格
            pos_with_pnl = []
            syms = [p["symbol"] for p in self._positions.list_all()]
            if syms:
                try:
                    pq = self._market.get_realtime_quotes(syms)
                    price_map = {}
                    for _, r in pq.iterrows():
                        s = str(r.get("symbol", ""))
                        if len(s) > 6:
                            s = s[2:]
                        price_map[s] = float(r.get("price", 0) or 0)
                    for p in self._positions.list_all():
                        price = price_map.get(p["symbol"], 0)
                        pnl = (price - p["cost"]) * p["shares"] if price > 0 else 0
                        pnl_pct = (price / p["cost"] - 1) * 100 if p["cost"] > 0 else 0
                        pos_with_pnl.append({**p, "price": price, "pnl": round(pnl, 2), "pnl_pct": round(pnl_pct, 2)})
                except Exception:
                    pos_with_pnl = self._positions.list_all()

            strategies = self._engine.list_all(enabled_only=True)

            # 信号回看：从 quotes 构建全部标的价格映射
            rev_price_map = {}
            for _, r in quotes.iterrows():
                s = str(r.get("symbol", ""))
                if len(s) > 6:
                    s = s[2:]
                rev_price_map[s] = float(r.get("price", 0) or 0)
            signal_reviews = self._engine.review_signals(rev_price_map)
            signal_stats = self._engine.signal_stats()

            # 辅助函数：从缓存获取 K线（不触发 API）
            def _cached_history(sym):
                return self._market.get_history(sym, period="days", freq="daily")

            body = build_closing_summary(
                quotes=quotes,
                active_symbols=active_symbols,
                signals_today=signals_today,
                positions_with_pnl=pos_with_pnl,
                strategies=strategies,
                get_history_fn=_cached_history,
                signal_reviews=signal_reviews,
                signal_stats=signal_stats,
            )

            # 通过通知渠道推送 (HTML)
            self._notify_mgr.send_report("email", "收盘小结", body)
            logger.info("收盘小结已发送")
        except Exception as e:
            logger.error(f"收盘小结生成失败: {e}", exc_info=True)

    def _send_lof_report(self) -> None:
        """发送 LOF 溢价率日报 + 可套利预警。"""
        from app.market.lof_data import LOFDataProvider
        from app.market.lof_report import build_report

        try:
            provider = LOFDataProvider(self._config.lof)
            df = provider.get_lof_premium()

            # 过滤无价格和低成交额
            if not df.empty:
                df = df[df["price"] > 0]
                df = df[df["amount"] >= 10000000]
            # 可套利筛选（仅正溢价，嵌入日报顶部高亮展示）
            trade_df = provider.apply_filter(df, min_amount=10000000, subscribe_only=True)
            all_alerts = provider.check_alerts(trade_df)
            alerts = [a for a in all_alerts if a.get("direction") == "premium"]

            # 日报（含套利预警）
            html_body = build_report(df, alerts, self._config.lof.premium_threshold)
            title = "LOF 日报"
            ok = self._notify_mgr.send_report("email", title, html_body)
            if ok:
                logger.info(f"LOF 日报已发送: {len(df)} 只, {len(alerts)} 条预警")
            else:
                logger.error("LOF 日报发送失败")
        except Exception as e:
            logger.error(f"LOF 日报发送异常: {e}", exc_info=True)

    def _tick(self) -> None:
        """调度器心跳：仅交易时段执行，非交易时段跳过。"""
        if _shutdown_flag:
            return

        now = datetime.now()
        # 周末跳过
        if now.weekday() >= 5:
            return

        current_minutes = now.hour * 60 + now.minute
        morning_end = 11 * 60 + 30
        afternoon_start = 13 * 60
        afternoon_end = 15 * 60

        # 仅交易时段执行
        in_trading = (9 * 60 + 30 <= current_minutes <= morning_end) or \
                     (afternoon_start <= current_minutes <= afternoon_end)
        if not in_trading:
            return

        interval = self._config.scheduler.interval_seconds
        if self._last_run is None or \
           (now - self._last_run).total_seconds() >= interval:
            try:
                self.run_once()
            except Exception as e:
                logger.error(f"监测循环异常: {e}", exc_info=True)

    def _calc_buy_quantity(self, symbol: str, price: float) -> int:
        """固定每笔手数：ETF 1000 份，股票 100 股。"""
        if price <= 0:
            return 0
        return 3000 if symbol.startswith(("5", "1", "58", "16")) else 300

    def _notify_signal(
        self,
        strategy,
        symbol: str,
        result: dict,
        fill_price: float,
        quantity: int,
        market_price: float,
        prefix: str = "",
    ) -> None:
        """发送交易信号通知。"""
        if not self._config.notification_enabled:
            return

        meta = self._symbols.get_meta(symbol)
        name = meta.get("name", symbol)
        action_cn = "买入" if result["action"] == "buy" else "卖出"

        title = f"{prefix}交易信号"
        action_color = "#dc2626" if result["action"] == "buy" else "#16a34a"
        suggestion = f"建议 {self._calc_buy_quantity(symbol, fill_price)} 份，买入后请在持仓管理记录" if result["action"] == "buy" else ""
        content = (
            f"<div style='font-family:system-ui,sans-serif;padding:8px 0'>"
            f"<p style='margin:0;font-size:16px;font-weight:700;color:{action_color}'>{action_cn}</p>"
            f"<p style='margin:6px 0 0;font-size:15px'>{symbol}[{name}]</p>"
            f"<p style='margin:2px 0;font-size:13px;color:#64748b'>策略: {strategy.name} · 当前价 {market_price:.4f} · {quantity} 份</p>"
            f"<p style='margin:4px 0;font-size:13px;color:#334155'>{result['reason']}</p>"
            f"{'<p style=\"margin:4px 0;font-size:12px;color:#94a3b8\">' + suggestion + '</p>' if suggestion else ''}"
            f"<p style='margin:0;font-size:11px;color:#94a3b8'>强度: {result['strength']:.1f}</p>"
            f"</div>"
        )

        level = NotifyLevel.WARNING if result["strength"] > 0.7 else NotifyLevel.INFO

        notification = Notification(title=title, content=content, level=level)

        # 通过 NotificationManager 发送（含频率控制和历史记录）
        self._notify_mgr.send_all(notification)

    @staticmethod
    def _graceful_shutdown(signum, frame):
        """优雅退出信号处理。"""
        global _shutdown_flag
        logger.info(f"收到信号 {signum}，正在优雅退出...")
        _shutdown_flag = True


def _build_price_map(quotes: pd.DataFrame) -> dict[str, float]:
    """从行情 DataFrame 构建 symbol -> price 映射。"""
    sym_col = "symbol" if "symbol" in quotes.columns else "代码"
    price_col = "price" if "price" in quotes.columns else "最新价"

    if sym_col not in quotes.columns or price_col not in quotes.columns:
        return {}

    symbols = quotes[sym_col].astype(str).str[-6:]
    prices = pd.to_numeric(quotes[price_col], errors="coerce")
    mask = prices.notna() & (symbols != "") & (symbols != "nan")
    return dict(zip(symbols[mask].tolist(), prices[mask].tolist()))

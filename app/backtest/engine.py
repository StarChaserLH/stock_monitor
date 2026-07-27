"""
回测引擎。

按交易日逐日推进，对历史数据执行策略并模拟交易，
输出权益曲线和绩效指标。
"""

import logging
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd

from app.backtest.metrics import compute_metrics

logger = logging.getLogger(__name__)


class BacktestBroker:
    """回测专用模拟账户。"""

    def __init__(self, initial_capital: float, etf_lot: int = 3000, stock_lot: int = 300):
        self.initial_capital = initial_capital
        self.cash = initial_capital
        self.positions: dict[str, dict] = {}
        self.trades: list[dict] = []
        self.etf_lot = etf_lot
        self.stock_lot = stock_lot

    def equity(self, price_map: dict[str, float]) -> float:
        mv = sum(self.positions[s]["shares"] * price_map.get(s, 0)
                 for s in self.positions)
        return self.cash + mv

    def buy(self, symbol: str, price: float, quantity: int, date: str) -> bool:
        cost = price * quantity + 5
        if cost > self.cash or quantity <= 0:
            return False
        self.cash -= cost
        pos = self.positions.get(symbol, {"shares": 0, "avg_cost": 0.0})
        total = pos["shares"] * pos["avg_cost"] + quantity * price
        pos["shares"] += quantity
        pos["avg_cost"] = total / pos["shares"] if pos["shares"] > 0 else price
        self.positions[symbol] = pos
        self.trades.append({"symbol": symbol, "action": "buy", "price": price,
                            "quantity": quantity, "date": date})
        return True

    def sell(self, symbol: str, price: float, quantity: int, date: str) -> bool:
        pos = self.positions.get(symbol)
        if not pos or pos["shares"] < quantity or quantity <= 0:
            return False
        revenue = price * quantity - 5
        self.cash += revenue
        pos["shares"] -= quantity
        if pos["shares"] <= 0:
            del self.positions[symbol]
        else:
            self.positions[symbol] = pos
        self.trades.append({"symbol": symbol, "action": "sell", "price": price,
                            "quantity": quantity, "date": date})
        return True

    def calc_quantity(self, symbol: str, price: float) -> int:
        """固定每笔手数：ETF 1000 份，股票 100 股。"""
        return self.etf_lot if symbol.startswith(("5", "1", "58", "16")) else self.stock_lot


class BacktestEngine:
    """逐日回测引擎。"""

    def __init__(self, config, strategy_engine, market_data):
        self._config = config
        self._engine = strategy_engine
        self._market = market_data
        self._market._config.cache_ttl = 99999

    def run(
        self,
        strategy_id: str,
        symbols: list[str],
        start_date: str,
        end_date: str,
        initial_capital: float = 100000.0,
        skip_filter: bool = False,
        etf_lot: int = 3000,
        stock_lot: int = 300,
    ) -> dict:
        """执行回测。"""
        # 估算需要的 K 线数量：交易日数 + 缓冲
        try:
            start_dt = datetime.strptime(start_date, "%Y-%m-%d")
            end_dt = datetime.strptime(end_date, "%Y-%m-%d")
            days_needed = max(250, int((end_dt - start_dt).days * 5 / 7) + 250)
        except ValueError:
            days_needed = 250
        self._force_kline_fetch(symbols, days_needed)

        broker = BacktestBroker(initial_capital, etf_lot=etf_lot, stock_lot=stock_lot)

        strategy = self._engine.get(strategy_id)
        if strategy is None:
            return {"error": f"策略不存在: {strategy_id}"}

        filter_func = self._engine.get_symbol_filter(strategy_id)

        # 大盘择时数据
        mt = self._config.market_timing
        benchmark_regime = {}
        if mt.enabled:
            benchmark_regime = self._compute_benchmark_regime(mt.benchmark)

        # 权益曲线从指定起始日开始
        equity_curve = [{"date": start_date, "value": initial_capital}]
        signals_today: set = set()  # 当日去重: (symbol, action)

        trading_dates = self._build_trading_calendar(symbols, start_date, end_date)
        logger.info(f"回测开始: {strategy.name} × {len(symbols)} 只, "
                    f"{start_date} → {end_date}, {len(trading_dates)} 个交易日")

        # 预加载并预索引 K 线：避免每天重复做 pandas 日期比较
        all_histories: dict[str, pd.DataFrame] = {}
        sym_close: dict[str, list] = {}
        sym_pos: dict[str, dict] = {}  # symbol -> {trading_day_index: row_position}
        for sym in symbols:
            h = self._market.get_history(sym, period="days", freq="daily")
            if h.empty or "close" not in h.columns:
                continue
            all_histories[sym] = h
            close_vals = h["close"].values
            sym_close[sym] = close_vals
            # 为每个交易日预计算该标的的数据位置
            pos_map = {}
            hi = 0
            for ti, tdt in enumerate(trading_dates):
                while hi < len(h) and h.index[hi] <= tdt:
                    hi += 1
                if hi > 0:
                    pos_map[ti] = hi
            sym_pos[sym] = pos_map

        for ti, dt in enumerate(trading_dates):
            date_str = dt.strftime("%Y-%m-%d")
            signals_today.clear()

            if not skip_filter and filter_func:
                meta = {s: {"name": s} for s in symbols}
                active = [s for s in filter_func(symbols, meta) if s in symbols]
            else:
                active = list(symbols)

            # 快速价格映射（O(1) 查数组）
            price_map = {}
            for sym in all_histories:
                p = sym_pos[sym].get(ti)
                if p is not None:
                    price_map[sym] = float(sym_close[sym][p - 1])

            context = {
                "cash": broker.cash,
                "positions": {s: p["shares"] for s, p in broker.positions.items()},
                "position_count": len(broker.positions),
            }

            for sym in active:
                if sym not in sym_pos:
                    continue
                p = sym_pos[sym].get(ti, 0)
                if p < 21:
                    continue
                data = all_histories[sym].iloc[:p]  # 整数索引，无日期比较

                try:
                    result = self._engine.execute(strategy.id, context, data)

                    if result["action"] == "hold":
                        continue
                    if result["strength"] < self._config.scheduler.min_signal_strength:
                        continue

                    # 当日去重
                    key = (sym, result["action"])
                    if key in signals_today:
                        continue
                    signals_today.add(key)

                    price = price_map.get(sym, 0)
                    if price <= 0:
                        continue

                    if result["action"] == "buy":
                        # 大盘择时过滤
                        if benchmark_regime:
                            regime = benchmark_regime.get(date_str, {})
                            if not regime.get("above_ma_bear", True):
                                continue  # MA200 下方不买
                            if not regime.get("above_ma_weak", True):
                                result["strength"] *= mt.strength_discount
                                if result["strength"] < self._config.scheduler.min_signal_strength:
                                    continue
                        qty = broker.calc_quantity(sym, price)
                        broker.buy(sym, price, qty, date_str)

                    elif result["action"] == "sell":
                        pos = broker.positions.get(sym)
                        if pos:
                            qty = broker.calc_quantity(sym, price)
                            broker.sell(sym, price, min(qty, pos["shares"]), date_str)

                except Exception as e:
                    logger.debug(f"回测策略异常 {sym} @ {date_str}: {e}")

            equity_curve.append({
                "date": date_str,
                "value": round(broker.equity(price_map), 2),
            })

        metrics = compute_metrics(equity_curve, broker.trades, initial_capital)
        benchmark_return = self._benchmark_return(symbols, start_date, end_date)
        metrics["benchmark_return"] = round(benchmark_return * 100, 2)
        metrics["excess_return"] = round(metrics["total_return"] - metrics["benchmark_return"], 2)

        logger.info(f"回测完成: 总收益={metrics['total_return']}%, "
                    f"最大回撤={metrics['max_drawdown']}%, "
                    f"夏普={metrics['sharpe_ratio']}, 交易={metrics['total_trades']}次")

        return {"metrics": metrics, "equity": equity_curve, "trades": broker.trades}

    def run_combined(
        self,
        strategy_ids: list[str],
        symbols: list[str],
        start_date: str,
        end_date: str,
        initial_capital: float = 100000.0,
        skip_filter: bool = False,
        etf_lot: int = 3000,
        stock_lot: int = 300,
    ) -> dict:
        """多策略组合回测，共享同一资金池，信号按强度优先级竞争资金。"""
        self._market.preload_kline_cache(symbols)
        broker = BacktestBroker(initial_capital, etf_lot=etf_lot, stock_lot=stock_lot)

        strategies = [s for s in (self._engine.get(sid) for sid in strategy_ids) if s]
        if not strategies:
            return {"error": "无有效策略"}

        mt = self._config.market_timing
        benchmark_regime = self._compute_benchmark_regime(mt.benchmark) if mt.enabled else {}

        equity_curve = [{"date": start_date, "value": initial_capital}]
        signals_today: set = set()
        trading_dates = self._build_trading_calendar(symbols, start_date, end_date)

        all_histories, sym_close, sym_pos = {}, {}, {}
        for sym in symbols:
            h = self._market.get_history(sym, period="days", freq="daily")
            if h.empty or "close" not in h.columns:
                continue
            all_histories[sym] = h
            close_vals = h["close"].values
            sym_close[sym] = close_vals
            pos_map = {}
            hi = 0
            for ti, tdt in enumerate(trading_dates):
                while hi < len(h) and h.index[hi] <= tdt:
                    hi += 1
                if hi > 0:
                    pos_map[ti] = hi
            sym_pos[sym] = pos_map

        logger.info(f"组合回测: {[s.name for s in strategies]}, {len(trading_dates)}天")

        for ti, dt in enumerate(trading_dates):
            date_str = dt.strftime("%Y-%m-%d")
            signals_today.clear()
            price_map = {}
            for sym in all_histories:
                p = sym_pos[sym].get(ti)
                if p is not None:
                    price_map[sym] = float(sym_close[sym][p - 1])

            context = {
                "cash": broker.cash,
                "positions": {s: p["shares"] for s, p in broker.positions.items()},
                "position_count": len(broker.positions),
            }

            pending = []
            for strategy in strategies:
                filter_func = None if skip_filter else self._engine.get_symbol_filter(strategy.id)
                if filter_func:
                    meta = {s: {"name": s} for s in symbols}
                    active = [s for s in filter_func(symbols, meta) if s in symbols]
                else:
                    active = list(symbols)
                for sym in active:
                    if sym not in sym_pos:
                        continue
                    p = sym_pos[sym].get(ti, 0)
                    if p < 21:
                        continue
                    data = all_histories[sym].iloc[:p]
                    try:
                        result = self._engine.execute(strategy.id, context, data)
                        if result["action"] == "hold":
                            continue
                        if result["strength"] < self._config.scheduler.min_signal_strength:
                            continue
                        price = price_map.get(sym, 0)
                        if price <= 0:
                            continue
                        pending.append((result["strength"], sym, result, price))
                    except Exception:
                        pass

            pending.sort(key=lambda x: x[0], reverse=True)
            for _, sym, result, price in pending:
                key = (sym, result["action"])
                if key in signals_today:
                    continue
                signals_today.add(key)
                if result["action"] == "buy":
                    if benchmark_regime:
                        regime = benchmark_regime.get(date_str, {})
                        if not regime.get("above_ma_bear", True):
                            continue
                        if not regime.get("above_ma_weak", True):
                            result["strength"] *= mt.strength_discount
                            if result["strength"] < self._config.scheduler.min_signal_strength:
                                continue
                    qty = broker.calc_quantity(sym, price)
                    broker.buy(sym, price, qty, date_str)
                elif result["action"] == "sell":
                    pos = broker.positions.get(sym)
                    if pos:
                        qty = broker.calc_quantity(sym, price)
                        broker.sell(sym, price, min(qty, pos["shares"]), date_str)

            equity_curve.append({"date": date_str, "value": round(broker.equity(price_map), 2)})

        metrics = compute_metrics(equity_curve, broker.trades, initial_capital)
        bm = self._benchmark_return(symbols, start_date, end_date)
        metrics["benchmark_return"] = round(bm * 100, 2)
        metrics["excess_return"] = round(metrics["total_return"] - metrics["benchmark_return"], 2)
        return {"metrics": metrics, "equity": equity_curve, "trades": broker.trades}

    def _force_kline_fetch(self, symbols: list[str], datalen: int) -> None:
        """若缓存已满足长度需求则跳过，否则重新拉取 K 线。"""
        import sqlite3 as _sql, requests as _r

        # 检查是否已有足够长度的数据
        sample = self._market.get_history(symbols[0], period="days", freq="daily")
        if not sample.empty and len(sample) >= datalen * 0.9:
            logger.info(f"K线缓存已满足 ({len(sample)}条 >= {int(datalen*0.9)})，跳过拉取")
            return

        logger.info(f"K线缓存不足 ({len(sample)} < {int(datalen*0.9)})，拉取 {len(symbols)} 只")
        # 1) 清除内存缓存
        for sym in symbols:
            self._market._cache.pop(f"hist_{sym}_days_daily", None)
        # 2) 清除 SQLite 中的旧 K 线
        try:
            db = self._market._kline_db
            if db.exists():
                conn = _sql.connect(str(db))
                for sym in symbols:
                    conn.execute("DELETE FROM kline_daily WHERE symbol=?", (sym,))
                conn.commit()
                conn.close()
        except Exception:
            pass
        # 3) 临时覆盖 datalen 参数
        orig_fetch = self._market._fetch_history

        def _fetch_with_datalen(symbol, period, freq):
            prefix = "sh" if str(symbol).startswith(("5", "6", "9")) else "sz"
            url = "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData"
            params = {"symbol": f"{prefix}{symbol}", "scale": "240",
                      "ma": "5,10,20", "datalen": str(datalen)}
            resp = _r.get(url, params=params,
                          headers={"Referer": "https://finance.sina.com.cn"},
                          timeout=self._market._config.timeout)
            data = resp.json()
            if not data:
                raise Exception(f"历史数据为空: {symbol}")
            import pandas as _pd
            df = _pd.DataFrame(data)
            df = df.rename(columns={"day": "date", "open": "open", "high": "high",
                                    "low": "low", "close": "close", "volume": "volume"})
            for col in ["open", "high", "low", "close", "volume"]:
                if col in df.columns:
                    df[col] = _pd.to_numeric(df[col], errors="coerce")
            df["date"] = _pd.to_datetime(df["date"])
            df = df.set_index("date").sort_index()
            return df

        self._market._fetch_history = _fetch_with_datalen
        try:
            self._market.preload_kline_cache(symbols)
        finally:
            self._market._fetch_history = orig_fetch

    def _compute_benchmark_regime(self, benchmark: str) -> dict[str, dict]:
        """预计算基准指数每日的大盘环境。

        Returns:
            {date_str: {"above_ma_bear": bool, "above_ma_weak": bool}, ...}
        """
        mt = self._config.market_timing
        result = {}
        hist = self._market.get_history(benchmark, period="days", freq="daily")
        if hist.empty or "close" not in hist.columns:
            return result
        close = hist["close"]
        ma_weak = close.rolling(mt.ma_weak).mean()
        ma_bear = close.rolling(mt.ma_bear).mean()
        for d in hist.index:
            if hasattr(d, "to_pydatetime"):
                d = d.to_pydatetime()
            if d is None:
                continue
            date_str = d.strftime("%Y-%m-%d")
            if pd.isna(ma_weak[d]) or pd.isna(ma_bear[d]):
                result[date_str] = {"above_ma_bear": True, "above_ma_weak": True}
            else:
                p = float(close[d])
                result[date_str] = {
                    "above_ma_bear": p > float(ma_bear[d]),
                    "above_ma_weak": p > float(ma_weak[d]),
                }
        return result

    def _build_trading_calendar(
        self, symbols: list[str], start: str, end: str
    ) -> list[datetime]:
        """以第一只标的的 K 线日期为交易日历基准。A 股所有标的共享同一交易日历。"""
        dates_set = set()
        start_dt = datetime.strptime(start, "%Y-%m-%d")
        end_dt = datetime.strptime(end, "%Y-%m-%d")

        hist = self._market.get_history(symbols[0], period="days", freq="daily")
        if not hist.empty:
            for d in hist.index:
                if hasattr(d, "to_pydatetime"):
                    d = d.to_pydatetime()
                if d is not None and start_dt <= d <= end_dt:
                    dates_set.add(d)

        if not dates_set:
            d = start_dt
            while d <= end_dt:
                if d.weekday() < 5:
                    dates_set.add(d)
                d += timedelta(days=1)

        return sorted(dates_set)

    def _benchmark_return(
        self, symbols: list[str], start: str, end: str
    ) -> float:
        returns = []
        for sym in symbols:
            hist = self._market.get_history(sym, period="days", freq="daily")
            if hist.empty or "close" not in hist.columns:
                continue
            close = hist["close"]
            start_slice = close[close.index <= start]
            end_slice = close[close.index <= end]
            if start_slice.empty or end_slice.empty:
                continue
            r = float(end_slice.iloc[-1] / start_slice.iloc[-1] - 1)
            returns.append(r)
        return sum(returns) / len(returns) if returns else 0

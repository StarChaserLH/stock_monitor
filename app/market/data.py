"""
行情数据获取模块。

封装 akshare 作为主数据源，提供统一的行情接口。
支持：实时行情、历史K线、ETF列表、全市场股票列表。
"""

import logging
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

logger = logging.getLogger(__name__)


class MarketDataError(Exception):
    """行情数据异常基类。"""


class MarketData:
    """A股行情数据获取器。

    使用 akshare 作为数据源，封装了限流、重试和缓存逻辑。

    Usage:
        md = MarketData(config)
        quotes = md.get_realtime_quotes(["510050", "159915"])
        history = md.get_history("510050", period="days", freq="daily")
    """

    def __init__(self, config):
        """
        Args:
            config: MarketConfig 实例。
        """
        self._config = config
        self._last_request_time: float = 0.0
        self._cache: dict[str, tuple[float, pd.DataFrame]] = {}
        self._max_cache_size: int = 128
        # SQLite K线本地存储
        self._kline_db = Path("data/kline.db")
        self._kline_db.parent.mkdir(parents=True, exist_ok=True)
        self._init_kline_db()

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    def get_realtime_quotes(self, symbols: list[str]) -> pd.DataFrame:
        """获取指定标的的实时行情。

        Args:
            symbols: 证券代码列表，如 ['510050', '159915']。

        Returns:
            DataFrame，包含代码、名称、最新价、涨跌幅、成交量、成交额等字段。
        """
        self._rate_limit()
        try:
            df = self._fetch_realtime(symbols)
            return self._normalize_realtime(df)
        except Exception as e:
            logger.error(f"获取实时行情失败: {e}")
            raise MarketDataError(f"实时行情获取失败: {e}") from e

    def get_history(
        self,
        symbol: str,
        period: str = "days",
        freq: str = "daily",
    ) -> pd.DataFrame:
        """获取历史K线数据。

        Args:
            symbol: 证券代码，如 '510050'。
            period: 时间跨度，'days'(日线) / 'weeks' / 'months'。
            freq: 频率，'daily' / 'weekly' / 'monthly' / '5min' / '15min' / '30min' / '60min'。

        Returns:
            DataFrame，包含 OHLCV 及日期索引。
        """
        cache_key = f"hist_{symbol}_{period}_{freq}"
        if cache_key in self._cache:
            ts, df = self._cache[cache_key]
            if time.time() - ts < self._config.cache_ttl:
                return df.copy()

        # 优先从本地 SQLite 读取（检查新鲜度）
        df = self._read_kline_from_db(symbol)
        if not df.empty:
            latest = df.index[-1]
            age_days = (datetime.now() - latest).days if hasattr(latest, 'date') else 99
            if age_days <= 2:  # 2 天内数据视为有效
                self._cache[cache_key] = (time.time(), df)
                return df

        try:
            df = self._fetch_history(symbol, period, freq)
            self._write_kline_to_db(symbol, df)
            self._cache[cache_key] = (time.time(), df)
            if len(self._cache) > self._max_cache_size:
                oldest_key = min(self._cache, key=lambda k: self._cache[k][0])
                del self._cache[oldest_key]
            return df
        except Exception as e:
            logger.error(f"获取历史数据失败 {symbol}: {e}")
            raise MarketDataError(f"历史数据获取失败 {symbol}: {e}") from e

    def get_all_etfs(self) -> pd.DataFrame:
        """获取全市场 ETF 列表。

        Returns:
            DataFrame，包含基金代码、简称、类型、最新净值等。
        """
        self._rate_limit()
        try:
            import akshare as ak
            df = ak.fund_etf_category_sina(symbol="ETF基金")
            return df
        except Exception as e:
            logger.error(f"获取ETF列表失败: {e}")
            raise MarketDataError(f"ETF列表获取失败: {e}") from e

    def get_all_lofs(self) -> pd.DataFrame:
        """获取全市场 LOF 列表。"""
        self._rate_limit()
        try:
            import akshare as ak
            df = ak.fund_lof_category_sina(symbol="LOF基金")
            return df
        except Exception as e:
            logger.error(f"获取LOF列表失败: {e}")
            raise MarketDataError(f"LOF列表获取失败: {e}") from e

    def get_stock_list(self) -> pd.DataFrame:
        """获取全市场 A 股股票列表。"""
        self._rate_limit()
        try:
            import akshare as ak
            df = ak.stock_info_a_code_name()
            return df
        except Exception as e:
            logger.error(f"获取股票列表失败: {e}")
            raise MarketDataError(f"股票列表获取失败: {e}") from e

    def get_minute_kline(self, symbol: str, freq: str = "1") -> pd.DataFrame:
        """获取分钟级K线。

        Args:
            symbol: 证券代码。
            freq: 分钟周期，'1', '5', '15', '30', '60'。
        """
        self._rate_limit()
        try:
            import akshare as ak
            df = ak.stock_zh_a_hist_min_em(
                symbol=symbol,
                period=freq,
                adjust="qfq",
            )
            return df
        except Exception as e:
            logger.error(f"获取分钟K线失败 {symbol}: {e}")
            raise MarketDataError(f"分钟K线获取失败 {symbol}: {e}") from e

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------

    def _rate_limit(self) -> None:
        """请求频率控制，确保调用间隔不小于配置值。"""
        elapsed = time.time() - self._last_request_time
        if elapsed < self._config.request_interval:
            time.sleep(self._config.request_interval - elapsed)
        self._last_request_time = time.time()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((ConnectionError, TimeoutError, OSError)),
        reraise=True,
    )
    def _fetch_realtime(self, symbols: list[str]) -> pd.DataFrame:
        """通过 Sina 实时行情 API 获取数据。

        支持 A 股股票、ETF、LOF 等任意标的。
        """
        import requests

        # 加 sh/sz 前缀
        prefixed = []
        for s in symbols:
            s = str(s)
            prefix = "sh" if s.startswith(("5", "6", "9")) else "sz"
            prefixed.append(f"{prefix}{s}")

        # 分批（Sina 单次最多 ~800 个）
        batch_size = 100
        rows = []
        for i in range(0, len(prefixed), batch_size):
            batch = prefixed[i:i + batch_size]
            url = "https://hq.sinajs.cn/list=" + ",".join(batch)
            headers = {"Referer": "https://finance.sina.com.cn"}
            resp = requests.get(url, headers=headers, timeout=self._config.timeout)
            resp.encoding = "gbk"
            rows.extend(self._parse_sina_response(resp.text, batch))

        if not rows:
            raise MarketDataError("获取实时行情返回空数据")
        return pd.DataFrame(rows)

    @staticmethod
    def _parse_sina_response(text: str, codes: list[str]) -> list[dict]:
        """解析 Sina 实时行情响应。

        格式: var hq_str_sh510050="名称,今开,昨收,最新价,最高,最低,..."
        字段索引: 0=名称,1=今开,2=昨收,3=最新价,4=最高,5=最低,
                  8=成交量(手),9=成交额(万元), 32=涨跌幅
        """
        import re
        # 一次正则扫描提取全部 hq_str_XXX="..." 条目
        pattern = re.compile(r'hq_str_(\w+)="([^"]*)"')
        parsed = {m.group(1): m.group(2) for m in pattern.finditer(text)}

        rows = []
        for code in codes:
            data_str = parsed.get(code)
            if not data_str:
                continue
            data = data_str.split(",")
            if len(data) < 32:
                continue
            try:
                pct_idx = 32
                rows.append({
                    "symbol": code[2:],  # sh510050 → 510050
                    "name": data[0],
                    "price": float(data[3]),
                    "open": float(data[1]),
                    "pre_close": float(data[2]),
                    "high": float(data[4]),
                    "low": float(data[5]),
                    "volume": float(data[8]) if data[8] else 0,
                    "amount": float(data[9]) if data[9] else 0,
                    "pct_change": round((float(data[3]) / float(data[2]) - 1) * 100, 2),
                    "change": round(float(data[3]) - float(data[2]), 4),
                })
            except (ValueError, IndexError):
                continue
        return rows

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((ConnectionError, TimeoutError, OSError)),
        reraise=True,
    )
    def _fetch_history(
        self, symbol: str, period: str, freq: str
    ) -> pd.DataFrame:
        """通过 Sina K 线 API 获取历史数据。"""
        import requests

        prefix = "sh" if str(symbol).startswith(("5", "6", "9")) else "sz"
        code = f"{prefix}{symbol}"

        url = "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData"
        params = {"symbol": code, "scale": "240", "ma": "5,10,20", "datalen": "250"}
        headers = {"Referer": "https://finance.sina.com.cn"}
        resp = requests.get(url, params=params, headers=headers,
                          timeout=self._config.timeout)
        data = resp.json()
        if not data:
            raise MarketDataError(f"历史数据为空: {symbol}")

        df = pd.DataFrame(data)
        df = df.rename(columns={
            "day": "date", "open": "open", "high": "high",
            "low": "low", "close": "close", "volume": "volume",
        })
        for col in ["open", "high", "low", "close", "volume"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date").sort_index()
        return df

    def _normalize_realtime(self, df: pd.DataFrame) -> pd.DataFrame:
        """标准化实时行情 DataFrame 的列名和格式。

        Sina 数据已在 _parse_sina_response 中标准化，此方法处理兼容。
        """
        if df.empty:
            return df
        # 如果有中文列名（兼容旧 eastmoney 格式），做映射
        if "代码" in df.columns:
            col_map = {
                "代码": "symbol", "名称": "name", "最新价": "price",
                "涨跌幅": "pct_change", "涨跌额": "change",
                "成交量": "volume", "成交额": "amount",
                "最高": "high", "最低": "low", "今开": "open", "昨收": "pre_close",
            }
            df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})
        return df

    # ------------------------------------------------------------------
    # SQLite K线本地存储
    # ------------------------------------------------------------------

    def _init_kline_db(self) -> None:
        with sqlite3.connect(str(self._kline_db)) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS kline_daily (
                    symbol TEXT, date TEXT,
                    open REAL, high REAL, low REAL, close REAL, volume REAL,
                    ma5 REAL, ma10 REAL, ma20 REAL,
                    PRIMARY KEY (symbol, date)
                )
            """)
            conn.commit()

    def _read_kline_from_db(self, symbol: str) -> pd.DataFrame:
        try:
            with sqlite3.connect(str(self._kline_db)) as conn:
                df = pd.read_sql_query(
                    "SELECT * FROM kline_daily WHERE symbol=? ORDER BY date",
                    conn, params=(symbol,))
                if df.empty:
                    return pd.DataFrame()
                df["date"] = pd.to_datetime(df["date"])
                df = df.set_index("date").sort_index()
                df = df.drop(columns=["symbol"], errors="ignore")
                return df
        except Exception:
            return pd.DataFrame()

    def _write_kline_to_db(self, symbol: str, df: pd.DataFrame) -> None:
        if df is None or df.empty:
            return
        try:
            with sqlite3.connect(str(self._kline_db)) as conn:
                rows = []
                for idx, row in df.iterrows():
                    d = str(idx.date()) if hasattr(idx, 'date') else str(idx)[:10]
                    rows.append((
                        symbol, d,
                        row.get("open"), row.get("high"), row.get("low"),
                        row.get("close"), row.get("volume"),
                        row.get("ma_price5"), row.get("ma_price10"),
                        row.get("ma_price20"),
                    ))
                conn.executemany(
                    "INSERT OR REPLACE INTO kline_daily VALUES (?,?,?,?,?,?,?,?,?,?)",
                    rows,
                )
                conn.commit()
        except Exception as e:
            logger.error(f"写入K线DB失败 {symbol}: {e}")

    def preload_kline_cache(self, symbols: list[str]) -> None:
        """预热 K 线缓存：对未缓存的标的从 API 拉取并写入 SQLite。"""
        to_fetch = []
        for s in symbols:
            df = self._read_kline_from_db(s)
            if df.empty:
                to_fetch.append(s)

        if not to_fetch:
            logger.info(f"K线缓存已就绪 ({len(symbols)} 只)")
            return

        logger.info(f"预热 K线缓存: {len(to_fetch)} 只需要拉取")
        from concurrent.futures import ThreadPoolExecutor, as_completed
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = {pool.submit(self._fetch_history, s, "days", "daily"): s for s in to_fetch}
            for f in as_completed(futures):
                sym = futures[f]
                try:
                    df = f.result()
                    self._write_kline_to_db(sym, df)
                except Exception as e:
                    logger.error(f"预热失败 {sym}: {e}")
        logger.info(f"K线缓存预热完成")

    @staticmethod
    def is_trading_time() -> bool:
        """判断当前是否为 A 股交易时间。

        交易时间：周一至周五 9:30-11:30, 13:00-15:00。
        """
        now = datetime.now()
        if now.weekday() >= 5:  # 周六日
            return False

        current_minutes = now.hour * 60 + now.minute
        morning_start = 9 * 60 + 30
        morning_end = 11 * 60 + 30
        afternoon_start = 13 * 60
        afternoon_end = 15 * 60

        return (morning_start <= current_minutes <= morning_end) or \
               (afternoon_start <= current_minutes <= afternoon_end)

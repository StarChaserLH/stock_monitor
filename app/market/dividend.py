"""
股息率数据提供器。

- 个股：stock_dividend_cninfo → 最近一次每股分红
- ETF 基准：510880 红利ETF 累积分红 → 年度分红

缓存：分红数据 TTL 24h（除权日后才变），ETF 基准 TTL 2h。
"""

import logging
import time
from datetime import datetime

import pandas as pd

logger = logging.getLogger(__name__)


class DividendProvider:
    """股息率底层数据提供器。

    只负责拉取原始分红数据，股息率 = 分红 / 股价 由外部计算。

    Usage:
        provider = DividendProvider()
        div_per_share = provider.get_stock_dividend("601318")  # 每股分红
        benchmark_yield = provider.get_etf_benchmark_yield()   # ETF 基准股息率
    """

    _ETF_BENCHMARK = "510880"

    def __init__(self):
        self._stock_cache: dict[str, tuple[float, float]] = {}     # symbol → (ts, div_per_share)
        self._benchmark_cache: tuple[float, float] | None = None   # (ts, div_yield_pct)

    # ------------------------------------------------------------------
    # 个股分红
    # ------------------------------------------------------------------

    def get_stock_dividend(self, symbol: str) -> float:
        """获取个股最近一次每股分红（元），无数据返回 0。"""
        today = datetime.now().strftime("%Y%m%d")

        if symbol in self._stock_cache:
            ts, div = self._stock_cache[symbol]
            if time.time() - ts < 86400:  # TTL 24h
                return div

        import akshare as ak
        try:
            df = ak.stock_dividend_cninfo(symbol=symbol)
            if df is None or df.empty:
                return 0.0

            div_col = df.columns[4]   # 派息比例 (每10股)
            date_col = df.columns[6]  # 除权日

            valid = df[df[div_col].notna() & (df[div_col] > 0)].copy()
            if valid.empty:
                return 0.0

            valid[date_col] = pd.to_datetime(valid[date_col], errors="coerce")
            valid = valid.dropna(subset=[date_col])
            if valid.empty:
                return 0.0

            latest = valid.sort_values(date_col, ascending=False).iloc[0]
            div_per_10 = float(latest[div_col])
            div_per_share = div_per_10 / 10

            self._stock_cache[symbol] = (time.time(), div_per_share)
            return div_per_share

        except Exception as e:
            logger.error(f"个股分红获取失败 {symbol}: {e}")
            return 0.0

    # ------------------------------------------------------------------
    # ETF 基准：510880 红利ETF 股息率
    # ------------------------------------------------------------------

    def get_etf_benchmark_yield(self) -> float:
        """获取 ETF 基准股息率（510880 红利ETF）。TTL 2h，失败时兜底返回上证A股股息率。

        股息率 = (最新累计 - 前次累计) / 510880当前价 * 100
        """
        if self._benchmark_cache is not None:
            ts, val = self._benchmark_cache
            if time.time() - ts < 7200:  # TTL 2h
                return val

        import akshare as ak

        # 1. 累积分红
        try:
            div_df = ak.fund_etf_dividend_sina(symbol=f"sh{self._ETF_BENCHMARK}")
            if div_df is None or div_df.empty or len(div_df) < 2:
                return self._fallback()

            total = float(div_df.iloc[-1].iloc[1])
            prev = float(div_df.iloc[-2].iloc[1])
            annual_div = total - prev
            if annual_div <= 0:
                return self._fallback()
        except Exception as e:
            logger.error(f"510880 分红数据获取失败: {e}")
            return self._fallback()

        # 2. 当前价
        try:
            import requests
            url = f"https://hq.sinajs.cn/list=sh{self._ETF_BENCHMARK}"
            resp = requests.get(url, headers={"Referer": "https://finance.sina.com.cn"}, timeout=8)
            resp.encoding = "gbk"
            import re
            m = re.search(r'hq_str_\w+="([^"]*)"', resp.text)
            if not m:
                return self._fallback()
            price = float(m.group(1).split(",")[3])
            if price <= 0:
                return self._fallback()
        except Exception as e:
            logger.error(f"510880 价格获取失败: {e}")
            return self._fallback()

        div_yield = round(annual_div / price * 100, 2)
        self._benchmark_cache = (time.time(), div_yield)
        logger.info(f"ETF dividend benchmark (510880): price={price:.3f} annual_div={annual_div:.4f} yield={div_yield:.2f}%")
        return div_yield

    @staticmethod
    def _fallback() -> float:
        """兜底：上证A股市场整体股息率。"""
        try:
            import akshare as ak
            df = ak.stock_a_gxl_lg(symbol="上证A股")
            if df is not None and not df.empty:
                return float(df.iloc[-1].iloc[1])
        except Exception:
            pass
        return 2.5

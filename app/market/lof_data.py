"""
LOF 基金溢价率数据模块。

溢价率 = (实时市价 / 最新净值 - 1) * 100%

数据来源：
  - 实时市价：新浪 fund_etf_category_sina (LOF基金)
  - 净值+申购状态：eastmoney fund_purchase_em（全市场，含申购状态/限额）
两路数据按基金代码合并。
"""

import logging
import time
from datetime import datetime
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)


class LOFDataError(Exception):
    """LOF 数据异常。"""


class LOFDataProvider:
    """LOF 基金数据提供器。

    双数据源合并：
      - 新浪：实时市价、涨跌幅
      - eastmoney fund_purchase_em：最新净值、申购状态、赎回状态、申购限额

    Usage:
        provider = LOFDataProvider(config)
        df = provider.get_lof_premium()
        alerts = provider.check_alerts(df)
    """

    # 新浪列名 → 统一列名
    _SINA_MAP = {
        "代码": "symbol", "名称": "name", "最新价": "price",
        "涨跌幅": "pct_change", "涨跌额": "change",
        "今开": "open", "昨收": "pre_close",
        "最高": "high", "最低": "low",
        "成交量": "volume", "成交额": "amount",
    }

    # fund_purchase_em 列名 → 统一列名
    _PURCHASE_MAP = {
        "基金代码": "symbol", "基金简称": "short_name",
        "最新净值/万份收益": "nav", "最新净值/万份收益-日期": "nav_date",
        "申购状态": "subscribe_status", "赎回状态": "redeem_status",
        "日累计限定金额": "daily_limit", "购买起点": "min_purchase",
    }

    def __init__(self, config):
        """
        Args:
            config: LOFConfig 实例。
        """
        self._config = config
        self._last_request_time: float = 0.0
        self._cache: Optional[tuple[float, pd.DataFrame]] = None
        self._request_interval: float = 1.0
        # Sina 价格独立缓存（TTL 120s）
        self._sina_cache: Optional[tuple[float, pd.DataFrame]] = None
        self._sina_cache_ttl: int = 120
        # 净值/申购状态独立缓存（每天 22:00 后刷新）
        self._purchase_cache: Optional[tuple[float, pd.DataFrame]] = None
        # 基金规模独立缓存（每天 22:00 后刷新）
        self._scale_cache: Optional[tuple[float, pd.DataFrame]] = None

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    def get_all_lofs(self, use_cache: bool = True) -> pd.DataFrame:
        """获取全市场 LOF（市价 + 净值 + 申购状态）。

        合并结果短缓存 5s（仅防重复），各数据源独立管理 TTL。
        """
        if use_cache and self._cache is not None:
            ts, df = self._cache
            if time.time() - ts < 5:
                return df.copy()

        # 直接从独立缓存获取（各自按 TTL 决定是否刷新）
        price_df = self._fetch_sina_prices()
        purchase_df = self._fetch_purchase_data()
        df = self._merge_all(price_df, purchase_df)
        self._cache = (time.time(), df)
        return df

    def get_lof_premium(self, symbols: list[str] | None = None) -> pd.DataFrame:
        """获取 LOF 溢价率列表。"""
        df = self.get_all_lofs()

        if df.empty:
            return pd.DataFrame()

        if symbols:
            df = df[df["symbol"].isin(symbols)]

        df["update_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        df = df.sort_values("premium_rate", ascending=False, na_position="last")

        desired = [
            "symbol", "name", "price", "nav", "nav_date",
            "premium_rate", "pct_change",
            "subscribe_status", "redeem_status", "daily_limit", "min_purchase",
            "volume", "amount",
            "update_time",
        ]
        available = [c for c in desired if c in df.columns]
        return df[available]

    def check_alerts(self, df: pd.DataFrame | None = None) -> list[dict]:
        """根据阈值筛选预警 LOF。"""
        if df is None:
            df = self.get_lof_premium()

        if df.empty or "premium_rate" not in df.columns:
            return []

        premium_threshold = self._config.premium_threshold
        discount_threshold = self._config.discount_threshold

        alerts = []
        for _, row in df.iterrows():
            pr = row.get("premium_rate")
            if pr is None or pd.isna(pr):
                continue

            if pr >= premium_threshold:
                alerts.append(self._build_alert(row, "premium", float(pr)))
            elif pr <= discount_threshold:
                alerts.append(self._build_alert(row, "discount", float(pr)))

        return alerts

    def get_summary(self) -> dict:
        """获取 LOF 汇总（Top 5 溢价/折价 + 预警数）。"""
        df = self.get_lof_premium()
        if df.empty:
            return {
                "top_premium": [], "top_discount": [],
                "alert_count": 0, "total_count": 0,
                "has_iopv": False, "data_source": "sina+em",
                "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }

        has_nav = "premium_rate" in df.columns and df["premium_rate"].notna().any()
        top_premium = []
        top_discount = []
        alerts = []

        if has_nav:
            valid = df[df["premium_rate"].notna()]
            top_premium = self._to_records(valid.nlargest(5, "premium_rate"))
            top_discount = self._to_records(valid.nsmallest(5, "premium_rate"))
            alerts = self.check_alerts(df)

        return {
            "top_premium": top_premium,
            "top_discount": top_discount,
            "alert_count": int(len(alerts)),
            "total_count": int(len(df)),
            "has_iopv": bool(has_nav),
            "data_source": "sina+em",
            "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

    # ------------------------------------------------------------------
    # 数据获取与合并
    # ------------------------------------------------------------------

    def apply_filter(self, df: pd.DataFrame, min_amount: float = 0,
                      subscribe_only: bool = False) -> pd.DataFrame:
        """按成交额和申购状态过滤。

        Args:
            min_amount: 最低成交额（元），0 表示不过滤
            subscribe_only: True 只保留"开放"申购的基金
        """
        if df.empty:
            return df
        if min_amount > 0 and "amount" in df.columns:
            df = df[df["amount"] >= min_amount]
        if subscribe_only and "subscribe_status" in df.columns:
            df = df[df["subscribe_status"].str.contains("开放", na=False)]
        return df

    @staticmethod
    def _daily_cache_valid(cache: tuple[float, pd.DataFrame] | None) -> bool:
        """缓存有效条件：存在 且 缓存在今天 22:00 之后。

        每天的净值数据在 22:00 左右更新，所以 22:00 后刷新。
        """
        if cache is None:
            return False
        ts = cache[0]
        now = datetime.now()
        cutoff = now.replace(hour=22, minute=0, second=0, microsecond=0)
        if now < cutoff:
            # 还没到今天的 22:00 → 缓存只要在今天 0 点之后就算有效
            return ts > now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        else:
            # 已过今天 22:00 → 缓存必须在今天 22:00 之后
            return ts > cutoff.timestamp()

    def _rate_limit(self) -> None:
        elapsed = time.time() - self._last_request_time
        if elapsed < self._request_interval:
            time.sleep(self._request_interval - elapsed)
        self._last_request_time = time.time()

    def _fetch_sina_prices(self) -> pd.DataFrame:
        """从新浪获取 LOF 实时市价，30s 短缓存防重复。"""
        if self._sina_cache is not None:
            ts, df = self._sina_cache
            if time.time() - ts < self._sina_cache_ttl:
                return df.copy()

        import akshare as ak
        self._rate_limit()
        try:
            df = ak.fund_etf_category_sina(symbol="LOF基金")
            if df is None or df.empty:
                raise LOFDataError("新浪 LOF 数据为空")
            df = df.rename(columns={k: v for k, v in self._SINA_MAP.items() if k in df.columns})
            if "symbol" in df.columns:
                df["symbol"] = df["symbol"].astype(str).str[2:]
            self._sina_cache = (time.time(), df)
            logger.info(f"新浪 LOF 市价: {len(df)} 条")
            return df
        except Exception as e:
            logger.error(f"新浪 LOF 获取失败: {e}")
            raise LOFDataError(f"新浪 LOF 失败: {e}") from e

    def _fetch_purchase_data(self) -> pd.DataFrame:
        """从 eastmoney 获取全市场基金申购状态（含净值），每天 22:00 后刷新。"""
        if self._daily_cache_valid(self._purchase_cache):
            return self._purchase_cache[1].copy()

        import akshare as ak
        self._rate_limit()
        try:
            df = ak.fund_purchase_em()
            if df is None or df.empty:
                logger.warning("fund_purchase_em 数据为空")
                return pd.DataFrame()

            rename = {k: v for k, v in self._PURCHASE_MAP.items() if k in df.columns}
            df = df.rename(columns=rename)
            keep = [v for v in rename.values() if v in df.columns]
            df = df[keep].copy()
            if "symbol" in df.columns:
                df["symbol"] = df["symbol"].astype(str).str.strip()
            self._purchase_cache = (time.time(), df)
            logger.info(f"fund_purchase_em: {len(df)} 条")
            return df
        except Exception as e:
            logger.error(f"fund_purchase_em 获取失败: {e}")
            return pd.DataFrame()

    def _fetch_fund_scale(self) -> pd.DataFrame:
        """从 Sina 获取基金规模数据（多分类合并），每天 22:00 后刷新。"""
        if self._daily_cache_valid(self._scale_cache):
            return self._scale_cache[1].copy()

        import akshare as ak
        self._rate_limit()
        all_scale = pd.DataFrame()
        categories = ["混合型基金", "债券型基金", "QDII基金", "货币型基金"]
        for cat in categories:
            try:
                df = ak.fund_scale_open_sina(symbol=cat)
                if df is not None and not df.empty:
                    df.columns = ["序号", "基金代码", "基金简称", "单位净值",
                                  "募集总规模", "资产总份额", "成立日期",
                                  "基金经理", "基准日期"]
                    all_scale = pd.concat([all_scale, df], ignore_index=True)
            except Exception as e:
                logger.debug(f"fund_scale_open_sina({cat}) 失败: {e}")

        try:
            df2 = ak.fund_scale_close_sina()
            if df2 is not None and not df2.empty:
                df2.columns = ["序号", "基金代码", "基金简称", "单位净值",
                               "募集总规模", "资产总份额", "成立日期",
                               "基金经理", "基准日期"]
                all_scale = pd.concat([all_scale, df2], ignore_index=True)
        except Exception as e:
            logger.debug(f"fund_scale_close_sina 失败: {e}")

        if all_scale.empty:
            return pd.DataFrame()

        all_scale = all_scale.drop_duplicates(subset=["基金代码"], keep="first")
        all_scale["symbol"] = all_scale["基金代码"].astype(str).str.strip()
        # AUM = 净值 * 份额（单位：元）
        all_scale["fund_size"] = pd.to_numeric(all_scale["单位净值"], errors="coerce") * \
                                  pd.to_numeric(all_scale["资产总份额"], errors="coerce")
        result = all_scale[["symbol", "fund_size"]].copy()
        self._scale_cache = (time.time(), result)
        logger.info(f"基金规模: {len(result)} 条")
        return result

    def _merge_all(self, price_df: pd.DataFrame, purchase_df: pd.DataFrame) -> pd.DataFrame:
        """按基金代码合并市价和净值。"""
        if price_df.empty:
            return pd.DataFrame()

        price_cols = ["symbol", "price", "pct_change", "volume", "amount"]
        price_sub = price_df[price_cols].copy()

        # 合并净值
        if not purchase_df.empty:
            nav_cols = ["symbol", "nav", "nav_date", "subscribe_status",
                         "redeem_status", "daily_limit", "min_purchase"]
            nav_available = [c for c in nav_cols if c in purchase_df.columns]
            merged = price_sub.merge(
                purchase_df[nav_available].drop_duplicates(subset="symbol", keep="first"),
                on="symbol", how="left")
        else:
            merged = price_sub
            merged["nav"] = None

        # 补回 name
        name_map = dict(zip(price_df["symbol"], price_df.get("name", price_df["symbol"])))
        merged["name"] = merged["symbol"].map(name_map)

        # 计算溢价率
        merged["premium_rate"] = merged.apply(self._safe_premium, axis=1)

        return merged

    @staticmethod
    def _safe_premium(row) -> Optional[float]:
        """安全计算溢价率: (price/nav - 1) * 100。"""
        try:
            price = float(row["price"])
            nav = float(row["nav"])
            if nav <= 0 or pd.isna(nav) or pd.isna(price):
                return None
            return round((price / nav - 1) * 100, 2)
        except (ValueError, TypeError, KeyError, ZeroDivisionError):
            return None

    def _build_alert(self, row, direction: str, pr: float) -> dict:
        """构建单条预警信号。"""
        symbol = str(row.get("symbol", ""))
        name = str(row.get("name", ""))
        price = float(row.get("price", 0))
        nav = float(row.get("nav", 0))
        direction_cn = "溢价" if direction == "premium" else "折价"
        return {
            "action": "alert",
            "symbol": symbol, "name": name,
            "price": round(price, 4), "nav": round(nav, 4),
            "premium_rate": round(pr, 2), "direction": direction,
            "reason": f"LOF{direction_cn}预警: {name}({symbol}) {direction_cn}率 {abs(pr):.2f}%",
            "strength": min(1.0, abs(pr) / 20.0),
            "amount": float(row.get("amount", 0)),
            "update_time": str(row.get("update_time", datetime.now())),
        }

    @staticmethod
    def _to_records(df: pd.DataFrame) -> list[dict]:
        """DataFrame 转纯 Python 记录列表。"""
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
        return records

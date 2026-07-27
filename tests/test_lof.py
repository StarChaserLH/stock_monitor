"""LOF 溢价率模块测试。"""

from unittest.mock import patch

import pandas as pd
import pytest

from app.config import LOFConfig
from app.market.lof_data import LOFDataProvider

# ---- Mock 数据 ----

_SINA_PRICES = pd.DataFrame({
    "代码": ["sz160105", "sz160505", "sz161005", "sz160706", "sz160311"],
    "名称": ["南方LOF", "博时LOF", "富国LOF", "嘉实LOF", "华夏LOF"],
    "最新价": [1.25, 1.08, 0.95, 2.10, 1.50],
    "涨跌幅": [2.5, -1.2, 0.8, 5.0, -0.5],
    "涨跌额": [0.03, -0.01, 0.01, 0.10, -0.01],
    "今开": [1.22, 1.09, 0.94, 2.00, 1.51],
    "昨收": [1.22, 1.09, 0.94, 2.00, 1.51],
    "最高": [1.26, 1.10, 0.96, 2.12, 1.52],
    "最低": [1.22, 1.07, 0.93, 2.00, 1.48],
    "成交量": [100000, 50000, 200000, 80000, 120000],
    "成交额": [125000, 54000, 190000, 168000, 180000],
})

# fund_purchase_em 格式: NAV 是字符串，申购状态含中文
_PURCHASE_DATA = pd.DataFrame({
    "序号": [1, 2, 3, 4, 5],
    "基金代码": ["160105", "160505", "161005", "160706", "160311"],
    "基金简称": ["南方积极配置", "博时主题行业", "富国天惠", "嘉实沪深300", "华夏大盘"],
    "基金类型": ["混合型-灵活", "混合型-偏股", "混合型-偏股", "股票型", "混合型-偏股"],
    "最新净值/万份收益": ["1.1480", "1.0950", "0.9980", "1.8950", "1.4850"],
    "最新净值/万份收益-日期": ["05-29", "05-29", "05-29", "05-29", "05-29"],
    "申购状态": ["开放申购", "限大额申购", "开放申购", "开放申购", "暂停申购"],
    "赎回状态": ["开放赎回", "开放赎回", "开放赎回", "开放赎回", "开放赎回"],
    "日累计限定金额": [1e11, 1e8, 1e11, 1e11, 0],
})


@pytest.fixture
def lof_config():
    return LOFConfig(enabled=True, premium_threshold=5.0, discount_threshold=-3.0)


# fund_scale_open_sina mock — returns fund size data
_SCALE_OPEN = pd.DataFrame({
    "序号": [1, 2, 3],
    "基金代码": ["160105", "160505", "161005"],
    "基金简称": ["南方积极配置", "博时主题行业", "富国天惠"],
    "单位净值": [1.148, 1.095, 0.998],
    "募集总规模": [1e9, 2e9, 5e8],
    "资产总份额": [5e8, 8e8, 2e9],
    "成立日期": ["2008-01-01", "2010-06-01", "2012-03-01"],
    "基金经理": ["张三", "李四", "王五"],
    "基准日期": ["2026-05-29", "2026-05-29", "2026-05-29"],
})

_SCALE_CLOSE = pd.DataFrame({
    "序号": [1, 2],
    "基金代码": ["160706", "160311"],
    "基金简称": ["嘉实沪深300", "华夏大盘"],
    "单位净值": [1.895, 1.485],
    "募集总规模": [3e9, 4e9],
    "资产总份额": [1e9, 1.5e9],
    "成立日期": ["2014-01-01", "2016-01-01"],
    "基金经理": ["赵六", "钱七"],
    "基准日期": ["2026-05-29", "2026-05-29"],
})


@pytest.fixture(autouse=True)
def mock_akshare():
    with patch("akshare.fund_etf_category_sina", return_value=_SINA_PRICES), \
         patch("akshare.fund_purchase_em", return_value=_PURCHASE_DATA), \
         patch("akshare.fund_scale_open_sina", return_value=_SCALE_OPEN), \
         patch("akshare.fund_scale_close_sina", return_value=_SCALE_CLOSE):
        yield


class TestLOFDataProvider:

    def test_premium_calculation(self, lof_config):
        """溢价率 = (price/nav - 1) * 100%，NAV 为字符串需转换。"""
        provider = LOFDataProvider(lof_config)
        df = provider.get_lof_premium()
        assert len(df) == 5
        # 南方LOF: (1.25/1.148 - 1)*100 ≈ 8.89%
        south = df[df["symbol"] == "160105"].iloc[0]
        assert abs(south["premium_rate"] - 8.89) < 0.2
        # 博时LOF: (1.08/1.095 - 1)*100 ≈ -1.37%
        boshi = df[df["symbol"] == "160505"].iloc[0]
        assert abs(boshi["premium_rate"] - (-1.37)) < 0.2

    def test_subscribe_status_column(self, lof_config):
        """申购状态列应存在且有值。"""
        provider = LOFDataProvider(lof_config)
        df = provider.get_lof_premium()
        assert "subscribe_status" in df.columns
        south = df[df["symbol"] == "160105"].iloc[0]
        assert "开放" in str(south["subscribe_status"])
        huaxia = df[df["symbol"] == "160311"].iloc[0]
        assert "暂停" in str(huaxia["subscribe_status"])

    def test_daily_limit_column(self, lof_config):
        """单日累计限额列应存在。"""
        provider = LOFDataProvider(lof_config)
        df = provider.get_lof_premium()
        assert any(c in df.columns for c in ["daily_limit", "min_purchase"])

    def test_premium_sorting(self, lof_config):
        provider = LOFDataProvider(lof_config)
        df = provider.get_lof_premium()
        rates = df["premium_rate"].dropna().tolist()
        assert rates == sorted(rates, reverse=True)

    def test_symbol_clean(self, lof_config):
        provider = LOFDataProvider(lof_config)
        df = provider.get_lof_premium()
        assert all(not s.startswith(("sz", "sh")) for s in df["symbol"])

    def test_cache(self, lof_config):
        provider = LOFDataProvider(lof_config)
        provider._cache_ttl = 999
        df1 = provider.get_all_lofs()
        df2 = provider.get_all_lofs()
        assert len(df1) == len(df2)


class TestLOFAlerts:

    def test_premium_alert(self, lof_config):
        provider = LOFDataProvider(lof_config)
        alerts = provider.check_alerts()
        south = [a for a in alerts if a["symbol"] == "160105"]
        assert len(south) == 1
        assert south[0]["direction"] == "premium"

    def test_discount_alert(self, lof_config):
        provider = LOFDataProvider(lof_config)
        alerts = provider.check_alerts()
        fuguo = [a for a in alerts if a["symbol"] == "161005"]
        assert len(fuguo) == 1
        assert fuguo[0]["direction"] == "discount"

    def test_no_alert_normal(self, lof_config):
        provider = LOFDataProvider(lof_config)
        alerts = provider.check_alerts()
        boshi = [a for a in alerts if a["symbol"] == "160505"]
        assert len(boshi) == 0

    def test_alert_has_nav(self, lof_config):
        provider = LOFDataProvider(lof_config)
        alerts = provider.check_alerts()
        for a in alerts:
            assert "nav" in a


class TestLOFSummary:

    def test_lof_columns(self, lof_config):
        """关键列应存在。"""
        provider = LOFDataProvider(lof_config)
        df = provider.get_lof_premium()
        assert "amount" in df.columns
        assert any(c in df.columns for c in ["daily_limit", "min_purchase"])
        assert "subscribe_status" in df.columns

    def test_summary_structure(self, lof_config):
        provider = LOFDataProvider(lof_config)
        summary = provider.get_summary()
        assert summary["total_count"] == 5
        assert summary["alert_count"] > 0
        assert len(summary["top_premium"]) <= 5
        assert len(summary["top_discount"]) <= 5

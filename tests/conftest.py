"""共享测试夹具。"""

import sys
from pathlib import Path

import pandas as pd
import pytest

# 确保项目根目录在 sys.path
_PROJECT_ROOT = Path(__file__).parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from app.config import AppConfig


@pytest.fixture(scope="session")
def app_config():
    """提供测试用 AppConfig。"""
    config = AppConfig()
    # 使用临时目录避免污染实际数据
    config.system.data_dir = "data/test"
    return config


@pytest.fixture
def sample_ohlcv_data() -> pd.DataFrame:
    """生成 60 天的 OHLCV 测试数据。"""
    import numpy as np
    np.random.seed(42)
    dates = pd.date_range("2025-01-01", periods=60, freq="D")
    df = pd.DataFrame({
        "open": np.random.randn(60).cumsum() + 10,
        "high": np.random.randn(60).cumsum() + 11,
        "low": np.random.randn(60).cumsum() + 9,
        "close": np.random.randn(60).cumsum() + 10,
        "volume": np.abs(np.random.randn(60)) * 1e6 + 1e6,
        "amount": np.abs(np.random.randn(60)) * 1e8,
        "pct_change": np.random.randn(60) * 0.02,
    }, index=dates)
    return df


@pytest.fixture
def sample_context():
    """生成测试用策略 context。"""
    return {
        "positions": {"510050": 1000},
        "cash": 80000.0,
        "signals": [],
        "holdings": {"510050": 2500.0},
    }

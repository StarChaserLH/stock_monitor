"""
配置管理模块。

支持多层级加载：
  1. config.yaml — 默认值
  2. config.local.yaml — 本地覆盖（如存在）
  3. .env — 环境变量，替换 ${VAR} 占位符
"""

import os
import re
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel


# ---- 加载环境变量 ----
_ENV_FILE = Path(__file__).parent.parent / ".env"
if _ENV_FILE.exists():
    load_dotenv(_ENV_FILE)
else:
    load_dotenv()  # 尝试从当前目录加载


def _resolve_env_vars(value: Any) -> Any:
    """递归解析字符串中的 ${VAR} 环境变量引用。"""
    if isinstance(value, str):
        pattern = re.compile(r"\$\{(\w+)\}")
        matches = pattern.findall(value)
        if matches and all(m in os.environ for m in matches):
            for m in matches:
                value = value.replace(f"${{{m}}}", os.environ[m])
        return value
    elif isinstance(value, dict):
        return {k: _resolve_env_vars(v) for k, v in value.items()}
    elif isinstance(value, list):
        return [_resolve_env_vars(v) for v in value]
    return value


def load_config(config_path: str | None = None) -> dict[str, Any]:
    """加载并合并配置文件。

    Args:
        config_path: 配置文件路径，默认为项目根目录的 config.yaml。

    Returns:
        解析后的配置字典。
    """
    base_dir = Path(__file__).parent.parent
    primary = config_path or str(base_dir / "config.yaml")
    local = str(base_dir / "config.local.yaml")

    with open(primary, encoding="utf-8") as f:
        config: dict[str, Any] = yaml.safe_load(f)

    # 合并本地配置
    if os.path.exists(local):
        with open(local, encoding="utf-8") as f:
            local_config = yaml.safe_load(f) or {}
        _deep_merge(config, local_config)

    return _resolve_env_vars(config)


def _deep_merge(base: dict, override: dict) -> None:
    """原地深度合并 override 到 base。"""
    for key, value in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value


# ---- 类型化配置模型 ----

class SystemConfig(BaseModel):
    name: str = "A股量化监测系统"
    version: str = "1.0.0"
    log_level: str = "INFO"
    log_file: str = "logs/system.log"
    data_dir: str = "data"


class MarketConfig(BaseModel):
    provider: str = "akshare"
    request_interval: float = 0.5
    max_retries: int = 3
    timeout: int = 15
    cache_ttl: int = 60


class LLMConfig(BaseModel):
    api_key: str = ""
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-v4-pro"
    temperature: float = 0.1
    max_tokens: int = 4096
    max_retries: int = 3


class StrategyConfig(BaseModel):
    storage_dir: str = "strategies"
    max_active: int = 10
    timeout_seconds: int = 30


class SymbolsConfig(BaseModel):
    mode: str = "specific"
    groups: dict[str, list[str]] = {}
    blacklist: list[str] = []


class TradingConfig(BaseModel):
    initial_capital: float = 100000.0
    commission_rate: float = 0.0001
    min_lot_size: int = 100
    slippage: float = 0.001
    stamp_duty: float = 0.0
    max_position_ratio: float = 0.3


class SchedulerConfig(BaseModel):
    interval_seconds: int = 60
    idle_interval_seconds: int = 300
    min_signal_strength: float = 0.3
    trading_start: str = "09:30"
    trading_lunch_start: str = "11:30"
    trading_lunch_end: str = "13:00"
    trading_end: str = "15:00"


class MarketTimingConfig(BaseModel):
    enabled: bool = True
    benchmark: str = "510300"
    ma_weak: int = 60
    ma_bear: int = 200
    strength_discount: float = 0.7
class WebConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 5000
    password: str = "admin123"
    secret_key: str = "change-me-to-a-random-string"


class LOFReportConfig(BaseModel):
    enabled: bool = True
    schedule: str = "14:30"
    recipient: str = "your@email.com"
    top_n: int = 0  # 0 = 全量


class LOFConfig(BaseModel):
    enabled: bool = True
    premium_threshold: float = 5.0
    discount_threshold: float = -3.0
    scan_interval_minutes: int = 5
    filter_min_amount: float = 5000000
    filter_subscribe_only: bool = False
    report: LOFReportConfig = LOFReportConfig()


class AppConfig:
    """应用配置单例，提供类型化属性访问。"""

    _instance: "AppConfig | None" = None

    def __init__(self, config_path: str | None = None):
        raw = load_config(config_path)
        self.system = SystemConfig(**raw.get("system", {}))
        self.market = MarketConfig(**raw.get("market", {}))
        self.llm = LLMConfig(**raw.get("llm", {}))
        self.strategy = StrategyConfig(**raw.get("strategy", {}))
        self.symbols = SymbolsConfig(**raw.get("symbols", {}))
        self.trading = TradingConfig(**raw.get("trading", {}))
        self.scheduler = SchedulerConfig(**raw.get("scheduler", {}))
        self.market_timing = MarketTimingConfig(**raw.get("market_timing", {}))
        self.web = WebConfig(**raw.get("web", {}))
        lof_raw = raw.get("lof", {})
        report_raw = lof_raw.pop("report", {}) if "report" in lof_raw else {}
        lof_raw["report"] = LOFReportConfig(**report_raw)
        self.lof = LOFConfig(**lof_raw)
        self._raw_notification = raw.get("notification", {})

    @property
    def notification_enabled(self) -> bool:
        return self._raw_notification.get("enabled", True)

    # ---- 免打扰（新结构） ----

    @property
    def notification_quiet_enabled(self) -> bool:
        return self._raw_notification.get("quiet_hours", {}).get("enabled", True)

    @property
    def notification_quiet_start_hour(self) -> int:
        return self._raw_notification.get("quiet_hours", {}).get("start", 22)

    @property
    def notification_quiet_end_hour(self) -> int:
        return self._raw_notification.get("quiet_hours", {}).get("end", 8)

    # ---- 兼容旧接口（被 BaseNotifier 使用） ----

    @property
    def notification_quiet_start(self) -> str:
        h = self.notification_quiet_start_hour
        return f"{h:02d}:00"

    @property
    def notification_quiet_end(self) -> str:
        h = self.notification_quiet_end_hour
        return f"{h:02d}:00"

    @property
    def notification_rate_limit(self) -> int:
        return self._raw_notification.get("frequency", {}).get("max_per_hour", 20)

    # ---- 频率限制 ----

    @property
    def notification_min_interval_minutes(self) -> int:
        return self._raw_notification.get("frequency", {}).get("min_interval_minutes", 5)

    @property
    def notification_max_per_hour(self) -> int:
        return self._raw_notification.get("frequency", {}).get("max_per_hour", 20)

    @property
    def notification_history_retention(self) -> int:
        return self._raw_notification.get("history_retention", 500)

    # ---- 渠道快捷访问 ----

    @property
    def wecom_enabled(self) -> bool:
        return self._raw_notification.get("channels", {}).get("wecom_bot", {}).get("enabled", False)

    @property
    def wecom_webhook_url(self) -> str:
        return self._raw_notification.get("channels", {}).get("wecom_bot", {}).get("webhook_url", "")

    @property
    def email_enabled(self) -> bool:
        return self._raw_notification.get("channels", {}).get("email", {}).get("enabled", False)

    @property
    def email_config(self) -> dict:
        return self._raw_notification.get("channels", {}).get("email", {})

    @classmethod
    def get_instance(cls, config_path: str | None = None) -> "AppConfig":
        if cls._instance is None:
            cls._instance = cls(config_path)
        return cls._instance

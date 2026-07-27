"""
消息推送抽象接口。

所有推送渠道必须实现此基类。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, time
from enum import Enum
from typing import Optional


class NotifyLevel(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass
class Notification:
    """通知数据结构。"""
    title: str
    content: str
    level: NotifyLevel = NotifyLevel.INFO
    timestamp: str | None = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class BaseNotifier(ABC):
    """通知渠道抽象基类。

    所有推送实现必须继承此类并实现 send 方法。

    Usage:
        class MyNotifier(BaseNotifier):
            def send(self, notification: Notification) -> bool:
                ...
    """

    def __init__(self, config):
        """
        Args:
            config: AppConfig 实例。
        """
        self._config = config
        self._sent_count: int = 0
        self._last_reset: datetime = datetime.now()

    @abstractmethod
    def send(self, notification: Notification) -> bool:
        """发送通知。

        Args:
            notification: 通知对象。

        Returns:
            发送成功返回 True。
        """
        ...

    def is_quiet_time(self) -> bool:
        """判断当前是否在免打扰时段。"""
        quiet_start = self._parse_time(self._config.notification_quiet_start)
        quiet_end = self._parse_time(self._config.notification_quiet_end)
        now = datetime.now().time()

        if quiet_start < quiet_end:
            return quiet_start <= now <= quiet_end
        else:
            # 跨日时段，如 22:00 - 08:00
            return now >= quiet_start or now <= quiet_end

    def check_rate_limit(self) -> bool:
        """检查是否超过频率限制。

        Returns:
            允许发送返回 True。
        """
        now = datetime.now()
        if (now - self._last_reset).seconds >= 60:
            self._sent_count = 0
            self._last_reset = now

        limit = self._config.notification_rate_limit
        if limit > 0 and self._sent_count >= limit:
            return False

        self._sent_count += 1
        return True

    @staticmethod
    def _parse_time(time_str: str) -> time:
        """解析 'HH:MM' 字符串为 time 对象。"""
        h, m = time_str.split(":")
        return time(int(h), int(m))

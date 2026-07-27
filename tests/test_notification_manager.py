"""消息推送管理器测试。"""

from unittest.mock import MagicMock, patch

import pytest

from app.config import AppConfig
from app.notify.base import Notification, NotifyLevel
from app.notify.manager import NotificationManager


@pytest.fixture
def notif_mgr(app_config: AppConfig):
    """创建测试用 NotificationManager，mock 掉渠道实例。"""
    with patch("app.notify.manager.NotificationManager._init_channels", return_value=None):
        mgr = NotificationManager(app_config)
        # 注入 mock 渠道
        mock_notifier = MagicMock()
        mock_notifier.send.return_value = True
        mgr._channels = {"wecom_bot": mock_notifier, "email": mock_notifier}
        return mgr


class TestQuietHours:
    """免打扰时段测试。"""

    def test_quiet_hours_disabled(self, notif_mgr):
        notif_mgr._quiet_enabled = False
        assert notif_mgr._is_quiet_time() is False

    def test_quiet_hours_within_range(self, notif_mgr):
        """免打扰时段内（如 22-8，当前小时在范围内）。"""
        notif_mgr._quiet_enabled = True
        # Mock datetime.now().hour
        with patch("app.notify.manager.datetime") as mock_dt:
            mock_dt.now.return_value.hour = 23
            assert notif_mgr._is_quiet_time() is True
            mock_dt.now.return_value.hour = 3
            assert notif_mgr._is_quiet_time() is True

    def test_quiet_hours_outside_range(self, notif_mgr):
        """免打扰时段外。"""
        notif_mgr._quiet_enabled = True
        with patch("app.notify.manager.datetime") as mock_dt:
            mock_dt.now.return_value.hour = 12
            assert notif_mgr._is_quiet_time() is False


class TestFrequencyLimit:
    """频率限制测试。"""

    def test_same_title_interval(self, notif_mgr):
        """相同标题在间隔内不发送。"""
        notif_mgr._min_interval = 5
        notification = Notification("买入信号", "详情", NotifyLevel.WARNING)

        # 第一次应该成功
        result1 = notif_mgr.send("wecom_bot", notification)
        assert result1 is True

        # 第二次相同标题，间隔不足应跳过
        result2 = notif_mgr.send("wecom_bot", notification)
        assert result2 is False

    def test_different_title_passes(self, notif_mgr):
        """不同标题不受相同标题限制。"""
        notif_mgr._min_interval = 5
        notif_mgr.send("wecom_bot", Notification("买入信号A", "", NotifyLevel.WARNING))
        result = notif_mgr.send("wecom_bot", Notification("买入信号B", "", NotifyLevel.WARNING))
        assert result is True


class TestHistory:
    """推送历史测试。"""

    def test_history_recording(self, notif_mgr):
        """发送后应记录到历史。"""
        notif_mgr.clear_history()  # 确保干净状态
        notif_mgr.send("wecom_bot", Notification("测试标题", "内容", NotifyLevel.INFO))
        hist = notif_mgr.get_history(page=1, page_size=10)
        assert hist["total"] >= 1
        assert hist["items"][0]["title"] == "测试标题"

    def test_history_filter_by_level(self, notif_mgr):
        """按级别筛选历史。"""
        notif_mgr.send("wecom_bot", Notification("T1", "", NotifyLevel.INFO))
        notif_mgr.send("wecom_bot", Notification("T2", "", NotifyLevel.WARNING))
        hist = notif_mgr.get_history(level="warning")
        assert all(h["level"] == "warning" for h in hist["items"])

    def test_history_filter_by_channel(self, notif_mgr):
        """按渠道筛选。"""
        notif_mgr.send("wecom_bot", Notification("T1", "", NotifyLevel.INFO))
        notif_mgr.send("email", Notification("T2", "", NotifyLevel.INFO))
        hist = notif_mgr.get_history(channel="wecom_bot")
        assert all(h["channel"] == "wecom_bot" for h in hist["items"])
        hist = notif_mgr.get_history(channel="email")
        assert all(h["channel"] == "email" for h in hist["items"])

    def test_clear_history(self, notif_mgr):
        """清空历史。"""
        notif_mgr.send("wecom_bot", Notification("标题", "", NotifyLevel.INFO))
        assert notif_mgr.get_history()["total"] >= 1
        notif_mgr.clear_history()
        assert notif_mgr.get_history()["total"] == 0

    def test_history_pagination(self, notif_mgr):
        """分页测试。"""
        for i in range(25):
            notif_mgr.send("wecom_bot", Notification(f"T{i}", "", NotifyLevel.INFO))
        hist1 = notif_mgr.get_history(page=1, page_size=10)
        assert len(hist1["items"]) == 10
        assert hist1["total"] >= 25
        assert hist1["total_pages"] >= 3

        hist2 = notif_mgr.get_history(page=2, page_size=10)
        assert len(hist2["items"]) == 10


class TestConfig:
    """配置管理测试。"""

    def test_get_full_config(self, notif_mgr):
        config = notif_mgr.get_full_config()
        assert "enabled" in config
        assert "quiet_hours" in config
        assert "frequency" in config
        assert "channels" in config

    def test_get_status(self, notif_mgr):
        status = notif_mgr.get_status()
        assert "channels" in status
        assert "today_count" in status
        assert "total_history" in status
        assert "quiet_active" in status

    def test_test_channel_not_found(self, notif_mgr):
        result = notif_mgr.test_channel("nonexistent")
        assert result["ok"] is False
        assert "不存在" in result["message"]

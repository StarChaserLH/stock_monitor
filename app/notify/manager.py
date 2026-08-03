"""
消息推送管理器。

封装多渠道推送，提供统一的发送接口、频率控制、
推送历史记录和配置持久化。
"""

import json
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import yaml

from app.notify.base import BaseNotifier, Notification, NotifyLevel

logger = logging.getLogger(__name__)

_HISTORY_FILE = None  # 由 init 时设置


def _get_history_path() -> Path:
    global _HISTORY_FILE
    if _HISTORY_FILE is None:
        _HISTORY_FILE = Path("logs/push_history.json")
    return _HISTORY_FILE


class NotificationManager:
    """多渠道推送管理器。

    负责：
      - 管理多个推送渠道实例
      - 频率限制（相同标题间隔 + 每小时上限）
      - 推送历史记录（内存 + JSON 文件持久化）
      - 免打扰时段控制
      - 配置读写回 config.yaml

    Usage:
        mgr = NotificationManager(config)
        mgr.send("企业微信机器人", notification)
        mgr.test_channel("wecom_bot")
        history = mgr.get_history(level="warning", page=1)
    """

    def __init__(self, config):
        """
        Args:
            config: AppConfig 实例。
        """
        self._config = config
        self._channels: dict[str, BaseNotifier] = {}
        self._history: list[dict] = []
        self._hourly_count: list[float] = []  # 最近1小时发送的时间戳
        self._title_timestamps: dict[str, float] = {}  # title -> last send time

        # 免打扰
        self._quiet_enabled = config.notification_quiet_enabled
        self._quiet_start = config.notification_quiet_start_hour
        self._quiet_end = config.notification_quiet_end_hour

        # 频率配置
        self._min_interval = config.notification_min_interval_minutes
        self._held_interval = config.notification_held_interval_minutes
        self._watch_interval = config.notification_watch_interval_minutes
        self._max_per_hour = config.notification_max_per_hour

        # 历史保留数
        self._history_retention = config.notification_history_retention

        # 延迟导入渠道实现
        self._init_channels()
        self._load_history()

    # ------------------------------------------------------------------
    # 渠道管理
    # ------------------------------------------------------------------

    def _init_channels(self) -> None:
        """根据配置初始化各推送渠道。"""
        cfg = self._config

        if cfg.wecom_enabled:
            from app.notify.wecom import WeComNotifier
            self._channels["wecom_bot"] = WeComNotifier(cfg)

        if cfg.email_enabled:
            from app.notify.email_ import EmailNotifier
            self._channels["email"] = EmailNotifier(cfg)

    def get_channel_status(self) -> dict[str, dict]:
        """获取所有渠道的状态信息。"""
        result = {}
        raw_channels = self._config._raw_notification.get("channels", {})
        for key, ch_cfg in raw_channels.items():
            notifier = self._channels.get(key)
            result[key] = {
                "name": ch_cfg.get("name", key),
                "enabled": ch_cfg.get("enabled", False),
                "webhook_url": self._mask_url(ch_cfg.get("webhook_url", "")),
                "connected": notifier is not None,
                "config": {k: v for k, v in ch_cfg.items()
                          if k not in ("webhook_url", "password", "username", "recipients")},
            }
            # 脱敏显示关键字段
            if "username" in ch_cfg:
                result[key]["username"] = self._mask_str(ch_cfg["username"])
            if "recipients" in ch_cfg:
                result[key]["recipients"] = [self._mask_str(r) for r in ch_cfg["recipients"]]
        return result

    # ------------------------------------------------------------------
    # 发送与频率控制
    # ------------------------------------------------------------------

    def send(self, channel_key: str, notification: Notification) -> bool:
        """通过指定渠道发送通知，自动执行频率限制。

        Args:
            channel_key: 渠道标识，如 'wecom_bot', 'email'。
            notification: 通知对象。

        Returns:
            成功返回 True（被频率限制跳过也返回 False）。
        """
        if channel_key not in self._channels:
            logger.warning(f"渠道不存在: {channel_key}")
            return False

        # 免打扰检查
        if notification.level == NotifyLevel.INFO and self._is_quiet_time():
            self._record_history(channel_key, notification, False, "免打扰时段")
            return False

        # 频率限制：持仓/自选分别控制间隔
        if notification.level == NotifyLevel.WARNING:
            interval = self._held_interval
        elif notification.level == NotifyLevel.INFO:
            interval = self._watch_interval
        else:
            interval = self._min_interval

        title_key = notification.title
        if title_key in self._title_timestamps:
            elapsed = (time.time() - self._title_timestamps[title_key]) / 60.0
            if elapsed < interval:
                self._record_history(channel_key, notification, False,
                                     f"间隔不足 ({elapsed:.1f}min < {interval}min)")
                return False

        # 频率限制：每小时上限
        self._prune_hourly()
        if self._max_per_hour > 0 and len(self._hourly_count) >= self._max_per_hour:
            self._record_history(channel_key, notification, False, "超过每小时推送上限")
            return False

        # 发送
        notifier = self._channels[channel_key]
        try:
            ok = notifier.send(notification)
            self._record_history(channel_key, notification, ok, "" if ok else "发送失败")
            if ok:
                self._title_timestamps[title_key] = time.time()
                self._hourly_count.append(time.time())
            return ok
        except Exception as e:
            self._record_history(channel_key, notification, False, str(e))
            return False

    def send_all(self, notification: Notification) -> dict[str, bool]:
        """向所有已启用渠道发送通知。"""
        results = {}
        for key in self._channels:
            results[key] = self.send(key, notification)
        return results

    def send_report(self, channel_key: str, title: str, html_body: str) -> bool:
        """发送定时报告（绕过频率限制和免打扰检查）。

        Args:
            channel_key: 渠道标识。
            title: 邮件标题。
            html_body: HTML 正文。

        Returns:
            成功返回 True。
        """
        if channel_key not in self._channels:
            logger.warning(f"渠道不存在: {channel_key}")
            return False

        notifier = self._channels[channel_key]
        notification = Notification(title=title, content=html_body, level=NotifyLevel.WARNING)
        try:
            ok = notifier.send(notification)
            self._record_history(channel_key, notification, ok, "" if ok else "发送失败")
            return ok
        except Exception as e:
            self._record_history(channel_key, notification, False, str(e))
            return False

    def test_channel(self, channel_key: str) -> dict:
        """测试指定渠道是否可用。

        Returns:
            {"ok": bool, "message": str}
        """
        if channel_key not in self._channels:
            return {"ok": False, "message": f"渠道 '{channel_key}' 不存在"}

        test = Notification(
            title="ETF Monitor 测试消息",
            content=f"这是一条测试推送，时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            level=NotifyLevel.INFO,
        )

        # 测试时不记录到历史、不受频率限制
        notifier = self._channels[channel_key]
        try:
            ok = notifier.send(test)
            return {"ok": ok, "message": "发送成功" if ok else "发送失败，请检查 Webhook 配置"}
        except Exception as e:
            return {"ok": False, "message": f"发送异常: {str(e)}"}

    # ------------------------------------------------------------------
    # 配置管理
    # ------------------------------------------------------------------

    def get_full_config(self) -> dict:
        """获取当前完整的推送配置（供 Web 页面展示）。"""
        raw = self._config._raw_notification
        channels_status = self.get_channel_status()
        return {
            "enabled": self._config.notification_enabled,
            "quiet_hours": {
                "enabled": self._quiet_enabled,
                "start": self._quiet_start,
                "end": self._quiet_end,
            },
            "frequency": {
                "min_interval_minutes": self._min_interval,
                "max_per_hour": self._max_per_hour,
            },
            "history_retention": self._history_retention,
            "channels": channels_status,
        }

    def update_config(self, new_config: dict) -> dict:
        """更新推送配置并持久化到 config.yaml。

        Args:
            new_config: Web 前端提交的配置 dict。

        Returns:
            {"status": "ok"} 或 {"status": "error", "message": "..."}。
        """
        try:
            # 更新内存
            if "enabled" in new_config:
                self._config._raw_notification["enabled"] = new_config["enabled"]

            if "quiet_hours" in new_config:
                qh = new_config["quiet_hours"]
                self._quiet_enabled = qh.get("enabled", True)
                self._quiet_start = int(qh.get("start", 22))
                self._quiet_end = int(qh.get("end", 8))
                self._config._raw_notification["quiet_hours"] = {
                    "enabled": self._quiet_enabled,
                    "start": self._quiet_start,
                    "end": self._quiet_end,
                }

            if "frequency" in new_config:
                freq = new_config["frequency"]
                self._min_interval = int(freq.get("min_interval_minutes", 5))
                self._held_interval = int(freq.get("held_interval_minutes", 10))
                self._watch_interval = int(freq.get("watch_interval_minutes", 60))
                self._max_per_hour = int(freq.get("max_per_hour", 20))
                self._config._raw_notification["frequency"] = {
                    "min_interval_minutes": self._min_interval,
                    "held_interval_minutes": self._held_interval,
                    "watch_interval_minutes": self._watch_interval,
                    "max_per_hour": self._max_per_hour,
                }

            if "history_retention" in new_config:
                self._history_retention = int(new_config["history_retention"])
                self._config._raw_notification["history_retention"] = self._history_retention

            # 更新渠道启用状态
            if "channels" in new_config:
                for key, ch_data in new_config["channels"].items():
                    channels_cfg = self._config._raw_notification.get("channels", {})
                    if key in channels_cfg:
                        if "enabled" in ch_data:
                            channels_cfg[key]["enabled"] = ch_data["enabled"]
                        if "webhook_url" in ch_data and ch_data.get("webhook_url"):
                            channels_cfg[key]["webhook_url"] = ch_data["webhook_url"]
                        # 邮箱字段
                        for f in ("username", "password", "recipients", "smtp_host", "smtp_port"):
                            if f in ch_data and ch_data[f]:
                                channels_cfg[key][f] = ch_data[f]
                        # 重新创建渠道实例
                        self._init_channels()

            # 持久化到 config.yaml
            self._write_config_to_file()

            return {"status": "ok"}
        except Exception as e:
            logger.error(f"更新推送配置失败: {e}", exc_info=True)
            return {"status": "error", "message": str(e)}

    # ------------------------------------------------------------------
    # 历史记录
    # ------------------------------------------------------------------

    def get_history(
        self,
        page: int = 1,
        page_size: int = 20,
        channel: str = "",
        level: str = "",
        date_from: str = "",
        date_to: str = "",
    ) -> dict:
        """获取推送历史记录（支持筛选和分页）。

        Returns:
            {"items": [...], "total": int, "page": int, "total_pages": int}
        """
        items = list(self._history)

        # 筛选
        if channel:
            items = [h for h in items if h.get("channel") == channel]
        if level:
            items = [h for h in items if h.get("level") == level]
        if date_from:
            items = [h for h in items if h.get("time", "")[:10] >= date_from]
        if date_to:
            items = [h for h in items if h.get("time", "")[:10] <= date_to]

        total = len(items)
        total_pages = max(1, (total + page_size - 1) // page_size)
        start = (page - 1) * page_size
        paged = items[start:start + page_size]

        return {
            "items": paged,
            "total": total,
            "page": page,
            "total_pages": total_pages,
        }

    def clear_history(self) -> None:
        """清空所有推送历史。"""
        self._history.clear()
        self._title_timestamps.clear()
        self._hourly_count.clear()
        self._save_history()
        logger.info("推送历史已清空")

    def get_status(self) -> dict:
        """获取推送模块运行状态。"""
        last = self._history[-1] if self._history else None
        channels_ok = {}
        for key in self._channels:
            channels_ok[key] = True
        # 检查配置中但未初始化的渠道
        raw_channels = self._config._raw_notification.get("channels", {})
        for key in raw_channels:
            if key not in channels_ok:
                channels_ok[key] = raw_channels[key].get("enabled", False)

        return {
            "channels": channels_ok,
            "last_push_time": last["time"] if last else None,
            "last_push_title": last["title"] if last else None,
            "total_history": len(self._history),
            "today_count": sum(
                1 for h in self._history
                if h.get("time", "").startswith(datetime.now().strftime("%Y-%m-%d"))
            ),
            "quiet_active": self._is_quiet_time(),
        }

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _is_quiet_time(self) -> bool:
        """判断当前是否在免打扰时段。"""
        if not self._quiet_enabled:
            return False
        now = datetime.now().hour
        start = self._quiet_start
        end = self._quiet_end
        if start < end:
            return start <= now < end
        else:
            # 跨日，如 22 -> 8
            return now >= start or now < end

    def _record_history(self, channel_key: str, notification: Notification,
                        success: bool, skip_reason: str = "") -> None:
        """记录推送历史。"""
        entry = {
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "channel": channel_key,
            "title": notification.title,
            "content": notification.content[:200],
            "level": notification.level.value,
            "success": success,
            "skip_reason": skip_reason,
        }
        self._history.append(entry)
        # 保留最近 N 条
        if len(self._history) > self._history_retention:
            self._history = self._history[-self._history_retention:]
        # 每 10 条异步写一次文件
        if len(self._history) % 10 == 0:
            self._save_history()

    def _prune_hourly(self) -> None:
        """清理超过 1 小时的时间戳。"""
        cutoff = time.time() - 3600
        self._hourly_count = [t for t in self._hourly_count if t > cutoff]

    def _save_history(self) -> None:
        """持久化历史到 JSON 文件。"""
        try:
            path = _get_history_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(self._history, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.error(f"保存推送历史失败: {e}")

    def _load_history(self) -> None:
        """从 JSON 文件加载历史。"""
        try:
            path = _get_history_path()
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    self._history = data[-self._history_retention:]
                    logger.info(f"推送历史已加载: {len(self._history)} 条")
        except Exception as e:
            logger.error(f"加载推送历史失败: {e}")

    def _write_config_to_file(self) -> None:
        """将当前内存配置写回 config.yaml。"""
        try:
            config_path = Path(__file__).parent.parent.parent / "config.yaml"
            with open(config_path, encoding="utf-8") as f:
                full = yaml.safe_load(f) or {}

            # 更新 notification 节
            full["notification"] = self._build_raw_config()

            with open(config_path, "w", encoding="utf-8") as f:
                yaml.dump(full, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

            logger.info("推送配置已写回 config.yaml")
        except Exception as e:
            logger.error(f"写回 config.yaml 失败: {e}")

    def _build_raw_config(self) -> dict:
        """构建原始配置字典用于序列化。"""
        channels_cfg = {}
        raw_channels = self._config._raw_notification.get("channels", {})
        for key, ch_cfg in raw_channels.items():
            channels_cfg[key] = dict(ch_cfg)

        return {
            "enabled": self._config.notification_enabled,
            "quiet_hours": {
                "enabled": self._quiet_enabled,
                "start": self._quiet_start,
                "end": self._quiet_end,
            },
            "frequency": {
                "min_interval_minutes": self._min_interval,
                "max_per_hour": self._max_per_hour,
            },
            "history_retention": self._history_retention,
            "channels": channels_cfg,
        }

    @staticmethod
    def _mask_url(url: str) -> str:
        """脱敏 Webhook URL，只显示域名和前缀。"""
        if not url:
            return ""
        import re
        # 隐藏 access_token / key 参数值
        return re.sub(r'(token|key|access_token)=[^&]+', r'\1=****', url)

    @staticmethod
    def _mask_str(s: str) -> str:
        """脱敏字符串。"""
        if not s:
            return ""
        if "@" in s:
            # 邮箱脱敏
            name, domain = s.split("@", 1)
            return name[:2] + "***@" + domain
        if len(s) > 6:
            return s[:3] + "****" + s[-2:]
        return s[:1] + "***"

"""
企业微信机器人推送渠道。

使用群机器人 Webhook 发送消息。
文档：https://developer.work.weixin.qq.com/document/path/91770
"""

import json
import logging
from datetime import datetime

import requests

from app.notify.base import BaseNotifier, Notification, NotifyLevel

logger = logging.getLogger(__name__)


class WeComNotifier(BaseNotifier):
    """企业微信机器人推送。

    Usage:
        notifier = WeComNotifier(config)
        notifier.send(Notification("标题", "内容", NotifyLevel.INFO))
    """

    def __init__(self, config):
        super().__init__(config)
        self._webhook_url = config.wecom_webhook_url

    def send(self, notification: Notification) -> bool:
        """通过企业微信机器人发送通知。

        如果处于免打扰时段，info 级别消息将跳过。
        """
        if notification.level == NotifyLevel.INFO and self.is_quiet_time():
            logger.debug("免打扰时段，跳过 info 通知")
            return False

        if not self.check_rate_limit():
            logger.warning("推送频率超限，消息被丢弃")
            return False

        if not self._webhook_url:
            logger.warning("企业微信 Webhook URL 未配置")
            return False

        markdown_content = self._format_markdown(notification)

        payload = {
            "msgtype": "markdown",
            "markdown": {
                "content": markdown_content,
            },
        }

        try:
            resp = requests.post(
                self._webhook_url,
                json=payload,
                timeout=10,
            )
            result = resp.json()
            if result.get("errcode") == 0:
                logger.info(f"企业微信推送成功: {notification.title}")
                return True
            else:
                logger.error(f"企业微信推送失败: {result}")
                return False
        except Exception as e:
            logger.error(f"企业微信推送异常: {e}")
            return False

    def _format_markdown(self, n: Notification) -> str:
        """构造 Markdown 格式消息。"""
        level_emoji = {
            NotifyLevel.INFO: "📊",
            NotifyLevel.WARNING: "⚠️",
            NotifyLevel.CRITICAL: "🚨",
        }
        emoji = level_emoji.get(n.level, "")

        return f"""## {emoji} {n.title}
> 时间: {n.timestamp}
> 级别: <font color="{'warning' if n.level == 'warning' else 'info' if n.level == 'info' else 'comment'}">{n.level.upper()}</font>

{n.content}"""

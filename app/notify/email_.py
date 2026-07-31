"""
邮件推送渠道。

使用 SMTP 发送通知邮件。
"""

import logging
import smtplib
from email.mime.text import MIMEText

from app.notify.base import BaseNotifier, Notification, NotifyLevel

logger = logging.getLogger(__name__)


class EmailNotifier(BaseNotifier):
    """邮件推送通知。

    Usage:
        notifier = EmailNotifier(config)
        notifier.send(Notification("标题", "内容"))
    """

    def __init__(self, config):
        super().__init__(config)
        email_cfg = config.email_config
        self._smtp_host = email_cfg.get("smtp_host", "smtp.qq.com")
        self._smtp_port = email_cfg.get("smtp_port", 587)
        self._username = email_cfg.get("username", "")
        self._password = email_cfg.get("password", "")
        self._recipients = email_cfg.get("recipients", [])

    def send(self, notification: Notification) -> bool:
        """发送邮件通知。

        info 级别在免打扰时段不发送。
        """
        if notification.level == NotifyLevel.INFO and self.is_quiet_time():
            logger.debug("免打扰时段，跳过邮件通知")
            return False

        if not self.check_rate_limit():
            logger.warning("推送频率超限，邮件被丢弃")
            return False

        if not self._username or not self._recipients:
            logger.warning("邮件配置不完整")
            return False

        try:
            html_body = self._format_html(notification)
            msg = MIMEText(html_body, "html", "utf-8")
            msg["Subject"] = f"[{notification.level.upper()}] {notification.title}"
            msg["From"] = self._username
            msg["To"] = ", ".join(self._recipients)

            if self._smtp_port == 465:
                server = smtplib.SMTP_SSL(self._smtp_host, self._smtp_port, timeout=15)
            else:
                server = smtplib.SMTP(self._smtp_host, self._smtp_port, timeout=15)
                server.starttls()
            with server:
                server.login(self._username, self._password)
                server.sendmail(self._username, self._recipients, msg.as_string())

            logger.info(f"邮件发送成功: {notification.title}")
            return True
        except Exception as e:
            logger.error(f"邮件发送失败: {e}")
            return False

    def _format_html(self, n: Notification) -> str:
        """构造 HTML 邮件内容。"""
        level_color = {
            NotifyLevel.INFO: "#2196F3",
            NotifyLevel.WARNING: "#FF9800",
            NotifyLevel.CRITICAL: "#F44336",
        }
        color = level_color.get(n.level, "#333")

        inner = n.content
        if not inner.strip().startswith('<'):
            inner = inner.replace('\n', '<br>')
        return f"""
        <html>
        <body style="font-family: 'Segoe UI', sans-serif; padding: 8px;">
            <div style="max-width: 600px; margin: 0 auto; border: 1px solid #e0e0e0; border-radius: 8px; overflow: hidden;">
                <div style="background: {color}; color: white; padding: 16px 20px;">
                    <h2 style="margin: 0;">{n.title}</h2>
                </div>
                <div style="padding: 20px;">
                    <p style="color: #666; font-size: 13px;">
                        时间: {n.timestamp} &nbsp;|&nbsp; 级别: {n.level.upper()}
                    </p>
                    <div style="border-top: 1px solid #eee; margin: 16px 0;"></div>
                    {inner}
                </div>
                <div style="background: #fafafa; padding: 12px 20px; font-size: 12px; color: #999; text-align: center;">
                    StockMonitor · A股行情监测系统
                </div>
            </div>
        </body>
        </html>
        """

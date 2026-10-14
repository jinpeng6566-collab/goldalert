"""通知渠道。

已内置：控制台、日志文件、仪表盘内嵌、邮件（SMTP）、通用 Webhook。
短信渠道（阿里云 / 腾讯云）在需要时按同一接口接进来即可。
"""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage
from email.utils import formatdate
from typing import Optional

import requests

_LEVELS = {
    "info": logging.INFO,
    "warning": logging.WARNING,
    "critical": logging.CRITICAL,
}


class Notifier:
    """通知渠道基类。"""

    name = "base"

    def send(self, title: str, body: str, level: str = "info") -> bool:  # pragma: no cover
        raise NotImplementedError


class ConsoleNotifier(Notifier):
    """打印到终端。"""

    name = "console"

    def send(self, title: str, body: str, level: str = "info") -> bool:
        icon = {"info": "ℹ", "warning": "⚠", "critical": "🚨"}.get(level, "ℹ")
        print(f"\n{icon} [{level.upper()}] {title}\n   {body}\n", flush=True)
        return True


class LogNotifier(Notifier):
    """写入日志文件。"""

    name = "log"

    def __init__(self, logger: Optional[logging.Logger] = None) -> None:
        self.logger = logger or logging.getLogger("goldalert.notify")

    def send(self, title: str, body: str, level: str = "info") -> bool:
        self.logger.log(_LEVELS.get(level, logging.INFO), "%s | %s", title, body.replace("\n", " "))
        return True


class DashboardNotifier(Notifier):
    """把通知显示在实时仪表盘上，避免与 Live 渲染冲突。"""

    name = "dashboard"

    def __init__(self, dashboard, logger: Optional[logging.Logger] = None) -> None:
        self.dashboard = dashboard
        self.logger = logger or logging.getLogger("goldalert.notify")

    def send(self, title: str, body: str, level: str = "info") -> bool:
        self.logger.log(_LEVELS.get(level, logging.INFO), "%s | %s", title, body.replace("\n", " "))
        self.dashboard.push_event(title, body, level)
        return True


class EmailNotifier(Notifier):
    """通过 SMTP 发送邮件（QQ 邮箱需使用授权码，不是登录密码）。

    邮件走直连，不经过 HTTP 代理——QQ 邮箱等国内 SMTP 服务器直连即可。
    """

    name = "email"

    def __init__(
        self,
        smtp_host: str,
        username: str,
        password: str,
        sender: str = "",
        recipients=None,
        smtp_port: int = 465,
        use_ssl: bool = True,
        use_starttls: bool = False,
        subject_prefix: str = "[GoldAlert 金价提醒]",
        timeout: float = 20.0,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.use_ssl = use_ssl
        self.use_starttls = use_starttls
        self.username = username
        self.password = password
        self.sender = sender or username
        self.recipients = list(recipients or [])
        self.subject_prefix = subject_prefix
        self.timeout = timeout
        self.logger = logger or logging.getLogger("goldalert.notify")

    def build_message(self, title: str, body: str) -> EmailMessage:
        """构造邮件对象（可脱离网络单独测试）。"""
        message = EmailMessage()
        message["Subject"] = f"{self.subject_prefix} {title}".strip()
        message["From"] = self.sender
        message["To"] = ", ".join(self.recipients)
        message["Date"] = formatdate(localtime=True)
        message.set_content(body)
        return message

    def send(self, title: str, body: str, level: str = "info") -> bool:
        if not self.recipients:
            raise ValueError("未配置收件人")
        message = self.build_message(title, body)

        if self.use_ssl:
            server = smtplib.SMTP_SSL(self.smtp_host, self.smtp_port, timeout=self.timeout)
        else:
            server = smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=self.timeout)

        with server:
            server.ehlo()
            if self.use_starttls and not self.use_ssl:
                server.starttls()
                server.ehlo()
            if self.username:
                server.login(self.username, self.password)
            server.send_message(message)

        self.logger.info("邮件已发送给 %s", ", ".join(self.recipients))
        return True


class WebhookNotifier(Notifier):
    """通用 Webhook：把 ``{title, body, level}`` 以 JSON POST 出去。"""

    name = "webhook"

    def __init__(
        self,
        url: str,
        session: requests.Session,
        timeout: float = 10.0,
        proxies: Optional[dict] = None,
    ) -> None:
        self.url = url
        self.session = session
        self.timeout = timeout
        self.proxies = proxies

    def send(self, title: str, body: str, level: str = "info") -> bool:
        resp = self.session.post(
            self.url,
            json={"title": title, "body": body, "level": level},
            timeout=self.timeout,
            proxies=self.proxies,
        )
        resp.raise_for_status()
        return True


class NotifierHub:
    """把同一条通知扇出到多个渠道。"""

    def __init__(self, notifiers=None) -> None:
        self.notifiers: list[Notifier] = list(notifiers or [])
        self.logger = logging.getLogger("goldalert.notify")

    def add(self, notifier: Notifier) -> None:
        self.notifiers.append(notifier)

    def send(self, title: str, body: str, level: str = "info") -> dict:
        results: dict[str, bool] = {}
        for notifier in self.notifiers:
            try:
                results[notifier.name] = notifier.send(title, body, level)
            except Exception as exc:  # 单个渠道失败不影响其他渠道
                results[notifier.name] = False
                self.logger.warning("通知渠道 %s 发送失败: %s", notifier.name, exc)
        return results
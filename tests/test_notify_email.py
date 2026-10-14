"""邮件通知测试（不联网）。"""

from __future__ import annotations

import pytest

import goldalert.notify as notify_module
from goldalert.config import Config, EmailConfig
from goldalert.notify import EmailNotifier, NotifierHub


def make_notifier(**kwargs) -> EmailNotifier:
    defaults = dict(
        smtp_host="smtp.qq.com",
        username="me@qq.com",
        password="授权码占位",
        sender="me@qq.com",
        recipients=["me@qq.com"],
    )
    defaults.update(kwargs)
    return EmailNotifier(**defaults)


def test_message_is_built_with_headers():
    message = make_notifier().build_message("代理已断开", "探测不到代理 7890")
    assert message["To"] == "me@qq.com"
    assert message["From"] == "me@qq.com"
    assert "代理已断开" in message["Subject"]
    assert "GoldAlert" in message["Subject"]
    assert "探测不到代理" in message.get_content()


def test_recipients_may_be_multiple():
    message = make_notifier(recipients=["a@qq.com", "b@qq.com"]).build_message("t", "b")
    assert message["To"] == "a@qq.com, b@qq.com"


def test_send_connects_logs_in_and_sends(monkeypatch):
    calls: dict = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout=None):
            calls["connect"] = (host, port)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def ehlo(self):
            calls["ehlo"] = calls.get("ehlo", 0) + 1

        def login(self, username, password):
            calls["login"] = (username, password)

        def send_message(self, message):
            calls["message"] = message

    monkeypatch.setattr(notify_module.smtplib, "SMTP_SSL", FakeSMTP)

    assert make_notifier().send("代理已断开", "正文") is True
    assert calls["connect"] == ("smtp.qq.com", 465)
    assert calls["login"] == ("me@qq.com", "授权码占位")
    assert calls["message"]["Subject"].endswith("代理已断开")


def test_send_without_recipients_raises():
    with pytest.raises(ValueError):
        make_notifier(recipients=[]).send("t", "b")


def test_hub_tolerates_one_broken_channel(monkeypatch):
    class Broken(EmailNotifier):
        name = "broken"

        def send(self, title, body, level="info"):
            raise RuntimeError("炸了")

    good = make_notifier()
    monkeypatch.setattr(notify_module.smtplib, "SMTP_SSL", lambda *a, **k: _FakeOk())
    results = NotifierHub([Broken(smtp_host="h", username="u", password="p", recipients=["r"]), good]).send("t", "b")
    assert results["broken"] is False
    assert results["email"] is True


class _FakeOk:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def ehlo(self):
        return None

    def login(self, *args):
        return None

    def send_message(self, message):
        return None


def test_email_config_validation():
    assert EmailConfig().problems()
    config = Config(email=EmailConfig.from_dict(
        {"enabled": True, "username": "me@qq.com", "recipients": ["me@qq.com"]}
    ))
    assert config.validate() == []


def test_config_parses_nested_email():
    config = Config.from_dict({"email": {"enabled": True, "recipients": ["x@qq.com"]}})
    assert config.email.enabled is True
    assert config.email.recipients == ("x@qq.com",)
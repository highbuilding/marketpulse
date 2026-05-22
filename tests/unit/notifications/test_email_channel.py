from unittest.mock import MagicMock, patch

import pytest

from core.notifications.email import EmailChannel


def test_disabled_when_env_missing(monkeypatch):
    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.delenv("SMTP_USER", raising=False)
    monkeypatch.delenv("SMTP_PASS", raising=False)
    ch = EmailChannel()
    assert ch.enabled is False


@pytest.mark.asyncio
async def test_disabled_send_is_noop(monkeypatch):
    monkeypatch.delenv("SMTP_HOST", raising=False)
    ch = EmailChannel()
    # 不 raise, 直接安静返回
    await ch.send("a@x.com", "subj", "body")


def test_enabled_when_env_present(monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_PORT", "465")
    monkeypatch.setenv("SMTP_USER", "user@example.com")
    monkeypatch.setenv("SMTP_PASS", "secret")
    ch = EmailChannel()
    assert ch.enabled is True
    assert ch.host == "smtp.example.com"
    assert ch.port == 465
    assert ch.from_addr == "user@example.com"
    assert ch.use_ssl is True


@pytest.mark.asyncio
async def test_send_uses_smtp_ssl(monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_PORT", "465")
    monkeypatch.setenv("SMTP_USER", "user@example.com")
    monkeypatch.setenv("SMTP_PASS", "secret")
    monkeypatch.setenv("SMTP_USE_SSL", "true")

    with patch("core.notifications.email.smtplib.SMTP_SSL") as smtp_cls:
        ctx = MagicMock()
        smtp_cls.return_value.__enter__.return_value = ctx
        ch = EmailChannel()
        await ch.send("recipient@x.com", "subj", "body")

    smtp_cls.assert_called_once()
    ctx.login.assert_called_once_with("user@example.com", "secret")
    ctx.send_message.assert_called_once()
    msg = ctx.send_message.call_args.args[0]
    assert msg["To"] == "recipient@x.com"
    assert msg["Subject"] == "subj"
    assert msg["From"] == "user@example.com"


@pytest.mark.asyncio
async def test_send_uses_starttls_when_ssl_false(monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_USER", "u@x.com")
    monkeypatch.setenv("SMTP_PASS", "p")
    monkeypatch.setenv("SMTP_USE_SSL", "false")

    with patch("core.notifications.email.smtplib.SMTP") as smtp_cls:
        ctx = MagicMock()
        smtp_cls.return_value.__enter__.return_value = ctx
        ch = EmailChannel()
        await ch.send("a@x.com", "s", "b")

    smtp_cls.assert_called_once()
    ctx.starttls.assert_called_once()
    ctx.login.assert_called_once()
    ctx.send_message.assert_called_once()


@pytest.mark.asyncio
async def test_send_with_html_creates_multipart(monkeypatch):
    """传入 html 参数 → 邮件应为 multipart/alternative。"""
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_PORT", "465")
    monkeypatch.setenv("SMTP_USER", "u@x.com")
    monkeypatch.setenv("SMTP_PASS", "p")

    with patch("core.notifications.email.smtplib.SMTP_SSL") as smtp_cls:
        ctx = MagicMock()
        smtp_cls.return_value.__enter__.return_value = ctx
        ch = EmailChannel()
        await ch.send("a@x.com", "subj", "plain body",
                      html="<html><body><h1>hi</h1></body></html>")

    msg = ctx.send_message.call_args.args[0]
    # multipart/alternative 的 content-type 主类型应是 multipart
    assert msg.is_multipart()
    parts = list(msg.iter_parts())
    types = {p.get_content_type() for p in parts}
    assert "text/plain" in types
    assert "text/html" in types


@pytest.mark.asyncio
async def test_send_without_html_plain_only(monkeypatch):
    """不传 html → 只有 text/plain, 不是 multipart。"""
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_PORT", "465")
    monkeypatch.setenv("SMTP_USER", "u@x.com")
    monkeypatch.setenv("SMTP_PASS", "p")

    with patch("core.notifications.email.smtplib.SMTP_SSL") as smtp_cls:
        ctx = MagicMock()
        smtp_cls.return_value.__enter__.return_value = ctx
        ch = EmailChannel()
        await ch.send("a@x.com", "subj", "plain body")

    msg = ctx.send_message.call_args.args[0]
    assert not msg.is_multipart()
    assert msg.get_content_type() == "text/plain"

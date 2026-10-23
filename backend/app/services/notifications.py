"""Alert channels: SMTP (STARTTLS), HMAC-signed webhook, Telegram. Credentials come only from
settings/env (SecretStr); destinations (recipients, URL, chat id) from settings 'alert_channels'."""

from __future__ import annotations

import hashlib
import hmac
import json
import smtplib
import ssl
import time
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Any, Protocol

import anyio
import httpx

from app.core.config import Settings


class ChannelError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class Notification:
    title: str
    text: str
    severity: str
    payload: dict[str, Any]


class Channel(Protocol):
    name: str

    async def send(self, note: Notification, destination: dict[str, Any]) -> None: ...


def sign(secret: str, timestamp: str, body: bytes) -> str:
    mac = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256)
    return "sha256=" + mac.hexdigest()


class WebhookChannel:
    name = "webhook"

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self._secret = settings.webhook_signing_secret
        self._client = client

    async def send(self, note: Notification, destination: dict[str, Any]) -> None:
        url = destination.get("url")
        insecure = not str(url or "").startswith("https://") and not destination.get("allow_http")
        if not url or insecure:
            raise ChannelError("webhook url must be https:// (set allow_http for lab receivers)")
        body = json.dumps(
            {"title": note.title, "text": note.text, "severity": note.severity, **note.payload},
            default=str,
        ).encode()
        ts = str(int(time.time()))
        headers = {"Content-Type": "application/json", "X-NAC-Timestamp": ts}
        if self._secret is not None:
            headers["X-NAC-Signature"] = sign(self._secret.get_secret_value(), ts, body)
        client = self._client or httpx.AsyncClient(timeout=10)
        try:
            resp = await client.post(str(url), content=body, headers=headers)
        finally:
            if self._client is None:
                await client.aclose()
        if resp.status_code >= 300:
            raise ChannelError(f"webhook responded {resp.status_code}")


class TelegramChannel:
    name = "telegram"

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self._token = settings.telegram_bot_token
        self._base = settings.telegram_api_base.rstrip("/")
        self._client = client

    async def send(self, note: Notification, destination: dict[str, Any]) -> None:
        if self._token is None:
            raise ChannelError("TELEGRAM_BOT_TOKEN is not configured")
        chat_id = destination.get("chat_id")
        if not chat_id:
            raise ChannelError("telegram chat_id missing")
        url = f"{self._base}/bot{self._token.get_secret_value()}/sendMessage"
        client = self._client or httpx.AsyncClient(timeout=10)
        try:
            resp = await client.post(url, json={"chat_id": chat_id, "text": note.text})
        finally:
            if self._client is None:
                await client.aclose()
        if resp.status_code >= 300:
            # Never include the URL (contains the bot token) in the error.
            raise ChannelError(f"telegram responded {resp.status_code}")


class EmailChannel:
    name = "email"

    def __init__(self, settings: Settings) -> None:
        self._s = settings

    def _send_sync(self, note: Notification, recipients: list[str]) -> None:
        s = self._s
        if not s.smtp_host or not s.smtp_from:
            raise ChannelError("SMTP is not configured")
        msg = EmailMessage()
        msg["Subject"] = f"[{note.severity.upper()}] {note.title}"
        msg["From"] = s.smtp_from
        msg["To"] = ", ".join(recipients)
        msg.set_content(note.text)
        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=15) as smtp:
            if s.smtp_starttls:
                smtp.starttls(context=ssl.create_default_context())
            if s.smtp_username and s.smtp_password:
                smtp.login(s.smtp_username, s.smtp_password.get_secret_value())
            smtp.send_message(msg)

    async def send(self, note: Notification, destination: dict[str, Any]) -> None:
        recipients = [str(r) for r in destination.get("to", [])]
        if not recipients:
            raise ChannelError("email recipients missing")
        try:
            await anyio.to_thread.run_sync(self._send_sync, note, recipients)
        except (OSError, smtplib.SMTPException) as exc:
            raise ChannelError(f"smtp error: {type(exc).__name__}") from exc


def default_channels(settings: Settings) -> dict[str, Channel]:
    return {
        c.name: c
        for c in (EmailChannel(settings), WebhookChannel(settings), TelegramChannel(settings))
    }

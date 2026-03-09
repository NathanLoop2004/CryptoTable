"""
alertService.py — Multi-channel alert system for the sniper bot.

Sends alerts via Telegram, Discord webhooks, and email.
Manages notification thresholds to avoid spam.
Supports rotating log files for persistent audit trails.

Usage:
    alerts = AlertService(config={
        "telegram_enabled": True,
        "telegram_bot_token": "BOT_TOKEN",
        "telegram_chat_id": "CHAT_ID",
        "discord_enabled": True,
        "discord_webhook_url": "https://discord.com/api/webhooks/...",
        "email_enabled": False,
        "email_smtp_host": "smtp.gmail.com",
        "email_smtp_port": 587,
        "email_from": "bot@example.com",
        "email_to": "user@example.com",
        "email_password": "app_password",
    })
    await alerts.send("Token XYZ es honeypot", level="warning", channel="all")
"""

import asyncio
import json
import logging
import smtplib
import time
from collections import deque
from dataclasses import dataclass, field, asdict
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional
from logging.handlers import RotatingFileHandler
import os

import aiohttp

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
#  Data classes
# ═══════════════════════════════════════════════════════════════

@dataclass
class AlertEvent:
    """A single alert record."""
    timestamp: float = 0
    level: str = "info"              # info | warning | error | critical
    category: str = "general"        # token | rpc | trade | system | rug
    title: str = ""
    message: str = ""
    token: str = ""                  # token address if applicable
    symbol: str = ""
    channels_sent: list = field(default_factory=list)  # telegram, discord, email
    success: bool = True

    def to_dict(self):
        return asdict(self)


# ═══════════════════════════════════════════════════════════════
#  Default configuration
# ═══════════════════════════════════════════════════════════════

DEFAULT_CONFIG = {
    # ── Telegram ──
    "telegram_enabled": False,
    "telegram_bot_token": os.environ.get("SNIPER_TELEGRAM_TOKEN", ""),
    "telegram_chat_id": os.environ.get("SNIPER_TELEGRAM_CHAT_ID", ""),
    # ── Discord ──
    "discord_enabled": False,
    "discord_webhook_url": os.environ.get("SNIPER_DISCORD_WEBHOOK", ""),
    # ── Email ──
    "email_enabled": False,
    "email_smtp_host": "smtp.gmail.com",
    "email_smtp_port": 587,
    "email_from": os.environ.get("SNIPER_EMAIL_FROM", ""),
    "email_to": os.environ.get("SNIPER_EMAIL_TO", ""),
    "email_password": os.environ.get("SNIPER_EMAIL_PASSWORD", ""),
    # ── Throttling ──
    "min_interval_seconds": 5,       # Min time between same-category alerts
    "max_alerts_per_hour": 60,       # Rate limit
    # ── Levels ──
    "min_level_telegram": "warning",  # Minimum level to send via Telegram
    "min_level_discord": "info",
    "min_level_email": "error",
    # ── Log rotation ──
    "log_file": "logs/sniper_alerts.log",
    "log_max_bytes": 5 * 1024 * 1024,  # 5 MB
    "log_backup_count": 5,
}

LEVEL_ORDER = {"info": 0, "warning": 1, "error": 2, "critical": 3}


# ═══════════════════════════════════════════════════════════════
#  Alert Service
# ═══════════════════════════════════════════════════════════════

class AlertService:
    """
    Multi-channel alert service with rate limiting and log rotation.
    """

    def __init__(self, config: dict | None = None):
        self.config = {**DEFAULT_CONFIG, **(config or {})}
        self._history: deque[AlertEvent] = deque(maxlen=500)
        self._last_sent: dict[str, float] = {}  # category → last timestamp
        self._hourly_count = 0
        self._hourly_reset = time.time()
        self._session: aiohttp.ClientSession | None = None

        # Setup rotating file logger
        self._file_logger = self._setup_file_logger()

    def _setup_file_logger(self) -> logging.Logger:
        """Create a separate rotating file logger for alerts."""
        log_path = self.config["log_file"]
        log_dir = os.path.dirname(log_path)
        if log_dir and not os.path.exists(log_dir):
            os.makedirs(log_dir, exist_ok=True)

        flogger = logging.getLogger("sniper.alerts.file")
        flogger.setLevel(logging.DEBUG)
        # Avoid duplicate handlers
        if not flogger.handlers:
            handler = RotatingFileHandler(
                log_path,
                maxBytes=self.config["log_max_bytes"],
                backupCount=self.config["log_backup_count"],
                encoding="utf-8",
            )
            handler.setFormatter(logging.Formatter(
                "%(asctime)s | %(levelname)s | %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            ))
            flogger.addHandler(handler)
        return flogger

    async def _ensure_session(self):
        """Lazy init shared HTTP session."""
        if not self._session or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10)
            )

    # ─── Public API ─────────────────────────────────────

    async def send(
        self,
        message: str,
        level: str = "info",
        category: str = "general",
        title: str = "",
        token: str = "",
        symbol: str = "",
        channels: str = "all",
    ) -> AlertEvent:
        """
        Send an alert to configured channels.

        Args:
            message: Alert body text
            level: info | warning | error | critical
            category: token | rpc | trade | system | rug | general
            title: Short title (auto-generated if empty)
            token: Token address (optional)
            symbol: Token symbol (optional)
            channels: "all" | "telegram" | "discord" | "email" (comma-separated)

        Returns:
            AlertEvent record
        """
        event = AlertEvent(
            timestamp=time.time(),
            level=level.lower(),
            category=category,
            title=title or self._auto_title(level, category),
            message=message,
            token=token,
            symbol=symbol,
        )

        # ── Rate limiting ──
        if not self._check_rate_limit(category):
            event.success = False
            event.channels_sent = ["rate_limited"]
            self._history.append(event)
            return event

        # ── Always log to file ──
        log_msg = f"[{category.upper()}] {event.title}: {message}"
        if token:
            log_msg += f" | token={token} symbol={symbol}"
        getattr(self._file_logger, level.lower(), self._file_logger.info)(log_msg)

        # ── Send to channels ──
        target_channels = [c.strip() for c in channels.split(",")] if channels != "all" else ["telegram", "discord", "email"]
        sent_to = []

        tasks = []
        if "telegram" in target_channels and self.config["telegram_enabled"]:
            if LEVEL_ORDER.get(level, 0) >= LEVEL_ORDER.get(self.config["min_level_telegram"], 1):
                tasks.append(("telegram", self._send_telegram(event)))

        if "discord" in target_channels and self.config["discord_enabled"]:
            if LEVEL_ORDER.get(level, 0) >= LEVEL_ORDER.get(self.config["min_level_discord"], 0):
                tasks.append(("discord", self._send_discord(event)))

        if "email" in target_channels and self.config["email_enabled"]:
            if LEVEL_ORDER.get(level, 0) >= LEVEL_ORDER.get(self.config["min_level_email"], 2):
                tasks.append(("email", self._send_email(event)))

        if tasks:
            results = await asyncio.gather(
                *[t[1] for t in tasks], return_exceptions=True
            )
            for (channel, _), result in zip(tasks, results):
                if isinstance(result, Exception):
                    logger.warning(f"Alert send failed ({channel}): {result}")
                elif result:
                    sent_to.append(channel)

        event.channels_sent = sent_to
        event.success = len(sent_to) > 0 or not tasks
        self._history.append(event)
        return event

    # ─── Convenience Methods ────────────────────────────

    async def alert_token_suspicious(self, token: str, symbol: str, reasons: list[str]):
        """Alert about a suspicious token."""
        msg = f"Token {symbol} ({token[:10]}…) flagged:\n" + "\n".join(f"  • {r}" for r in reasons[:5])
        return await self.send(msg, level="warning", category="token", token=token, symbol=symbol)

    async def alert_rpc_error(self, rpc_url: str, error: str):
        """Alert about RPC failure."""
        msg = f"RPC failed: {rpc_url}\nError: {error[:200]}"
        return await self.send(msg, level="error", category="rpc")

    async def alert_trade_executed(self, symbol: str, action: str, amount: float, tx_hash: str):
        """Alert about a trade execution."""
        msg = f"{action.upper()} {symbol}: {amount} native\nTx: {tx_hash}"
        return await self.send(msg, level="info", category="trade", symbol=symbol)

    async def alert_trade_failed(self, symbol: str, action: str, error: str):
        """Alert about a failed trade."""
        msg = f"{action.upper()} FAILED for {symbol}: {error}"
        return await self.send(msg, level="error", category="trade", symbol=symbol)

    async def alert_rug_detected(self, token: str, symbol: str, message: str, severity: int):
        """Alert about rug pull detection."""
        level = "critical" if severity >= 8 else "error" if severity >= 5 else "warning"
        msg = f"RUG ALERT [{severity}/10]: {symbol} ({token[:10]}…)\n{message}"
        return await self.send(msg, level=level, category="rug", token=token, symbol=symbol)

    async def alert_resource_warning(self, warnings: list[str]):
        """Alert about resource issues."""
        msg = "Resource monitoring:\n" + "\n".join(warnings)
        return await self.send(msg, level="warning", category="system")

    # ─── Telegram ───────────────────────────────────────

    async def _send_telegram(self, event: AlertEvent) -> bool:
        """Send alert to Telegram bot."""
        token = self.config["telegram_bot_token"]
        chat_id = self.config["telegram_chat_id"]
        if not token or not chat_id:
            return False

        await self._ensure_session()
        icon = {"info": "ℹ️", "warning": "⚠️", "error": "❌", "critical": "🚨"}.get(event.level, "📌")
        text = f"{icon} *{event.title}*\n\n{event.message}"
        if event.symbol:
            text += f"\n\n🪙 Token: `{event.symbol}`"
        if event.token:
            text += f"\n📋 `{event.token}`"

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }

        try:
            async with self._session.post(url, json=payload) as resp:
                if resp.status == 200:
                    return True
                body = await resp.text()
                logger.warning(f"Telegram API error {resp.status}: {body[:200]}")
                return False
        except Exception as e:
            logger.warning(f"Telegram send error: {e}")
            return False

    # ─── Discord ────────────────────────────────────────

    async def _send_discord(self, event: AlertEvent) -> bool:
        """Send alert to Discord webhook."""
        webhook_url = self.config["discord_webhook_url"]
        if not webhook_url:
            return False

        await self._ensure_session()
        color_map = {"info": 3447003, "warning": 16776960, "error": 15158332, "critical": 10038562}
        icon = {"info": "ℹ️", "warning": "⚠️", "error": "❌", "critical": "🚨"}.get(event.level, "📌")

        embed = {
            "title": f"{icon} {event.title}",
            "description": event.message,
            "color": color_map.get(event.level, 3447003),
            "footer": {"text": f"TradingWeb Sniper • {event.category}"},
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(event.timestamp)),
        }
        if event.symbol:
            embed["fields"] = [
                {"name": "Token", "value": f"`{event.symbol}`", "inline": True},
            ]
            if event.token:
                embed["fields"].append(
                    {"name": "Address", "value": f"`{event.token[:20]}…`", "inline": True}
                )

        payload = {
            "username": "TradingWeb Sniper",
            "embeds": [embed],
        }

        try:
            async with self._session.post(webhook_url, json=payload) as resp:
                if resp.status in (200, 204):
                    return True
                body = await resp.text()
                logger.warning(f"Discord webhook error {resp.status}: {body[:200]}")
                return False
        except Exception as e:
            logger.warning(f"Discord send error: {e}")
            return False

    # ─── Email ──────────────────────────────────────────

    async def _send_email(self, event: AlertEvent) -> bool:
        """Send alert via email (runs in executor to avoid blocking async)."""
        cfg = self.config
        if not cfg["email_from"] or not cfg["email_to"] or not cfg["email_password"]:
            return False

        loop = asyncio.get_event_loop()
        try:
            return await loop.run_in_executor(None, self._send_email_sync, event)
        except Exception as e:
            logger.warning(f"Email send error: {e}")
            return False

    def _send_email_sync(self, event: AlertEvent) -> bool:
        """Synchronous email sending."""
        cfg = self.config
        icon = {"info": "ℹ️", "warning": "⚠️", "error": "❌", "critical": "🚨"}.get(event.level, "📌")

        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"{icon} Sniper Alert: {event.title}"
        msg["From"] = cfg["email_from"]
        msg["To"] = cfg["email_to"]

        # HTML body
        html = f"""
        <html><body style="font-family:Arial,sans-serif;background:#0B0E11;color:#EAECEF;padding:20px;">
        <div style="max-width:600px;margin:0 auto;background:#1E2329;border-radius:8px;padding:24px;border:1px solid #2B3139;">
            <h2 style="color:#F0B90B;margin:0 0 12px;">{icon} {event.title}</h2>
            <p style="color:#848E9C;font-size:12px;margin:0 0 16px;">
                Category: <strong>{event.category.upper()}</strong> | Level: <strong>{event.level.upper()}</strong>
            </p>
            <div style="background:#2B3139;border-radius:4px;padding:16px;margin:0 0 16px;">
                <pre style="margin:0;white-space:pre-wrap;color:#EAECEF;">{event.message}</pre>
            </div>
            {"<p style='color:#848E9C;font-size:13px;'>Token: <code>" + event.symbol + "</code> (" + event.token[:20] + "…)</p>" if event.token else ""}
            <hr style="border:none;border-top:1px solid #2B3139;margin:16px 0;">
            <p style="color:#5E6673;font-size:11px;margin:0;">TradingWeb Sniper Bot • Automated Alert</p>
        </div>
        </body></html>
        """
        msg.attach(MIMEText(html, "html"))

        try:
            with smtplib.SMTP(cfg["email_smtp_host"], cfg["email_smtp_port"]) as server:
                server.starttls()
                server.login(cfg["email_from"], cfg["email_password"])
                server.sendmail(cfg["email_from"], cfg["email_to"], msg.as_string())
            return True
        except Exception as e:
            logger.warning(f"SMTP error: {e}")
            return False

    # ─── Rate Limiting ──────────────────────────────────

    def _check_rate_limit(self, category: str) -> bool:
        """Check if this alert should be sent (rate limiting)."""
        now = time.time()

        # Reset hourly counter
        if now - self._hourly_reset > 3600:
            self._hourly_count = 0
            self._hourly_reset = now

        # Max per hour
        if self._hourly_count >= self.config["max_alerts_per_hour"]:
            return False

        # Per-category cooldown
        min_interval = self.config["min_interval_seconds"]
        last = self._last_sent.get(category, 0)
        if now - last < min_interval:
            return False

        self._last_sent[category] = now
        self._hourly_count += 1
        return True

    # ─── Helpers ────────────────────────────────────────

    @staticmethod
    def _auto_title(level: str, category: str) -> str:
        """Generate a title from level + category."""
        titles = {
            "token": "Token Alert",
            "rpc": "RPC Alert",
            "trade": "Trade Alert",
            "system": "System Alert",
            "rug": "Rug Pull Alert",
            "general": "Sniper Alert",
        }
        prefix = {"info": "Info", "warning": "Warning", "error": "Error", "critical": "CRITICAL"}.get(level, "Alert")
        return f"{prefix}: {titles.get(category, 'Sniper Alert')}"

    def get_history(self, limit: int = 50) -> list[dict]:
        """Return recent alert history."""
        return [e.to_dict() for e in list(self._history)[-limit:]]

    def get_stats(self) -> dict:
        """Summary stats."""
        now = time.time()
        recent = [e for e in self._history if now - e.timestamp < 3600]
        return {
            "total_alerts": len(self._history),
            "alerts_last_hour": len(recent),
            "alerts_by_level": {
                "info": sum(1 for e in recent if e.level == "info"),
                "warning": sum(1 for e in recent if e.level == "warning"),
                "error": sum(1 for e in recent if e.level == "error"),
                "critical": sum(1 for e in recent if e.level == "critical"),
            },
            "channels": {
                "telegram": self.config["telegram_enabled"],
                "discord": self.config["discord_enabled"],
                "email": self.config["email_enabled"],
            },
        }

    def update_config(self, new_config: dict):
        """Update alert configuration."""
        for k, v in new_config.items():
            if k in self.config:
                self.config[k] = v
        logger.info(f"Alert config updated: {list(new_config.keys())}")

    async def close(self):
        """Cleanup HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()

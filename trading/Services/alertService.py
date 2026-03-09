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
    "discord_enabled": bool(os.environ.get("SNIPER_DISCORD_WEBHOOK", "")),
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

        # ── Rate limiting (use category:token for per-token cooldowns) ──
        rate_key = f"{category}:{token}" if token else category
        if not self._check_rate_limit(rate_key):
            event.success = False
            event.channels_sent = ["rate_limited"]
            self._history.append(event)
            logger.info(f"[ALERT-SEND] Rate-limited: {rate_key}")
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
        else:
            if "discord" in target_channels:
                logger.info(f"[ALERT-SEND] Discord skipped: enabled={self.config['discord_enabled']}, "
                            f"webhook={'SET' if self.config.get('discord_webhook_url') else 'EMPTY'}")

        if "email" in target_channels and self.config["email_enabled"]:
            if LEVEL_ORDER.get(level, 0) >= LEVEL_ORDER.get(self.config["min_level_email"], 2):
                tasks.append(("email", self._send_email(event)))

        logger.info(f"[ALERT-SEND] '{event.title}' level={level} → tasks={[t[0] for t in tasks]}")

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
        if isinstance(reasons, str):
            reasons = [reasons]
        msg = f"⚠️ Token **{symbol}** (`{token[:16]}…`) flagged:\n" + "\n".join(f"  • {r}" for r in reasons[:5])
        return await self.send(msg, level="warning", category="token", token=token, symbol=symbol)

    async def alert_rpc_error(self, rpc_url: str, error: str):
        """Alert about RPC failure."""
        msg = f"RPC failed: {rpc_url}\nError: {error[:200]}"
        return await self.send(msg, level="error", category="rpc")

    async def alert_trade_executed(self, token: str, symbol: str, action: str,
                                   amount: float, tx_hash: str, extra: dict = None):
        """Alert about a trade execution."""
        msg = f"{'🟢 BUY' if action == 'buy' else '🔴 SELL'} **{symbol}**: {amount} BNB"
        if tx_hash:
            msg += f"\n🔗 Tx: `{tx_hash[:20]}…`"
        if extra:
            if extra.get("mev_protected"):
                msg += f"\n🛡️ MEV protected ({extra.get('mev_strategy', 'unknown')})"
            if extra.get("risk_engine_score"):
                msg += f"\n📊 Risk score: {extra['risk_engine_score']}/100"
        return await self.send(msg, level="info", category="trade", token=token, symbol=symbol)

    async def alert_trade_failed(self, token: str, symbol: str, action: str, error: str):
        """Alert about a failed trade."""
        msg = f"❌ {action.upper()} FAILED for **{symbol}** (`{token[:16]}…`)\n{error}"
        return await self.send(msg, level="error", category="trade", token=token, symbol=symbol)

    async def alert_rug_detected(self, token: str, symbol: str, message: str, severity: int):
        """Alert about rug pull detection."""
        level = "critical" if severity >= 8 else "error" if severity >= 5 else "warning"
        bars = "🔴" * min(severity, 10) + "⚪" * max(0, 10 - severity)
        msg = f"🚨 **RUG ALERT** [{severity}/10] {bars}\n**{symbol}** (`{token[:16]}…`)\n{message}"
        return await self.send(msg, level=level, category="rug", token=token, symbol=symbol)

    async def alert_resource_warning(self, warnings: list[str]):
        """Alert about resource issues."""
        msg = "Resource monitoring:\n" + "\n".join(warnings)
        return await self.send(msg, level="warning", category="system")

    # ─── NEW: Rich token analysis alerts ────────────────

    async def alert_token_analysis(self, token: str, symbol: str, name: str,
                                   risk: str, liquidity_usd: float,
                                   passes_safety: bool,
                                   rejection_reasons: list[str] | None = None,
                                   token_info=None):
        """
        Single comprehensive alert for every analyzed token.
        Combines detection, verdict, rejection reasons, and
        suspicious flags into ONE rich Discord embed.
        """
        risk_emoji = {"safe": "🟢", "warning": "🟡", "danger": "🔴"}.get(risk, "⚪")
        is_honeypot = getattr(token_info, "is_honeypot", False) if token_info else False

        # ── VERDICT header ──
        if passes_safety:
            verdict = "✅ **APTO PARA COMPRA**"
            level = "info"
        elif is_honeypot:
            verdict = "🍯 **HONEYPOT — NO COMPRAR**"
            level = "error"
        else:
            verdict = "🚫 **RECHAZADO — NO COMPRAR**"
            level = "warning"

        # ── BscScan / DexScreener links ──
        bsc_link = f"[BscScan](https://bscscan.com/token/{token})"
        dex_link = f"[DexScreener](https://dexscreener.com/bsc/{token})"

        msg = (
            f"{verdict}\n\n"
            f"**{symbol}** ({name})\n"
            f"📋 `{token}`\n"
            f"🔗 {bsc_link} | {dex_link}\n\n"
            f"{risk_emoji} Riesgo: **{risk.upper()}**\n"
            f"💰 Liquidez: **${liquidity_usd:,.2f}**"
        )

        # ── Token details ──
        details = []
        if token_info:
            buy_tax = getattr(token_info, "buy_tax", 0) or 0
            sell_tax = getattr(token_info, "sell_tax", 0) or 0
            details.append(f"📊 Impuestos: Buy {buy_tax}% / Sell {sell_tax}%")

            if is_honeypot:
                details.append("🍯 **HONEYPOT — compra/venta imposible**")

            hp_sim = getattr(token_info, "swap_sim_honeypot", None)
            if hp_sim:
                details.append("🧪 Swap sim: Honeypot confirmado")
            elif hp_sim is False:
                can_buy = "✓" if getattr(token_info, "swap_sim_can_buy", False) else "✗"
                can_sell = "✓" if getattr(token_info, "swap_sim_can_sell", False) else "✗"
                details.append(f"🧪 Swap sim: Buy {can_buy} / Sell {can_sell}")

            lp_locked = getattr(token_info, "lp_locked", False)
            lp_pct = getattr(token_info, "lp_lock_percent", 0) or 0
            details.append(f"🔒 LP Lock: {'SÍ' if lp_locked else 'NO'} ({lp_pct:.0f}%)")

            # Risk engine
            if getattr(token_info, "_risk_engine_ok", False):
                score = getattr(token_info, "risk_engine_score", 0)
                action = getattr(token_info, "risk_engine_action", "?")
                hard_stop = getattr(token_info, "risk_engine_hard_stop", False)
                details.append(f"🎯 Risk Engine: **{score}/100** → {action}"
                               + (" ⛔ HARD STOP" if hard_stop else ""))

            # Pump score
            pump = getattr(token_info, "pump_score", 0) or 0
            if pump > 0:
                grade = getattr(token_info, "pump_grade", "?")
                details.append(f"🚀 Pump Score: **{pump}/100** ({grade})")

            # ML prediction
            if getattr(token_info, "_ml_pump_ok", False):
                ml_score = getattr(token_info, "ml_pump_score", 0)
                ml_label = getattr(token_info, "ml_pump_label", "?")
                details.append(f"🧠 ML Predicción: {ml_score}/100 ({ml_label})")

            # v7 modules
            if getattr(token_info, "_rl_ok", False):
                details.append(f"🤖 RL: {token_info.rl_decision} ({token_info.rl_confidence:.0%})")
            if getattr(token_info, "_orderflow_ok", False):
                details.append(f"📈 Orderflow: organic {token_info.orderflow_organic_score:.0f}%")
            if getattr(token_info, "_sim_v7_ok", False):
                details.append(f"🧪 Simulator: risk {token_info.sim_v7_risk_score:.0f} → {token_info.sim_v7_recommendation}")
            if getattr(token_info, "_whale_network_ok", False):
                details.append(f"🐋 Whale sybil: {token_info.whale_network_sybil_risk:.0%}")

            # Dev tracker
            dev_score = getattr(token_info, "dev_score", 0) or 0
            if dev_score > 0:
                dev_label = getattr(token_info, "dev_label", "?")
                details.append(f"👨‍💻 Dev: {dev_score}/100 ({dev_label})")

        if details:
            msg += "\n\n" + "\n".join(f"  {d}" for d in details)

        # ── Rejection reasons ──
        if not passes_safety and rejection_reasons:
            msg += "\n\n🚩 **Razones de rechazo:**\n"
            msg += "\n".join(f"  ❌ {r}" for r in rejection_reasons[:10])

        return await self.send(
            msg, level=level, category="token_analysis", token=token, symbol=symbol,
            title=f"{risk_emoji} {symbol} — {verdict.split('**')[1] if '**' in verdict else 'ANALIZADO'}"
        )

    async def alert_token_detected(self, token: str, symbol: str, name: str,
                                   risk: str, liquidity_usd: float, token_info=None):
        """
        Rich alert when a new token is detected and analyzed.
        Sends a comprehensive embed with analysis results.
        """
        risk_emoji = {"safe": "🟢", "warning": "🟡", "danger": "🔴"}.get(risk, "⚪")
        msg = (
            f"🔍 New token detected: **{symbol}** ({name})\n"
            f"{risk_emoji} Risk: **{risk.upper()}**\n"
            f"💰 Liquidity: **${liquidity_usd:,.0f}**"
        )
        details = []
        if token_info:
            # Core metrics
            if getattr(token_info, "buy_tax", 0):
                details.append(f"Buy tax: {token_info.buy_tax}%")
            if getattr(token_info, "sell_tax", 0):
                details.append(f"Sell tax: {token_info.sell_tax}%")
            if getattr(token_info, "is_honeypot", False):
                details.append("⚠️ HONEYPOT")
            if getattr(token_info, "lp_locked", False):
                details.append(f"LP locked: {getattr(token_info, 'lp_lock_percent', 0)}%")
            # Risk engine
            if getattr(token_info, "_risk_engine_ok", False):
                details.append(f"Risk score: {token_info.risk_engine_score}/100 ({token_info.risk_engine_action})")
            # Pump score
            if getattr(token_info, "pump_score", 0) > 0:
                details.append(f"Pump score: {token_info.pump_score}/100 ({getattr(token_info, 'pump_grade', '?')})")
            # ML prediction
            if getattr(token_info, "_ml_pump_ok", False):
                details.append(f"ML: {token_info.ml_pump_score}/100 ({getattr(token_info, 'ml_pump_label', '?')})")
            # v7 modules
            if getattr(token_info, "_rl_ok", False):
                details.append(f"🤖 RL: {token_info.rl_decision} ({token_info.rl_confidence:.0%})")
            if getattr(token_info, "_orderflow_ok", False):
                details.append(f"📊 Orderflow: organic {token_info.orderflow_organic_score:.0f}%")
            if getattr(token_info, "_sim_v7_ok", False):
                details.append(f"🧪 Simulator: risk {token_info.sim_v7_risk_score:.0f} → {token_info.sim_v7_recommendation}")
            if getattr(token_info, "_whale_network_ok", False):
                details.append(f"🐋 Whale sybil: {token_info.whale_network_sybil_risk:.0%}")
        if details:
            msg += "\n\n" + "\n".join(f"  • {d}" for d in details)

        return await self.send(
            msg, level="info", category="token", token=token, symbol=symbol,
            title=f"🔍 {symbol} detected — {risk.upper()}"
        )

    async def alert_token_rejected(self, token: str, symbol: str, reasons: list[str]):
        """Alert when a token is rejected with specific reasons."""
        if isinstance(reasons, str):
            reasons = [reasons]
        msg = (
            f"🚫 **{symbol}** (`{token[:16]}…`) **REJECTED**\n"
            + "\n".join(f"  ❌ {r}" for r in reasons[:8])
        )
        return await self.send(
            msg, level="warning", category="token", token=token, symbol=symbol,
            title=f"🚫 {symbol} rejected"
        )

    async def alert_snipe_opportunity(self, token: str, symbol: str, risk: str,
                                      liquidity_usd: float, risk_score: int = 0,
                                      risk_action: str = "", auto_buy: bool = False):
        """Alert when a safe token passes all checks — buy opportunity."""
        risk_emoji = {"safe": "🟢", "warning": "🟡"}.get(risk, "🟢")
        msg = (
            f"🎯 **SNIPE OPPORTUNITY: {symbol}**\n"
            f"{risk_emoji} Risk: **{risk.upper()}**\n"
            f"💰 Liquidity: **${liquidity_usd:,.0f}**\n"
        )
        if risk_score:
            msg += f"📊 Risk score: **{risk_score}/100** ({risk_action})\n"
        if auto_buy:
            msg += "⚡ Auto-buy: **ENABLED** — executing trade…\n"
        else:
            msg += "👆 Auto-buy: OFF — manual confirmation needed\n"
        return await self.send(
            msg, level="info", category="trade", token=token, symbol=symbol,
            title=f"🎯 SNIPE: {symbol}"
        )

    async def send_test_webhook(self) -> dict:
        """
        Send a test alert to all enabled channels.
        Returns dict with results per channel.
        """
        import datetime
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        results = {}

        if self.config["discord_enabled"] and self.config.get("discord_webhook_url"):
            await self._ensure_session()
            embed = {
                "title": "✅ Discord Webhook Connected",
                "description": (
                    "Tu webhook de Discord está funcionando correctamente.\n"
                    "Recibirás alertas de:\n\n"
                    "🔍 **Tokens detectados** — análisis completo\n"
                    "🎯 **Oportunidades de snipe** — tokens que pasan todos los filtros\n"
                    "🚫 **Tokens rechazados** — y por qué fueron bloqueados\n"
                    "💰 **Trades ejecutados** — compras y ventas automáticas\n"
                    "🚨 **Rug pulls detectados** — alertas de emergencia\n"
                    "🐋 **Whale alerts** — movimientos de ballenas\n"
                    "🤖 **RL decisions** — decisiones del agente de IA\n"
                ),
                "color": 3066993,  # green
                "fields": [
                    {"name": "Estado", "value": "🟢 Conectado", "inline": True},
                    {"name": "Hora", "value": now, "inline": True},
                    {"name": "Canales activos", "value": (
                        ("✅ Discord " if self.config["discord_enabled"] else "❌ Discord ") +
                        ("✅ Telegram " if self.config["telegram_enabled"] else "❌ Telegram ") +
                        ("✅ Email" if self.config["email_enabled"] else "❌ Email")
                    ), "inline": False},
                ],
                "footer": {"text": "TradingWeb Sniper Bot • Alertas en tiempo real"},
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            payload = {
                "username": "TradingWeb Sniper",
                "avatar_url": "https://i.imgur.com/4M34hi2.png",
                "embeds": [embed],
            }
            try:
                async with self._session.post(self.config["discord_webhook_url"], json=payload) as resp:
                    results["discord"] = resp.status in (200, 204)
                    if resp.status not in (200, 204):
                        body = await resp.text()
                        results["discord_error"] = body[:200]
            except Exception as e:
                results["discord"] = False
                results["discord_error"] = str(e)[:200]
        else:
            results["discord"] = None
            results["discord_reason"] = "disabled or no webhook URL"

        if self.config["telegram_enabled"] and self.config.get("telegram_bot_token"):
            event = AlertEvent(
                timestamp=time.time(), level="info", category="system",
                title="✅ Test — Conexión OK",
                message="Webhook de Telegram conectado. Recibirás alertas del Sniper Bot.",
            )
            try:
                results["telegram"] = await self._send_telegram(event)
            except Exception as e:
                results["telegram"] = False
                results["telegram_error"] = str(e)[:200]
        else:
            results["telegram"] = None
            results["telegram_reason"] = "disabled or no token"

        return results

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
            logger.info("[DISCORD] No webhook URL configured")
            return False

        await self._ensure_session()
        color_map = {
            "info": 3066993,       # green
            "warning": 16776960,   # yellow
            "error": 15158332,     # red
            "critical": 10038562,  # dark red
        }
        icon = {"info": "ℹ️", "warning": "⚠️", "error": "❌", "critical": "🚨"}.get(event.level, "📌")

        # Truncate description to Discord's 4096 char limit
        description = event.message[:4090] + "…" if len(event.message) > 4096 else event.message

        embed = {
            "title": f"{icon} {event.title}"[:256],
            "description": description,
            "color": color_map.get(event.level, 3066993),
            "footer": {"text": f"TradingWeb Sniper • {event.category}"},
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(event.timestamp)),
        }
        # Only add token field for non-analysis alerts (analysis already has full info in description)
        if event.symbol and event.category != "token_analysis":
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
                    logger.info(f"[DISCORD] Sent: {event.title}")
                    return True
                body = await resp.text()
                logger.warning(f"[DISCORD] webhook error {resp.status}: {body[:200]}")
                return False
        except Exception as e:
            logger.warning(f"[DISCORD] send error: {e}")
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

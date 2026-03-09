"""
sniperConsumer.py — WebSocket consumer for the Sniper Bot.
Bridges the SniperBot engine with the frontend via Django Channels.

Client messages:
  { "action": "start", "chain_id": 56 }
  { "action": "stop" }
  { "action": "get_state" }
  { "action": "update_settings", "settings": { ... } }
  { "action": "register_snipe", "token": "0x...", "symbol": "X", "buy_price": 1.2, "amount": 0.05, "tx": "0x..." }
"""

import asyncio
import json
import logging

from channels.generic.websocket import AsyncWebsocketConsumer

from trading.Services.sniperService import SniperBot

logger = logging.getLogger(__name__)


class SniperConsumer(AsyncWebsocketConsumer):
    """
    Per-connection sniper bot instance.
    Each client gets their own SniperBot engine.
    """

    async def connect(self):
        await self.accept()
        self.bot: SniperBot | None = None
        self._bot_task: asyncio.Task | None = None
        logger.info("Sniper WS connected")

    async def disconnect(self, close_code):
        if self.bot:
            self.bot.stop()
        if self._bot_task and not self._bot_task.done():
            self._bot_task.cancel()
        logger.info("Sniper WS disconnected")

    async def receive(self, text_data=None, bytes_data=None):
        if not text_data:
            return

        try:
            msg = json.loads(text_data)
        except json.JSONDecodeError:
            await self._send_event("error", {"message": "Invalid JSON"})
            return

        action = msg.get("action", "")

        if action == "start":
            await self._start_bot(msg.get("chain_id", 56))

        elif action == "stop":
            await self._stop_bot()

        elif action == "get_state":
            await self._send_state()

        elif action == "update_settings":
            settings = msg.get("settings", {})
            if self.bot:
                self.bot.update_settings(settings)
                await self._send_event("settings_updated", self.bot.settings)
            else:
                await self._send_event("error", {"message": "Bot not running"})

        elif action == "register_snipe":
            if self.bot:
                snipe = self.bot.add_manual_snipe(
                    token_address=msg.get("token", ""),
                    symbol=msg.get("symbol", "?"),
                    buy_price=float(msg.get("buy_price", 0)),
                    amount_native=float(msg.get("amount", 0)),
                    tx_hash=msg.get("tx", ""),
                    auto_hold_hours=float(msg.get("auto_hold_hours", 0)),
                )
                await self._send_event("snipe_registered", snipe)
            else:
                await self._send_event("error", {"message": "Bot not running"})

        elif action == "mark_snipe_sold":
            if self.bot:
                token_addr = msg.get("token", "")
                sell_tx    = msg.get("sell_tx", "")
                sell_price = float(msg.get("sell_price", 0))
                sold = self.bot.mark_snipe_sold(token_addr, sell_tx, sell_price)
                if sold:
                    await self._send_event("snipe_sold", sold)
                else:
                    await self._send_event("error", {"message": "Position not found"})
            else:
                await self._send_event("error", {"message": "Bot not running"})

        elif action == "get_dashboard":
            if self.bot:
                dashboard = self.bot.metrics_service.get_dashboard()
                await self._send_event("dashboard", dashboard)
            else:
                await self._send_event("error", {"message": "Bot not running"})

        elif action == "update_alert_config":
            if self.bot:
                config = msg.get("config", {})
                self.bot.alert_service.update_config(config)
                await self._send_event("alert_config_updated", self.bot.alert_service.config)
            else:
                await self._send_event("error", {"message": "Bot not running"})

        else:
            await self._send_event("error", {"message": f"Unknown action: {action}"})

    # ─── Bot lifecycle ──────────────────────────────────────

    async def _start_bot(self, chain_id: int):
        """Create and start a SniperBot instance."""
        # Stop existing bot if running
        if self.bot and self.bot.running:
            self.bot.stop()
            if self._bot_task and not self._bot_task.done():
                self._bot_task.cancel()
                try:
                    await self._bot_task
                except asyncio.CancelledError:
                    pass

        self.bot = SniperBot(chain_id=int(chain_id))
        self.bot.set_event_callback(self._on_bot_event)

        # Run bot in background task
        self._bot_task = asyncio.create_task(self._run_bot())
        await self._send_event("bot_started", {"chain_id": chain_id})

    async def _run_bot(self):
        """Wrapper to run the bot and catch errors."""
        try:
            await self.bot.run()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Sniper bot crashed: {e}")
            await self._send_event("error", {"message": f"Bot crashed: {str(e)[:200]}"})

    async def _stop_bot(self):
        if self.bot:
            self.bot.stop()
        await self._send_event("bot_stopped", {})

    # ─── Event handling ─────────────────────────────────────

    async def _on_bot_event(self, event: dict):
        """Callback from SniperBot — forward to WS client."""
        await self.send(text_data=json.dumps(event, default=str))

    async def _send_event(self, event_type: str, data: dict):
        await self.send(text_data=json.dumps({
            "type": event_type,
            "data": data,
        }, default=str))

    async def _send_state(self):
        if self.bot:
            state = self.bot.get_state()
            await self._send_event("full_state", state)
        else:
            await self._send_event("full_state", {
                "running": False,
                "chain_id": 56,
                "settings": {},
                "detected_pairs": [],
                "active_snipes": [],
                "recent_events": [],
            })

"""
walletConsumer.py - WebSocket consumer para eventos de wallet.
Trust Wallet / WalletConnect / MetaMask en tiempo real.

Actions:
  - connect_wallet    { address, provider, chain_id }
  - disconnect_wallet { address }
  - switch_chain      { address, chain_id }

Usage from frontend:
  const ws = new WebSocket("ws://localhost:8000/ws/wallet/");
  ws.onopen = () => {
      ws.send(JSON.stringify({
          "action": "connect_wallet",
          "address": "0x…",
          "provider": "trust_wallet",
          "chain_id": 56
      }));
  };
"""

import json
import logging

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer

from trading.Services import walletService

logger = logging.getLogger(__name__)


class WalletConsumer(AsyncWebsocketConsumer):
    """
    Real-time WebSocket channel for wallet events.

    After the user authenticates via the REST endpoints
    (nonce → sign → verify), they open this socket to:
      - Notify the backend of wallet connection / disconnection.
      - Receive server-pushed events (balance updates, tx confirmations…).
      - Switch chains dynamically.

    The consumer also joins a Channels *group* per address so the
    backend can push messages from anywhere (e.g. a Celery task).
    """

    async def connect(self):
        await self.accept()
        self._wallet_address: str | None = None
        self._group_name: str | None = None
        logger.info("Wallet WS client connected.")

    async def disconnect(self, close_code):
        # Mark wallet as disconnected in DB
        if self._wallet_address:
            await database_sync_to_async(walletService.ws_set_active)(
                self._wallet_address, False
            )
            if self._group_name:
                await self.channel_layer.group_discard(
                    self._group_name, self.channel_name
                )
        logger.info("Wallet WS client disconnected (code=%s).", close_code)

    # ------------------------------------------------------------------ #
    #  Messages from the frontend
    # ------------------------------------------------------------------ #

    async def receive(self, text_data=None, bytes_data=None):
        try:
            payload = json.loads(text_data)
        except (json.JSONDecodeError, TypeError):
            await self._send_error("Invalid JSON")
            return

        action = payload.get("action")
        handler = {
            "connect_wallet": self._handle_connect,
            "disconnect_wallet": self._handle_disconnect,
            "switch_chain": self._handle_switch_chain,
        }.get(action)

        if handler:
            await handler(payload)
        else:
            await self._send_error(f"Unknown action: {action}")

    # ------------------------------------------------------------------ #
    #  Action handlers
    # ------------------------------------------------------------------ #

    async def _handle_connect(self, payload: dict):
        address = (payload.get("address") or "").strip()
        provider = payload.get("provider", "trust_wallet")
        chain_id = payload.get("chain_id", 1)

        if not address:
            await self._send_error("address is required.")
            return

        self._wallet_address = address.lower()
        self._group_name = f"wallet_{self._wallet_address}"

        # Join channel group for this wallet
        await self.channel_layer.group_add(
            self._group_name, self.channel_name
        )

        # Persist state via Service
        await database_sync_to_async(walletService.ws_connect_wallet)(
            address, provider, chain_id
        )

        await self.send(text_data=json.dumps({
            "event": "wallet_connected",
            "address": address,
            "provider": provider,
            "chain_id": chain_id,
        }))
        logger.info("Wallet WS connected: %s (%s chain=%s)", address, provider, chain_id)

    async def _handle_disconnect(self, payload: dict):
        address = (payload.get("address") or "").strip()
        if not address:
            address = self._wallet_address or ""

        if address:
            await database_sync_to_async(walletService.ws_set_active)(address, False)

        if self._group_name:
            await self.channel_layer.group_discard(
                self._group_name, self.channel_name
            )

        self._wallet_address = None
        self._group_name = None

        await self.send(text_data=json.dumps({
            "event": "wallet_disconnected",
            "address": address,
        }))
        logger.info("Wallet WS disconnected: %s", address)

    async def _handle_switch_chain(self, payload: dict):
        chain_id = payload.get("chain_id")
        address = (payload.get("address") or self._wallet_address or "").strip()

        if not chain_id:
            await self._send_error("chain_id is required.")
            return

        await database_sync_to_async(walletService.ws_switch_chain)(
            address, int(chain_id)
        )

        await self.send(text_data=json.dumps({
            "event": "chain_switched",
            "address": address,
            "chain_id": chain_id,
        }))
        logger.info("Wallet %s switched to chain %s", address, chain_id)

    # ------------------------------------------------------------------ #
    #  Group message handler (server → client push)
    # ------------------------------------------------------------------ #

    async def wallet_event(self, event: dict):
        """Called when the backend sends a message to the wallet group."""
        await self.send(text_data=json.dumps(event.get("data", event)))

    # ------------------------------------------------------------------ #
    #  Utility
    # ------------------------------------------------------------------ #

    async def _send_error(self, msg: str):
        await self.send(text_data=json.dumps({"error": msg}))

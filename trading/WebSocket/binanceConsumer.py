"""
binanceConsumer.py - WebSocket consumer para Binance market data.
Se conecta al WebSocket de Binance y retransmite datos en tiempo real.

Supported streams:
  - Trade streams:      symbol@trade          (e.g. btcusdt@trade)
  - Kline streams:      symbol@kline_interval (e.g. btcusdt@kline_1m)
  - Mini ticker:        symbol@miniTicker
  - Ticker:             symbol@ticker
  - All market tickers: !ticker@arr
  - Depth:              symbol@depth@100ms

Usage from frontend:
  const ws = new WebSocket("ws://localhost:8000/ws/binance/");
  ws.onopen = () => {
      ws.send(JSON.stringify({
          "action": "subscribe",
          "streams": ["btcusdt@trade", "ethusdt@kline_1m"]
      }));
  };
"""

import asyncio
import json
import logging

import websockets
from channels.generic.websocket import AsyncWebsocketConsumer

logger = logging.getLogger(__name__)

BINANCE_WS_BASE = "wss://stream.binance.com:9443/ws"


class BinanceConsumer(AsyncWebsocketConsumer):
    """
    Bridges a client WebSocket with Binance's streaming API.

    - On connect: accepts the client and opens a connection to Binance.
    - Client can send subscribe / unsubscribe messages dynamically.
    - Binance data is forwarded to the client in real-time.
    """

    # ------------------------------------------------------------------ #
    #  Lifecycle
    # ------------------------------------------------------------------ #

    async def connect(self):
        await self.accept()
        self.binance_ws = None
        self._binance_task = None
        self._subscribed_streams: set[str] = set()
        logger.info("Client connected – ready to receive stream requests.")

    async def disconnect(self, close_code):
        await self._close_binance()
        logger.info("Client disconnected (code=%s).", close_code)

    # ------------------------------------------------------------------ #
    #  Messages from the frontend
    # ------------------------------------------------------------------ #

    async def receive(self, text_data=None, bytes_data=None):
        """Handle subscribe / unsubscribe commands from the client."""
        try:
            payload = json.loads(text_data)
        except (json.JSONDecodeError, TypeError):
            await self.send(text_data=json.dumps({"error": "Invalid JSON"}))
            return

        action = payload.get("action")
        streams = payload.get("streams", [])

        if not isinstance(streams, list) or not streams:
            await self.send(text_data=json.dumps(
                {"error": "Provide a non-empty 'streams' list."}
            ))
            return

        if action == "subscribe":
            await self._subscribe(streams)
        elif action == "unsubscribe":
            await self._unsubscribe(streams)
        else:
            await self.send(text_data=json.dumps(
                {"error": f"Unknown action: {action}"}
            ))

    # ------------------------------------------------------------------ #
    #  Binance stream management
    # ------------------------------------------------------------------ #

    async def _subscribe(self, streams: list[str]):
        """Subscribe to one or more Binance streams."""
        new_streams = [s.lower() for s in streams if s.lower() not in self._subscribed_streams]
        if not new_streams:
            await self.send(text_data=json.dumps(
                {"info": "Already subscribed to all requested streams."}
            ))
            return

        self._subscribed_streams.update(new_streams)

        # (Re)open connection with the full combined stream URL
        await self._reconnect_binance()

        await self.send(text_data=json.dumps({
            "subscribed": sorted(self._subscribed_streams),
        }))

    async def _unsubscribe(self, streams: list[str]):
        """Unsubscribe from one or more Binance streams."""
        removed = []
        for s in streams:
            key = s.lower()
            if key in self._subscribed_streams:
                self._subscribed_streams.discard(key)
                removed.append(key)

        if not self._subscribed_streams:
            await self._close_binance()
        else:
            await self._reconnect_binance()

        await self.send(text_data=json.dumps({
            "unsubscribed": removed,
            "active_streams": sorted(self._subscribed_streams),
        }))

    # ------------------------------------------------------------------ #
    #  Internal helpers
    # ------------------------------------------------------------------ #

    async def _reconnect_binance(self):
        """Close existing Binance socket (if any) and open a new one
        with the current set of subscribed streams."""
        await self._close_binance()

        if not self._subscribed_streams:
            return

        # Binance combined streams URL
        combined = "/".join(sorted(self._subscribed_streams))
        url = f"wss://stream.binance.com:9443/stream?streams={combined}"

        logger.info("Connecting to Binance: %s", url)
        try:
            self.binance_ws = await websockets.connect(url)
            self._binance_task = asyncio.create_task(self._relay_binance())
        except Exception as exc:
            logger.error("Failed to connect to Binance: %s", exc)
            await self.send(text_data=json.dumps(
                {"error": f"Binance connection failed: {exc}"}
            ))

    async def _relay_binance(self):
        """Read messages from Binance and forward them to the client."""
        try:
            async for message in self.binance_ws:
                await self.send(text_data=message)
        except websockets.ConnectionClosed:
            logger.warning("Binance WebSocket closed unexpectedly – reconnecting.")
            await self._reconnect_binance()
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error("Error relaying Binance data: %s", exc)

    async def _close_binance(self):
        """Cleanly close the Binance WebSocket and cancel the relay task."""
        if self._binance_task and not self._binance_task.done():
            self._binance_task.cancel()
            try:
                await self._binance_task
            except asyncio.CancelledError:
                pass
            self._binance_task = None

        if self.binance_ws:
            await self.binance_ws.close()
            self.binance_ws = None

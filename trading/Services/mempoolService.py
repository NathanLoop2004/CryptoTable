"""
mempoolService.py — Mempool Listener for Early Token Detection.

Monitors pending (unconfirmed) transactions via WebSocket to detect:
  - PairCreated events before they're mined (10–30s advantage)
  - addLiquidity calls to the DEX router
  - Large approve calls to router (pre-launch signal)
  - Contract creation transactions

This gives the sniper bot a significant time advantage over block-based
detection, allowing analysis to begin before the pair is even confirmed.

Integration:
  Runs as a parallel async task alongside the main block scanner.
  Emits events through the SniperBot._emit() callback.

Note: Not all public WebSocket endpoints support eth_subscribe("pendingTransactions").
      The service gracefully degrades if mempool access is unavailable.
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, asdict
from typing import Optional, Callable, Awaitable

from web3 import Web3
import aiohttp

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
#  Constants
# ═══════════════════════════════════════════════════════════════════

# Method selectors for interesting transactions
METHOD_SELECTORS = {
    # PancakeSwap / Uniswap Router
    "f305d719": "addLiquidityETH",
    "e8e33700": "addLiquidity",
    "02751cec": "removeLiquidityETH",
    "baa2abde": "removeLiquidity",
    "af2979eb": "removeLiquidityETHSupportingFeeOnTransferTokens",
    # ERC-20
    "095ea7b3": "approve",
    # Factory
    "c9c65396": "createPair",
}

# WebSocket endpoints that may support pending transactions
# Most public endpoints do NOT support this — listed in priority order
MEMPOOL_WS_ENDPOINTS = {
    56: [
        "wss://bsc-rpc.publicnode.com",
        "wss://bsc.drpc.org",
    ],
    1: [
        "wss://ethereum-rpc.publicnode.com",
        "wss://eth.drpc.org",
    ],
}


# ═══════════════════════════════════════════════════════════════════
#  Data structures
# ═══════════════════════════════════════════════════════════════════

@dataclass
class MempoolEvent:
    """A decoded pending transaction from the mempool."""
    tx_hash: str
    method: str = ""                   # addLiquidityETH, createPair, approve, etc.
    from_address: str = ""
    to_address: str = ""               # usually router or factory
    value_wei: int = 0                 # native token value
    value_native: float = 0            # human-readable
    token_address: str = ""            # extracted token address (if applicable)
    pair_address: str = ""             # for createPair events
    gas_price_gwei: float = 0
    timestamp: float = 0
    raw_input: str = ""                # first 200 chars of input data

    def to_dict(self):
        return asdict(self)


# ═══════════════════════════════════════════════════════════════════
#  Mempool Listener
# ═══════════════════════════════════════════════════════════════════

class MempoolListener:
    """
    Listens for pending transactions targeting DEX routers and factories.

    Architecture:
    1. Connect to WSS endpoint
    2. Subscribe to newPendingTransactions (full tx mode if supported)
    3. For each pending tx:
       a. Check if `to` is a known router or factory
       b. Decode the method selector
       c. Extract token/pair addresses from calldata
       d. Emit event for the sniper to process

    Graceful degradation:
    - If WSS doesn't support pendingTransactions, falls back to newHeads
    - If all WSS fail, the service sleeps and retries periodically
    """

    def __init__(self, chain_id: int, w3: Web3,
                 factory_address: str, router_address: str,
                 weth_address: str):
        self.chain_id = chain_id
        self.w3 = w3
        self.factory_addr = factory_address.lower()
        self.router_addr = router_address.lower()
        self.weth_addr = weth_address.lower()

        self.running = False
        self.connected = False
        self.events_detected = 0

        # Callback to process detected events — set by SniperBot
        self._event_callback: Optional[Callable] = None
        self._emit_callback: Optional[Callable] = None  # for UI updates

        # Watchlist of addresses we're monitoring (lowercase)
        self._watched_addresses: set = {
            self.factory_addr,
            self.router_addr,
        }

        # Track seen tx hashes to avoid duplicates
        self._seen_txs: set = set()
        self._max_seen = 5000

        # Statistics
        self.stats = {
            "total_pending_seen": 0,
            "relevant_detected": 0,
            "add_liquidity": 0,
            "remove_liquidity": 0,
            "create_pair": 0,
            "large_approves": 0,
            "connection_errors": 0,
            "last_event_time": 0,
        }

    def set_callbacks(self, event_cb, emit_cb):
        """Set callbacks for processing mempool events."""
        self._event_callback = event_cb
        self._emit_callback = emit_cb

    async def _emit(self, event_type: str, data: dict):
        """Forward UI events through the SniperBot's emit system."""
        if self._emit_callback:
            try:
                await self._emit_callback(event_type, data)
            except Exception as e:
                logger.debug(f"Mempool emit error: {e}")

    async def run(self):
        """Main mempool listener loop with auto-reconnect."""
        self.running = True
        ws_list = MEMPOOL_WS_ENDPOINTS.get(self.chain_id, MEMPOOL_WS_ENDPOINTS[56])
        ws_idx = 0

        await self._emit("mempool_status", {
            "status": "starting",
            "message": "🔍 Iniciando mempool listener...",
        })

        while self.running:
            ws_url = ws_list[ws_idx % len(ws_list)]

            try:
                async with aiohttp.ClientSession() as session:
                    ws = await session.ws_connect(
                        ws_url,
                        heartbeat=15,
                        timeout=aiohttp.ClientWSTimeout(ws_close=10),
                    )
                    self.connected = True

                    await self._emit("mempool_status", {
                        "status": "connected",
                        "message": f"📡 Mempool connected: {ws_url}",
                    })

                    # Try subscribing to full pending transactions
                    sub_ok = await self._subscribe_pending(ws)
                    if not sub_ok:
                        # Fallback: subscribe to newPendingTransactions (hash only)
                        sub_ok = await self._subscribe_pending_hashes(ws)

                    if not sub_ok:
                        logger.info(f"Mempool not supported on {ws_url}")
                        await self._emit("mempool_status", {
                            "status": "unsupported",
                            "message": f"⚠️ {ws_url} no soporta mempool completo",
                        })
                        self.connected = False
                        ws_idx += 1
                        await asyncio.sleep(10)
                        continue

                    # Listen for events
                    async for msg in ws:
                        if not self.running:
                            break

                        if msg.type == aiohttp.WSMsgType.TEXT:
                            try:
                                data = json.loads(msg.data)
                                if data.get("method") == "eth_subscription":
                                    tx_data = data.get("params", {}).get("result")
                                    if tx_data:
                                        await self._process_pending_tx(tx_data)
                            except Exception as e:
                                logger.debug(f"Mempool msg error: {e}")

                        elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                            break

            except Exception as e:
                self.stats["connection_errors"] += 1
                logger.warning(f"Mempool WS error ({ws_url}): {e}")
                await self._emit("mempool_status", {
                    "status": "error",
                    "message": f"⚠️ Mempool reconnecting... ({str(e)[:60]})",
                })
            finally:
                self.connected = False

            ws_idx += 1
            if self.running:
                await asyncio.sleep(3)

    async def _subscribe_pending(self, ws) -> bool:
        """Subscribe to full pending transaction objects."""
        try:
            await ws.send_str(json.dumps({
                "jsonrpc": "2.0", "id": 1,
                "method": "eth_subscribe",
                "params": ["alchemy_pendingTransactions", {
                    "toAddress": [
                        Web3.to_checksum_address(self.factory_addr),
                        Web3.to_checksum_address(self.router_addr),
                    ],
                    "hashesOnly": False,
                }],
            }))

            # Wait for subscription confirmation
            try:
                resp = await asyncio.wait_for(ws.receive(), timeout=5)
                if resp.type == aiohttp.WSMsgType.TEXT:
                    data = json.loads(resp.data)
                    if "result" in data:
                        logger.info(f"Mempool full tx subscription: {data['result']}")
                        return True
                    elif "error" in data:
                        return False
            except asyncio.TimeoutError:
                return False

        except Exception:
            return False

        return False

    async def _subscribe_pending_hashes(self, ws) -> bool:
        """Subscribe to pending transaction hashes (more broadly supported)."""
        try:
            await ws.send_str(json.dumps({
                "jsonrpc": "2.0", "id": 1,
                "method": "eth_subscribe",
                "params": ["newPendingTransactions"],
            }))

            try:
                resp = await asyncio.wait_for(ws.receive(), timeout=5)
                if resp.type == aiohttp.WSMsgType.TEXT:
                    data = json.loads(resp.data)
                    if "result" in data:
                        logger.info(f"Mempool hash subscription: {data['result']}")
                        await self._emit("mempool_status", {
                            "status": "active",
                            "message": "📡 Mempool listener activo (hash mode)",
                        })
                        return True
            except asyncio.TimeoutError:
                return False

        except Exception:
            return False

        return False

    async def _process_pending_tx(self, tx_data):
        """
        Process a pending transaction from the mempool.
        tx_data can be either:
          - A full tx object (from alchemy_pendingTransactions)
          - A tx hash string (from newPendingTransactions)
        """
        if isinstance(tx_data, str):
            # Hash only — need to fetch the full tx
            tx_hash = tx_data
            if tx_hash in self._seen_txs:
                return
            self._seen_txs.add(tx_hash)
            self._trim_seen()

            # Fetch full tx (this adds latency but necessary for hash-only subs)
            loop = asyncio.get_event_loop()
            try:
                tx = await loop.run_in_executor(
                    None,
                    lambda: self.w3.eth.get_transaction(tx_hash),
                )
                if not tx:
                    return
                tx_data = dict(tx)
            except Exception:
                return

        # Extract fields
        tx_hash = tx_data.get("hash", "")
        if isinstance(tx_hash, bytes):
            tx_hash = "0x" + tx_hash.hex()
        elif hasattr(tx_hash, 'hex'):
            tx_hash = "0x" + tx_hash.hex()

        to_addr = str(tx_data.get("to", "") or "").lower()
        from_addr = str(tx_data.get("from", "") or "").lower()

        # Properly convert input data to hex string (HexBytes → "0x...")
        raw_input = tx_data.get("input", "") or ""
        if isinstance(raw_input, (bytes, bytearray)):
            input_data = "0x" + raw_input.hex()
        elif hasattr(raw_input, 'hex') and callable(raw_input.hex):
            input_data = "0x" + raw_input.hex()
        else:
            input_data = str(raw_input)

        value = int(tx_data.get("value", 0))

        self.stats["total_pending_seen"] += 1

        # Only process txs targeting our watched addresses
        if to_addr not in self._watched_addresses:
            return

        # Decode method selector (first 4 bytes of input)
        if len(input_data) < 10:
            return

        selector = input_data[2:10].lower()
        method_name = METHOD_SELECTORS.get(selector, f"unknown_0x{selector}")

        event = MempoolEvent(
            tx_hash=tx_hash,
            method=method_name,
            from_address=from_addr,
            to_address=to_addr,
            value_wei=value,
            value_native=value / 1e18,
            gas_price_gwei=int(tx_data.get("gasPrice", 0)) / 1e9,
            timestamp=time.time(),
            raw_input=input_data[:200],
        )

        # Decode token address from calldata based on method
        event.token_address = self._extract_token_from_calldata(
            method_name, input_data
        )

        # Update statistics
        self.stats["relevant_detected"] += 1
        self.stats["last_event_time"] = time.time()

        if "addLiquidity" in method_name:
            self.stats["add_liquidity"] += 1
        elif "removeLiquidity" in method_name:
            self.stats["remove_liquidity"] += 1
        elif method_name == "createPair":
            self.stats["create_pair"] += 1
        elif method_name == "approve":
            self.stats["large_approves"] += 1

        self.events_detected += 1

        # Forward to sniper bot for processing (it emits to the UI)
        if self._event_callback:
            try:
                await self._event_callback(event)
            except Exception as e:
                logger.debug(f"Mempool event callback error: {e}")

    def _extract_token_from_calldata(self, method: str, input_data: str) -> str:
        """Extract token address from the transaction calldata."""
        try:
            if len(input_data) < 74:
                return ""

            # addLiquidityETH(token, amountTokenDesired, amountTokenMin, amountETHMin, to, deadline)
            # First param is the token address (offset 10-74)
            if method == "addLiquidityETH":
                raw = input_data[10:74]
                return "0x" + raw[-40:]

            # addLiquidity(tokenA, tokenB, ...)
            if method == "addLiquidity":
                # tokenA is first param, tokenB is second
                token_a = "0x" + input_data[10:74][-40:]
                token_b = "0x" + input_data[74:138][-40:]
                # Return whichever isn't WETH
                if token_a.lower() == self.weth_addr:
                    return token_b
                return token_a

            # createPair(tokenA, tokenB)
            if method == "createPair":
                token_a = "0x" + input_data[10:74][-40:]
                token_b = "0x" + input_data[74:138][-40:]
                if token_a.lower() == self.weth_addr:
                    return token_b
                return token_a

            # approve(spender, amount) — spender is first param
            if method == "approve":
                spender = "0x" + input_data[10:74][-40:]
                # If approving the router, the token is the contract being called
                return ""  # token address is the `to` field, not in calldata

            # removeLiquidityETH(token, liquidity, ...)
            if "removeLiquidity" in method:
                raw = input_data[10:74]
                return "0x" + raw[-40:]

        except Exception:
            pass
        return ""

    def _trim_seen(self):
        """Keep the seen tx set manageable."""
        if len(self._seen_txs) > self._max_seen:
            # Remove oldest 20%
            excess = len(self._seen_txs) - int(self._max_seen * 0.8)
            for _ in range(excess):
                self._seen_txs.pop()

    def add_watched_address(self, address: str):
        """Add an address to watch in the mempool."""
        self._watched_addresses.add(address.lower())

    def get_stats(self) -> dict:
        """Return mempool monitoring statistics."""
        return {
            **self.stats,
            "connected": self.connected,
            "watched_addresses": len(self._watched_addresses),
        }

    def stop(self):
        """Stop the mempool listener."""
        self.running = False

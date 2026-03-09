"""
mevProtection.py — MEV Protection / Private Transaction Relay v6.
══════════════════════════════════════════════════════════════════

Shields transactions from MEV bots (sandwich attacks, frontrunning)
by routing through private transaction relays.

Supported strategies:
  - Flashbots (Ethereum): Bundle transactions to miners privately
  - 48 Club (BSC): Private relay avoiding public mempool
  - Private RPC: Generic private mempool submission
  - Gas priority: Outbid frontrunners with aggressive gas pricing
  - Transaction splitting: Split large trades into smaller pieces
  - Deadline tightening: Reduce swap deadline to minimize exposure

Architecture:
  MEVProtector wraps TradeExecutor:
    1. Analyzes pending mempool for frontrunners
    2. Chooses best relay strategy
    3. Submits via private channel
    4. Monitors inclusion and detects sandwich attacks post-tx

Integration:
  TradeExecutor calls MEVProtector.protect_transaction() before sending.
  MEVProtector modifies the tx (private relay, gas boost, etc.) and returns.
"""

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field, asdict
from typing import Optional, Callable
from enum import Enum

from web3 import Web3
import aiohttp

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
#  Data structures
# ═══════════════════════════════════════════════════════════════════

class MEVStrategy(Enum):
    NONE = "none"
    FLASHBOTS = "flashbots"
    PRIVATE_RPC = "private_rpc"
    BSC_48CLUB = "bsc_48club"
    GAS_BOOST = "gas_boost"
    TX_SPLIT = "tx_split"
    DEADLINE_TIGHT = "deadline_tight"


@dataclass
class MEVConfig:
    """MEV protection configuration."""
    enabled: bool = False
    # Strategy priority (first available wins)
    strategy_priority: list = field(default_factory=lambda: [
        "flashbots", "bsc_48club", "private_rpc", "gas_boost"
    ])
    # Flashbots (ETH)
    flashbots_relay_url: str = "https://relay.flashbots.net"
    flashbots_signer_key: str = ""  # separate signer for Flashbots auth
    # 48 Club (BSC)
    bsc_48club_url: str = "https://api.48.club/api/v1/private"
    # Private RPC
    private_rpc_urls: list = field(default_factory=list)
    # Gas boost
    gas_boost_percent: float = 15.0  # % above current gas
    gas_boost_max_gwei: float = 50.0
    # Transaction splitting
    split_threshold_native: float = 1.0  # split trades above this amount
    split_chunks: int = 3
    split_delay_ms: int = 500
    # Deadline
    tight_deadline_seconds: int = 30  # vs default 120s
    # Monitoring
    detect_sandwich: bool = True  # check if we were sandwiched

    def to_dict(self):
        d = asdict(self)
        # Mask signer key
        if d.get("flashbots_signer_key"):
            d["flashbots_signer_key"] = "***"
        return d


@dataclass
class MEVAnalysis:
    """Result of MEV threat analysis for a transaction."""
    token_address: str = ""
    threat_level: str = "unknown"  # "low" | "medium" | "high" | "critical"
    # Mempool analysis
    pending_bots_count: int = 0
    pending_whale_txs: int = 0
    frontrun_risk: float = 0.0     # 0-1 probability
    sandwich_risk: float = 0.0     # 0-1 probability
    # Recommendation
    recommended_strategy: str = "none"
    recommended_gas_boost_pct: float = 0
    should_split: bool = False
    # Detected post-tx
    was_sandwiched: bool = False
    sandwich_profit_usd: float = 0.0
    frontrunner_address: str = ""

    def to_dict(self):
        return asdict(self)


@dataclass
class ProtectedTx:
    """A transaction modified for MEV protection."""
    original_tx: dict = field(default_factory=dict)
    protected_tx: dict = field(default_factory=dict)
    strategy_used: str = "none"
    relay_url: str = ""
    bundle_hash: str = ""
    gas_price_original: int = 0
    gas_price_protected: int = 0
    deadline_original: int = 0
    deadline_protected: int = 0
    timestamp: float = 0.0
    success: bool = False
    error: str = ""

    def to_dict(self):
        return asdict(self)


# ═══════════════════════════════════════════════════════════════════
#  MEV Protector
# ═══════════════════════════════════════════════════════════════════

class MEVProtector:
    """
    MEV protection engine.

    Wraps transaction submission to avoid frontrunning and sandwich attacks.

    Usage:
        mev = MEVProtector(w3, chain_id=56)
        mev.configure(enabled=True)
        protected = await mev.protect_transaction(raw_tx)
        tx_hash = await mev.send_protected(protected)
    """

    # Known MEV bot addresses (partial list)
    KNOWN_MEV_BOTS = {
        "0x00000000003b3cc22aF3aE1EAc0440BcEe416B40",
        "0x56178a0d5F301bAf6CF3e1Cd53d9863437345Bf9",
        "0x6b75d8AF000000e20B7a7DDf000Ba900b4009A80",
        "0xDef1C0ded9bec7F1a1670819833240f027b25EfF",
    }

    def __init__(self, w3: Web3, chain_id: int = 56):
        self.w3 = w3
        self.chain_id = chain_id
        self.config = MEVConfig()
        self._session: aiohttp.ClientSession | None = None

        # Load keys from env
        self.config.flashbots_signer_key = os.environ.get("FLASHBOTS_SIGNER_KEY", "")
        self.config.bsc_48club_url = os.environ.get("BSC_48CLUB_URL", self.config.bsc_48club_url)

        # Stats
        self.stats = {
            "txs_protected": 0,
            "txs_sent_private": 0,
            "sandwiches_detected": 0,
            "sandwiches_avoided": 0,
            "total_gas_saved_usd": 0.0,
        }

        self._recent_analyses: list[MEVAnalysis] = []

    async def _ensure_session(self):
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10)
            )

    def configure(self, **kwargs):
        """Update MEV config."""
        for k, v in kwargs.items():
            if hasattr(self.config, k):
                setattr(self.config, k, v)
        logger.info(f"MEVProtector: config updated — {list(kwargs.keys())}")

    # ───────────────────────────────────────────────────────
    #  Threat analysis
    # ───────────────────────────────────────────────────────

    async def analyze_threat(self, token_address: str, amount_native: float) -> MEVAnalysis:
        """
        Analyze MEV threat for a potential swap.

        Checks pending mempool for known bots, large pending swaps,
        and estimates frontrun/sandwich risk.
        """
        analysis = MEVAnalysis(token_address=token_address)

        try:
            loop = asyncio.get_event_loop()

            # 1. Check pending transactions for the token's pair
            pending_txs = await self._get_pending_swaps(token_address)

            bot_count = 0
            whale_count = 0
            for ptx in pending_txs:
                from_addr = ptx.get("from", "")
                if from_addr in self.KNOWN_MEV_BOTS:
                    bot_count += 1
                value = int(ptx.get("value", 0)) / 1e18
                if value > amount_native * 5:
                    whale_count += 1

            analysis.pending_bots_count = bot_count
            analysis.pending_whale_txs = whale_count

            # 2. Calculate risk scores
            # Higher risk if: known bots present, large amount, high gas activity
            base_risk = 0.1
            if bot_count > 0:
                base_risk += 0.3 * min(bot_count, 3)
            if amount_native > 0.5:
                base_risk += 0.1
            if amount_native > 2.0:
                base_risk += 0.2

            analysis.frontrun_risk = min(base_risk, 1.0)
            analysis.sandwich_risk = min(base_risk * 0.8, 1.0)

            # 3. Determine threat level
            risk = max(analysis.frontrun_risk, analysis.sandwich_risk)
            if risk < 0.2:
                analysis.threat_level = "low"
            elif risk < 0.5:
                analysis.threat_level = "medium"
            elif risk < 0.8:
                analysis.threat_level = "high"
            else:
                analysis.threat_level = "critical"

            # 4. Recommend strategy
            analysis.recommended_strategy = self._recommend_strategy(analysis, amount_native)
            if analysis.threat_level in ("high", "critical"):
                analysis.recommended_gas_boost_pct = 20.0
                analysis.should_split = amount_native > self.config.split_threshold_native

        except Exception as e:
            logger.debug(f"MEV analysis error: {e}")
            analysis.threat_level = "unknown"
            analysis.recommended_strategy = "gas_boost"

        self._recent_analyses.append(analysis)
        if len(self._recent_analyses) > 100:
            self._recent_analyses = self._recent_analyses[-50:]

        return analysis

    async def _get_pending_swaps(self, token_address: str) -> list[dict]:
        """Get pending swap transactions related to a token."""
        try:
            loop = asyncio.get_event_loop()
            # Use pending block filter
            pending = await loop.run_in_executor(
                None, lambda: self.w3.eth.get_block("pending", full_transactions=True)
            )
            if not pending:
                return []

            router_addrs = {
                "0x10ed43c718714eb63d5aa57b78b54704e256024e",  # PancakeSwap
                "0x7a250d5630b4cf539739df2c5dacb4c659f2488d",  # Uniswap V2
                "0xe592427a0aece92de3edee1f18e0157c05861564",  # Uniswap V3
            }

            swaps = []
            for tx in pending.get("transactions", []):
                to = (tx.get("to", "") or "").lower()
                if to in router_addrs:
                    input_data = tx.get("input", "")
                    if isinstance(input_data, bytes):
                        input_data = input_data.hex()
                    # Check if token address appears in calldata
                    token_clean = token_address.lower().replace("0x", "")
                    if token_clean in input_data.lower():
                        swaps.append(tx)

            return swaps

        except Exception as e:
            logger.debug(f"Pending swap check error: {e}")
            return []

    def _recommend_strategy(self, analysis: MEVAnalysis, amount: float) -> str:
        """Choose the best protection strategy."""
        # Iterate priority list
        for strategy in self.config.strategy_priority:
            if strategy == "flashbots" and self.chain_id == 1 and self.config.flashbots_signer_key:
                return "flashbots"
            elif strategy == "bsc_48club" and self.chain_id == 56:
                return "bsc_48club"
            elif strategy == "private_rpc" and self.config.private_rpc_urls:
                return "private_rpc"
            elif strategy == "gas_boost":
                return "gas_boost"

        return "gas_boost"  # fallback

    # ───────────────────────────────────────────────────────
    #  Transaction protection
    # ───────────────────────────────────────────────────────

    async def protect_transaction(self, raw_tx: dict, analysis: MEVAnalysis = None) -> ProtectedTx:
        """
        Apply MEV protection to a transaction.

        Args:
            raw_tx: unsigned transaction dict (to, data, value, gas, etc.)
            analysis: optional pre-computed MEV analysis

        Returns:
            ProtectedTx with modified transaction and relay info.
        """
        if not self.config.enabled:
            return ProtectedTx(
                original_tx=raw_tx, protected_tx=raw_tx,
                strategy_used="none", success=True
            )

        protected = ProtectedTx(
            original_tx=raw_tx.copy(),
            timestamp=time.time(),
        )

        try:
            strategy = analysis.recommended_strategy if analysis else self._recommend_strategy(
                MEVAnalysis(), raw_tx.get("value", 0) / 1e18
            )

            # Apply strategy
            if strategy == "flashbots":
                protected = await self._apply_flashbots(raw_tx, protected)
            elif strategy == "bsc_48club":
                protected = await self._apply_48club(raw_tx, protected)
            elif strategy == "private_rpc":
                protected = await self._apply_private_rpc(raw_tx, protected)
            elif strategy == "gas_boost":
                protected = self._apply_gas_boost(raw_tx, protected)
            elif strategy == "tx_split":
                # For splits, we return multiple txs — handled separately
                protected = self._apply_gas_boost(raw_tx, protected)

            # Always tighten deadline
            protected = self._tighten_deadline(protected)
            protected.strategy_used = strategy
            protected.success = True
            self.stats["txs_protected"] += 1

        except Exception as e:
            logger.error(f"MEV protection error: {e}")
            protected.protected_tx = raw_tx
            protected.strategy_used = "none"
            protected.error = str(e)
            protected.success = False

        return protected

    async def send_protected(self, protected: ProtectedTx, signed_tx=None) -> str:
        """
        Send a protected transaction via the appropriate relay.

        Args:
            protected: ProtectedTx from protect_transaction()
            signed_tx: signed transaction to submit

        Returns:
            tx_hash string
        """
        if protected.strategy_used == "flashbots" and signed_tx:
            return await self._send_flashbots(signed_tx, protected)
        elif protected.strategy_used == "bsc_48club" and signed_tx:
            return await self._send_48club(signed_tx, protected)
        elif protected.strategy_used == "private_rpc" and signed_tx:
            return await self._send_private_rpc(signed_tx, protected)
        else:
            # Standard submission
            if signed_tx:
                loop = asyncio.get_event_loop()
                tx_hash = await loop.run_in_executor(
                    None, self.w3.eth.send_raw_transaction, signed_tx.raw_transaction
                )
                return tx_hash.hex()
        return ""

    # ───────────────────────────────────────────────────────
    #  Strategy implementations
    # ───────────────────────────────────────────────────────

    async def _apply_flashbots(self, raw_tx: dict, protected: ProtectedTx) -> ProtectedTx:
        """Apply Flashbots bundle strategy (Ethereum)."""
        protected.protected_tx = raw_tx.copy()
        protected.relay_url = self.config.flashbots_relay_url

        # Boost gas slightly (miners prioritize higher-tip bundles)
        gas_price = raw_tx.get("maxFeePerGas", raw_tx.get("gasPrice", 0))
        boosted = int(gas_price * 1.05)  # 5% boost (minimal, since private)
        if "maxFeePerGas" in raw_tx:
            protected.protected_tx["maxFeePerGas"] = boosted
            protected.protected_tx["maxPriorityFeePerGas"] = int(
                raw_tx.get("maxPriorityFeePerGas", 2 * 1e9) * 1.1
            )
        else:
            protected.protected_tx["gasPrice"] = boosted

        protected.gas_price_original = gas_price
        protected.gas_price_protected = boosted
        return protected

    async def _apply_48club(self, raw_tx: dict, protected: ProtectedTx) -> ProtectedTx:
        """Apply 48 Club private relay strategy (BSC)."""
        protected.protected_tx = raw_tx.copy()
        protected.relay_url = self.config.bsc_48club_url

        # 48Club accepts standard BSC transactions
        gas_price = raw_tx.get("gasPrice", int(5 * 1e9))
        boosted = int(gas_price * 1.05)
        protected.protected_tx["gasPrice"] = boosted
        protected.gas_price_original = gas_price
        protected.gas_price_protected = boosted
        return protected

    async def _apply_private_rpc(self, raw_tx: dict, protected: ProtectedTx) -> ProtectedTx:
        """Apply private RPC submission."""
        protected.protected_tx = raw_tx.copy()
        if self.config.private_rpc_urls:
            protected.relay_url = self.config.private_rpc_urls[0]
        return protected

    def _apply_gas_boost(self, raw_tx: dict, protected: ProtectedTx) -> ProtectedTx:
        """Boost gas price to outpace frontrunners."""
        protected.protected_tx = raw_tx.copy()
        boost = 1 + (self.config.gas_boost_percent / 100)

        gas_price = raw_tx.get("gasPrice", raw_tx.get("maxFeePerGas", int(5 * 1e9)))
        boosted = int(gas_price * boost)
        max_gas = int(self.config.gas_boost_max_gwei * 1e9)
        boosted = min(boosted, max_gas)

        if "maxFeePerGas" in raw_tx:
            protected.protected_tx["maxFeePerGas"] = boosted
            protected.protected_tx["maxPriorityFeePerGas"] = int(
                raw_tx.get("maxPriorityFeePerGas", 2 * 1e9) * boost
            )
        else:
            protected.protected_tx["gasPrice"] = boosted

        protected.gas_price_original = gas_price
        protected.gas_price_protected = boosted
        return protected

    def _tighten_deadline(self, protected: ProtectedTx) -> ProtectedTx:
        """Reduce transaction deadline for less exposure."""
        tx = protected.protected_tx
        if not tx:
            return protected

        # Check if calldata contains a deadline parameter
        # Deadline is typically the last uint256 in swap functions
        input_data = tx.get("data", "")
        if isinstance(input_data, bytes):
            input_data = "0x" + input_data.hex()

        if len(input_data) > 10:
            # Set tighter deadline in the transaction itself
            # The actual deadline is in the calldata — handled by TradeExecutor
            pass

        protected.deadline_protected = self.config.tight_deadline_seconds
        return protected

    # ───────────────────────────────────────────────────────
    #  Relay senders
    # ───────────────────────────────────────────────────────

    async def _send_flashbots(self, signed_tx, protected: ProtectedTx) -> str:
        """Send transaction bundle via Flashbots relay."""
        await self._ensure_session()
        try:
            raw = signed_tx.raw_transaction.hex()
            if not raw.startswith("0x"):
                raw = "0x" + raw

            loop = asyncio.get_event_loop()
            block_number = await loop.run_in_executor(
                None, lambda: self.w3.eth.block_number
            )
            target_block = hex(block_number + 1)

            # Flashbots bundle
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "eth_sendBundle",
                "params": [{
                    "txs": [raw],
                    "blockNumber": target_block,
                    "minTimestamp": 0,
                    "maxTimestamp": int(time.time()) + self.config.tight_deadline_seconds
                }]
            }

            headers = {"Content-Type": "application/json"}

            # Sign the payload with Flashbots signer if available
            if self.config.flashbots_signer_key:
                try:
                    from eth_account import Account
                    signer = Account.from_key(self.config.flashbots_signer_key)
                    message = Web3.keccak(text=json.dumps(payload))
                    signature = signer.signHash(message)
                    headers["X-Flashbots-Signature"] = (
                        f"{signer.address}:{signature.signature.hex()}"
                    )
                except Exception as e:
                    logger.warning(f"Flashbots signing error: {e}")

            async with self._session.post(
                self.config.flashbots_relay_url,
                json=payload, headers=headers
            ) as resp:
                result = await resp.json()
                bundle_hash = result.get("result", {}).get("bundleHash", "")
                protected.bundle_hash = bundle_hash
                self.stats["txs_sent_private"] += 1
                logger.info(f"MEV: Flashbots bundle submitted — {bundle_hash[:16]}…")
                return bundle_hash

        except Exception as e:
            logger.error(f"Flashbots send error: {e}")
            # Fallback to standard submission
            loop = asyncio.get_event_loop()
            tx_hash = await loop.run_in_executor(
                None, self.w3.eth.send_raw_transaction, signed_tx.raw_transaction
            )
            return tx_hash.hex()

    async def _send_48club(self, signed_tx, protected: ProtectedTx) -> str:
        """Send transaction via 48 Club private relay (BSC)."""
        await self._ensure_session()
        try:
            raw = signed_tx.raw_transaction.hex()
            if not raw.startswith("0x"):
                raw = "0x" + raw

            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "eth_sendRawTransaction",
                "params": [raw]
            }

            async with self._session.post(
                self.config.bsc_48club_url,
                json=payload
            ) as resp:
                result = await resp.json()
                tx_hash = result.get("result", "")
                self.stats["txs_sent_private"] += 1
                logger.info(f"MEV: 48Club tx submitted — {tx_hash[:16]}…")
                return tx_hash

        except Exception as e:
            logger.error(f"48Club send error: {e}")
            loop = asyncio.get_event_loop()
            tx_hash = await loop.run_in_executor(
                None, self.w3.eth.send_raw_transaction, signed_tx.raw_transaction
            )
            return tx_hash.hex()

    async def _send_private_rpc(self, signed_tx, protected: ProtectedTx) -> str:
        """Send transaction via private RPC."""
        await self._ensure_session()
        for rpc_url in self.config.private_rpc_urls:
            try:
                raw = signed_tx.raw_transaction.hex()
                if not raw.startswith("0x"):
                    raw = "0x" + raw

                payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "eth_sendRawTransaction",
                    "params": [raw]
                }

                async with self._session.post(rpc_url, json=payload) as resp:
                    result = await resp.json()
                    tx_hash = result.get("result", "")
                    if tx_hash:
                        self.stats["txs_sent_private"] += 1
                        return tx_hash
            except Exception as e:
                logger.debug(f"Private RPC {rpc_url} failed: {e}")
                continue

        # All RPCs failed — standard submission
        loop = asyncio.get_event_loop()
        tx_hash = await loop.run_in_executor(
            None, self.w3.eth.send_raw_transaction, signed_tx.raw_transaction
        )
        return tx_hash.hex()

    # ───────────────────────────────────────────────────────
    #  Post-tx analysis
    # ───────────────────────────────────────────────────────

    async def detect_sandwich(self, tx_hash: str, token_address: str) -> MEVAnalysis:
        """
        Check if a completed transaction was sandwiched.

        Looks at transactions in the same block before/after ours
        to detect sandwich patterns.
        """
        analysis = MEVAnalysis(token_address=token_address)

        try:
            loop = asyncio.get_event_loop()
            receipt = await loop.run_in_executor(
                None, self.w3.eth.get_transaction_receipt, tx_hash
            )
            if not receipt:
                return analysis

            block_num = receipt["blockNumber"]
            tx_index = receipt["transactionIndex"]

            block = await loop.run_in_executor(
                None, lambda: self.w3.eth.get_block(block_num, full_transactions=True)
            )
            if not block:
                return analysis

            txs = block.get("transactions", [])
            token_clean = token_address.lower().replace("0x", "")

            # Check transactions right before and after ours
            before_sus = False
            after_sus = False

            for tx in txs:
                idx = tx.get("transactionIndex", 0)
                input_data = tx.get("input", "")
                if isinstance(input_data, bytes):
                    input_data = input_data.hex()

                if token_clean not in input_data.lower():
                    continue

                from_addr = tx.get("from", "")
                if from_addr in self.KNOWN_MEV_BOTS:
                    if idx < tx_index:
                        before_sus = True
                    elif idx > tx_index:
                        after_sus = True

            if before_sus and after_sus:
                analysis.was_sandwiched = True
                analysis.threat_level = "critical"
                self.stats["sandwiches_detected"] += 1
                logger.warning(f"MEV: sandwich detected on {tx_hash[:16]}…!")
            elif before_sus or after_sus:
                analysis.threat_level = "high"

        except Exception as e:
            logger.debug(f"Sandwich detection error: {e}")

        return analysis

    # ───────────────────────────────────────────────────────
    #  Transaction splitting (for large trades)
    # ───────────────────────────────────────────────────────

    def split_transaction(self, raw_tx: dict, chunks: int = 0) -> list[dict]:
        """
        Split a large trade into smaller pieces to reduce MEV exposure.

        Returns list of modified transactions with split values.
        """
        chunks = chunks or self.config.split_chunks
        value = int(raw_tx.get("value", 0))
        if value == 0 or chunks <= 1:
            return [raw_tx]

        chunk_value = value // chunks
        remainder = value - chunk_value * chunks

        split_txs = []
        for i in range(chunks):
            tx = raw_tx.copy()
            tx["value"] = chunk_value + (remainder if i == chunks - 1 else 0)
            split_txs.append(tx)

        logger.info(f"MEV: split trade into {chunks} chunks of ~{chunk_value / 1e18:.4f} native")
        return split_txs

    # ───────────────────────────────────────────────────────
    #  Stats
    # ───────────────────────────────────────────────────────

    def get_stats(self) -> dict:
        return {
            **self.stats,
            "config": self.config.to_dict(),
            "recent_analyses": [a.to_dict() for a in self._recent_analyses[-10:]],
        }

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

"""
smartMoneyTracker.py — Smart Money / Whale Wallet Tracking.

Tracks wallets that consistently buy early in successful pumps and copies
their movements. Also monitors known "smart money" wallets for activity.

Features:
  - Tracks wallets that bought the same token before/after a pump
  - Scores wallets by historical success rate
  - Alerts when a tracked wallet buys a new token
  - Maintains a rolling database of "profitable wallets"
  - Cross-references with detected tokens for copy-trading signals

Integration:
  Runs in background, analyzing trade history of detected tokens.
  When a smart wallet buys a newly detected token → boost pump score.
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, asdict, field
from typing import Optional, Callable

from web3 import Web3
import aiohttp

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
#  Constants
# ═══════════════════════════════════════════════════════════════════

# Transfer event topic
TRANSFER_TOPIC = "0x" + Web3.keccak(
    text="Transfer(address,address,uint256)"
).hex()

# Swap event topic (Uniswap V2 pairs)
SWAP_TOPIC = "0x" + Web3.keccak(
    text="Swap(address,uint256,uint256,uint256,uint256,address)"
).hex()

# Known whale/smart money wallets to track initially
# These are example addresses — in production, populated from analysis
KNOWN_SMART_WALLETS = {
    56: [],  # BSC smart wallets
    1: [],   # ETH smart wallets
}

# Minimum profit to consider a wallet "smart"
MIN_PROFIT_TRADES = 3
MIN_WIN_RATE = 0.6  # 60% win rate


# ═══════════════════════════════════════════════════════════════════
#  Data structures
# ═══════════════════════════════════════════════════════════════════

@dataclass
class SmartWallet:
    """A wallet being tracked for smart money activity."""
    address: str
    label: str = ""                    # custom label (e.g., "Whale #1")
    # Performance metrics
    total_trades: int = 0
    winning_trades: int = 0
    win_rate: float = 0                # 0-1
    avg_profit_percent: float = 0
    total_profit_usd: float = 0
    # Activity tracking
    tokens_bought: list = field(default_factory=list)  # last N tokens
    last_activity: float = 0
    first_seen: float = 0
    # Score (0-100)
    smart_score: int = 0
    # Source
    source: str = "auto"               # auto | manual | known

    def to_dict(self):
        d = asdict(self)
        # Limit list sizes for serialization
        d["tokens_bought"] = d["tokens_bought"][-20:]
        return d


@dataclass
class SmartMoneySignal:
    """A signal that smart money is buying a specific token."""
    token_address: str
    wallet_address: str
    wallet_label: str = ""
    wallet_score: int = 0
    signal_type: str = ""              # early_buy | accumulation | follow
    tx_hash: str = ""
    amount_native: float = 0
    timestamp: float = 0
    confidence: int = 0                # 0-100

    def to_dict(self):
        return asdict(self)


# ═══════════════════════════════════════════════════════════════════
#  Smart Money Tracker
# ═══════════════════════════════════════════════════════════════════

class SmartMoneyTracker:
    """
    Tracks profitable wallets and emits signals when they buy new tokens.

    How it works:
    1. For each successfully pumped token, analyze early buyers
    2. Wallets that consistently buy early → added to "smart wallet" list
    3. Monitor smart wallets for new buys on any token
    4. When a smart wallet buys a token being sniped → boost confidence

    Data flow:
      Token pumps → analyze early buyers → identify smart wallets
      Smart wallet buys new token → signal to sniper → boost pump score
    """

    def __init__(self, w3: Web3, chain_id: int):
        self.w3 = w3
        self.chain_id = chain_id

        self.running = False
        self._wallets: dict[str, SmartWallet] = {}  # addr → SmartWallet
        self._recent_signals: list[SmartMoneySignal] = []

        # Callbacks
        self._signal_callback: Optional[Callable] = None
        self._emit_callback: Optional[Callable] = None

        # Initialize with known wallets
        for addr in KNOWN_SMART_WALLETS.get(chain_id, []):
            self._wallets[addr.lower()] = SmartWallet(
                address=addr,
                label="Known Whale",
                source="known",
                smart_score=70,
                first_seen=time.time(),
            )

        # Statistics
        self.stats = {
            "wallets_tracked": len(self._wallets),
            "signals_emitted": 0,
            "tokens_analyzed": 0,
        }

    def set_callbacks(self, signal_cb, emit_cb):
        self._signal_callback = signal_cb
        self._emit_callback = emit_cb

    async def _emit(self, event_type: str, data: dict):
        if self._emit_callback:
            try:
                await self._emit_callback(event_type, data)
            except Exception as e:
                logger.debug(f"SmartMoney emit error: {e}")

    async def analyze_token_traders(self, token_address: str,
                                     pair_address: str,
                                     is_pumped: bool = False):
        """
        Analyze the early buyers of a token.
        If the token pumped successfully, these buyers are "smart money".

        Args:
            token_address: The token contract
            pair_address: The DEX pair address
            is_pumped: True if this token had a successful pump (>2x)
        """
        loop = asyncio.get_event_loop()
        self.stats["tokens_analyzed"] += 1

        try:
            token_cs = Web3.to_checksum_address(token_address)
            current_block = await loop.run_in_executor(
                None, lambda: self.w3.eth.block_number
            )

            # Get early Transfer events (first 100 blocks of the token's life)
            # Looking for transfers FROM the pair TO buyer wallets (= buys)
            from_block = max(0, current_block - 2000)

            logs = await loop.run_in_executor(
                None,
                lambda: self.w3.eth.get_logs({
                    "fromBlock": from_block,
                    "toBlock": current_block,
                    "address": token_cs,
                    "topics": [
                        TRANSFER_TOPIC,
                        # from = pair address (tokens leaving pair = someone bought)
                        "0x" + pair_address.lower().replace("0x", "").zfill(64),
                    ],
                }),
            )

            # Extract buyer addresses
            buyers: dict[str, dict] = {}  # addr → {tx_count, first_block, total_amount}
            for log_entry in logs[:500]:  # Limit processing
                try:
                    topics = log_entry.get("topics", [])
                    if len(topics) < 3:
                        continue
                    to_addr = "0x" + topics[2].hex()[-40:]
                    amount = int(log_entry.get("data", "0x0"), 16)
                    block = log_entry.get("blockNumber", 0)

                    to_lower = to_addr.lower()
                    if to_lower not in buyers:
                        buyers[to_lower] = {
                            "address": to_addr,
                            "tx_count": 0,
                            "first_block": block,
                            "total_amount": 0,
                        }
                    buyers[to_lower]["tx_count"] += 1
                    buyers[to_lower]["total_amount"] += amount

                except Exception:
                    continue

            # If the token pumped, record these as "smart" buys
            if is_pumped:
                for addr, data in buyers.items():
                    if addr in self._wallets:
                        wallet = self._wallets[addr]
                        wallet.winning_trades += 1
                        wallet.total_trades += 1
                        wallet.tokens_bought.append({
                            "token": token_address[:10],
                            "block": data["first_block"],
                            "result": "win",
                        })
                    else:
                        # New smart wallet discovered
                        wallet = SmartWallet(
                            address=data["address"],
                            label=f"Smart #{len(self._wallets)+1}",
                            total_trades=1,
                            winning_trades=1,
                            first_seen=time.time(),
                            last_activity=time.time(),
                            source="auto",
                            tokens_bought=[{
                                "token": token_address[:10],
                                "block": data["first_block"],
                                "result": "win",
                            }],
                        )
                        self._wallets[addr] = wallet

                    self._update_wallet_score(self._wallets[addr])

            self.stats["wallets_tracked"] = len(self._wallets)

        except Exception as e:
            logger.debug(f"SmartMoney analysis error for {token_address}: {e}")

    async def check_smart_buyers(self, token_address: str,
                                  pair_address: str) -> list[SmartMoneySignal]:
        """
        Check if any tracked smart wallets have bought this token.
        Returns signals if smart money is accumulating.
        """
        signals = []
        loop = asyncio.get_event_loop()

        if not self._wallets:
            return signals

        try:
            token_cs = Web3.to_checksum_address(token_address)
            current_block = await loop.run_in_executor(
                None, lambda: self.w3.eth.block_number
            )

            # Check recent transfers to see if any smart wallet received tokens
            from_block = max(0, current_block - 100)

            logs = await loop.run_in_executor(
                None,
                lambda: self.w3.eth.get_logs({
                    "fromBlock": from_block,
                    "toBlock": current_block,
                    "address": token_cs,
                    "topics": [TRANSFER_TOPIC],
                }),
            )

            for log_entry in logs:
                try:
                    topics = log_entry.get("topics", [])
                    if len(topics) < 3:
                        continue
                    to_addr = "0x" + topics[2].hex()[-40:]
                    to_lower = to_addr.lower()

                    if to_lower in self._wallets:
                        wallet = self._wallets[to_lower]
                        tx_hash = log_entry.get("transactionHash", b"")
                        if isinstance(tx_hash, bytes):
                            tx_hash = "0x" + tx_hash.hex()

                        signal = SmartMoneySignal(
                            token_address=token_address,
                            wallet_address=to_addr,
                            wallet_label=wallet.label,
                            wallet_score=wallet.smart_score,
                            signal_type="early_buy",
                            tx_hash=str(tx_hash),
                            timestamp=time.time(),
                            confidence=min(95, wallet.smart_score + 20),
                        )
                        signals.append(signal)

                        # Emit signal
                        self.stats["signals_emitted"] += 1
                        await self._emit("smart_money_signal", signal.to_dict())

                        if self._signal_callback:
                            await self._signal_callback(signal)

                except Exception:
                    continue

        except Exception as e:
            logger.debug(f"SmartMoney buyer check error: {e}")

        self._recent_signals.extend(signals)
        # Keep last 100 signals
        if len(self._recent_signals) > 100:
            self._recent_signals = self._recent_signals[-100:]

        return signals

    def _update_wallet_score(self, wallet: SmartWallet):
        """Update a wallet's smart score based on performance."""
        if wallet.total_trades == 0:
            wallet.smart_score = 0
            return

        wallet.win_rate = wallet.winning_trades / wallet.total_trades
        wallet.last_activity = time.time()

        # Base score from win rate
        score = int(wallet.win_rate * 60)  # 60% max from win rate

        # Bonus for number of trades (consistency)
        if wallet.total_trades >= 10:
            score += 20
        elif wallet.total_trades >= 5:
            score += 10
        elif wallet.total_trades >= MIN_PROFIT_TRADES:
            score += 5

        # Recency bonus
        age_days = (time.time() - wallet.first_seen) / 86400
        if age_days < 7:
            score += 10  # active within last week

        # Known wallet bonus
        if wallet.source == "known":
            score += 10

        wallet.smart_score = max(0, min(100, score))

    def add_wallet(self, address: str, label: str = ""):
        """Manually add a wallet to track."""
        addr_lower = address.lower()
        if addr_lower not in self._wallets:
            self._wallets[addr_lower] = SmartWallet(
                address=address,
                label=label or f"Manual #{len(self._wallets)+1}",
                source="manual",
                smart_score=50,
                first_seen=time.time(),
            )
            self.stats["wallets_tracked"] = len(self._wallets)

    def get_top_wallets(self, limit: int = 20) -> list[dict]:
        """Return top-scoring smart wallets."""
        sorted_wallets = sorted(
            self._wallets.values(),
            key=lambda w: w.smart_score,
            reverse=True,
        )
        return [w.to_dict() for w in sorted_wallets[:limit]]

    def get_recent_signals(self, limit: int = 20) -> list[dict]:
        """Return recent smart money signals."""
        return [s.to_dict() for s in self._recent_signals[-limit:]]

    def get_stats(self) -> dict:
        return {
            **self.stats,
            "running": self.running,
            "top_wallets": self.get_top_wallets(5),
        }

    def stop(self):
        self.running = False

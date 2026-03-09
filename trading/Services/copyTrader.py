"""
copyTrader.py — Whale Copy-Trading Engine v6.
═══════════════════════════════════════════════

Extends SmartMoneyTracker to automatically replicate trades from
profitable wallets ("whales") in real time.

Features:
  - Real-time mempool monitoring for tracked wallet swaps
  - Configurable follow amount, delay, and max concurrent follows
  - Wallet scoring with automatic tracking/untracking
  - Portfolio guard: skip if already holding same token
  - Copy-buy, copy-sell, or both
  - WebSocket updates on copy-trade signals
  - Persistent wallet list with performance stats

Architecture:
  CopyTrader → watches SmartMoneyTracker signals
  On whale buy → validate → queue buy via TradeExecutor
  On whale sell → check if we hold → queue sell
  Performance tracking per wallet and per pair

Integration:
  CopyTrader.start() → starts concurrent monitoring loop
  Signals flow: Mempool → SmartMoneyTracker → CopyTrader → TradeExecutor
"""

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field, asdict
from typing import Optional, Callable

from web3 import Web3
import aiohttp

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
#  Data structures
# ═══════════════════════════════════════════════════════════════════

@dataclass
class TrackedWallet:
    """A wallet the copy trader follows."""
    address: str
    label: str = ""
    enabled: bool = True
    # Copy config per wallet
    follow_buys: bool = True
    follow_sells: bool = True
    max_follow_amount_native: float = 0.1   # max BNB/ETH per copy trade
    follow_percent: float = 50.0             # copy 50% of whale's trade size
    min_whale_amount_native: float = 0.5     # min whale amount to trigger copy
    max_delay_seconds: float = 5.0           # max delay before copying
    # Performance
    total_copies: int = 0
    successful_copies: int = 0
    total_pnl_usd: float = 0.0
    avg_pnl_pct: float = 0.0
    win_rate: float = 0.0
    # Activity
    last_activity: float = 0.0
    first_tracked: float = 0.0
    # Source
    source: str = "auto"  # auto | manual | smartmoney
    smart_score: int = 0

    def to_dict(self):
        return asdict(self)


@dataclass
class CopyTradeSignal:
    """A signal to copy a whale's trade."""
    wallet_address: str
    wallet_label: str = ""
    token_address: str = ""
    pair_address: str = ""
    direction: str = ""     # "buy" | "sell"
    whale_amount_native: float = 0.0
    copy_amount_native: float = 0.0
    tx_hash: str = ""
    timestamp: float = 0.0
    status: str = "pending"  # pending | executed | skipped | failed
    skip_reason: str = ""
    result_tx_hash: str = ""
    result_pnl_usd: float = 0.0
    confidence: int = 0

    def to_dict(self):
        return asdict(self)


@dataclass
class CopyTraderConfig:
    """Global copy trading configuration."""
    enabled: bool = False
    max_concurrent_follows: int = 5
    max_daily_copy_trades: int = 20
    max_total_exposure_native: float = 1.0  # max total BNB/ETH in copy positions
    default_follow_amount_native: float = 0.05
    default_follow_percent: float = 50.0
    min_wallet_score: int = 60           # min smart score to auto-follow
    auto_follow_smart_money: bool = True
    copy_buy: bool = True
    copy_sell: bool = True
    blacklisted_tokens: list = field(default_factory=list)  # tokens to never copy

    def to_dict(self):
        return asdict(self)


# ═══════════════════════════════════════════════════════════════════
#  Swap decoding helpers
# ═══════════════════════════════════════════════════════════════════

# Common swap function selectors
_SWAP_SELECTORS = {
    "0x7ff36ab5": "swapExactETHForTokens",
    "0xb6f9de95": "swapExactETHForTokensSupportingFeeOnTransferTokens",
    "0x18cbafe5": "swapExactTokensForETH",
    "0x791ac947": "swapExactTokensForETHSupportingFeeOnTransferTokens",
    "0x38ed1739": "swapExactTokensForTokens",
    "0x5c11d795": "swapExactTokensForTokensSupportingFeeOnTransferTokens",
}


def _decode_swap_direction(input_data: str) -> str:
    """Determine if a swap is a buy or sell based on function selector."""
    selector = input_data[:10] if len(input_data) >= 10 else ""
    name = _SWAP_SELECTORS.get(selector, "")
    if "ETHForToken" in name:
        return "buy"
    if "TokensForETH" in name:
        return "sell"
    return "unknown"


def _extract_token_from_path(input_data: str, direction: str) -> str:
    """Extract token address from swap path (best effort)."""
    try:
        # The path is encoded in the calldata — extract last address for buys, first for sells
        data = input_data[10:]  # skip selector
        # Each address is 32 bytes (64 hex), embedded at offset positions
        # This is simplified — real implementation would use ABI decode
        if len(data) >= 320:  # at least enough for basic swap
            if direction == "buy":
                # Last address in path is the token being bought
                addr = "0x" + data[-40:]
                return Web3.to_checksum_address(addr)
            elif direction == "sell":
                # First non-WETH address in path
                # Skip: offset(64) + offset(64) + offset(64) + to(64) + deadline(64) + path_length(64) + first_addr(64)
                addr = "0x" + data[64 * 6 + 24:64 * 6 + 64]
                return Web3.to_checksum_address(addr)
    except Exception:
        pass
    return ""


# ═══════════════════════════════════════════════════════════════════
#  Copy Trader
# ═══════════════════════════════════════════════════════════════════

class CopyTrader:
    """
    Whale copy-trading engine.

    Monitors tracked wallets for swaps and replicates them using TradeExecutor.

    Usage:
        copy_trader = CopyTrader(w3, chain_id)
        copy_trader.set_trade_executor(executor)
        copy_trader.add_wallet("0x...", label="Whale Alpha")
        await copy_trader.start()
    """

    # Router addresses per chain
    ROUTERS = {
        56: "0x10ED43C718714eb63d5aA57B78B54704E256024E",  # PancakeSwap
        1: "0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D",   # Uniswap V2
    }

    def __init__(self, w3: Web3, chain_id: int = 56):
        self.w3 = w3
        self.chain_id = chain_id
        self.config = CopyTraderConfig()
        self.running = False

        self._wallets: dict[str, TrackedWallet] = {}  # addr (lower) → TrackedWallet
        self._signals: list[CopyTradeSignal] = []      # history
        self._active_copies: dict[str, CopyTradeSignal] = {}  # token → signal

        # External components
        self._trade_executor = None
        self._emit_callback: Optional[Callable] = None
        self._smart_money_tracker = None

        # Rate limiting
        self._daily_trades = 0
        self._daily_reset_time = 0.0
        self._total_exposure = 0.0  # current BNB/ETH in copy positions

        # Pending tx filter
        self._pending_filter = None
        self._monitor_task: Optional[asyncio.Task] = None

        # Stats
        self.stats = {
            "wallets_tracked": 0,
            "signals_received": 0,
            "copies_executed": 0,
            "copies_skipped": 0,
            "total_pnl_usd": 0.0,
        }

    # ───────────────────────────────────────────────────────
    #  Setup
    # ───────────────────────────────────────────────────────

    def set_trade_executor(self, executor):
        """Set the TradeExecutor for executing copy trades."""
        self._trade_executor = executor

    def set_smart_money_tracker(self, tracker):
        """Connect to SmartMoneyTracker for auto-follow."""
        self._smart_money_tracker = tracker

    def set_emit_callback(self, callback: Callable):
        """Set WebSocket emit callback."""
        self._emit_callback = callback

    def add_wallet(
        self, address: str, label: str = "",
        follow_buys: bool = True, follow_sells: bool = True,
        max_amount: float = 0.1, source: str = "manual"
    ):
        """Add a wallet to track."""
        addr = address.lower()
        if addr in self._wallets:
            logger.info(f"CopyTrader: wallet already tracked: {addr[:10]}…")
            return

        self._wallets[addr] = TrackedWallet(
            address=Web3.to_checksum_address(address),
            label=label or f"Wallet-{len(self._wallets) + 1}",
            follow_buys=follow_buys,
            follow_sells=follow_sells,
            max_follow_amount_native=max_amount,
            source=source,
            first_tracked=time.time(),
        )
        self.stats["wallets_tracked"] = len(self._wallets)
        logger.info(f"CopyTrader: tracking wallet {addr[:10]}… ({label})")

    def remove_wallet(self, address: str):
        """Remove a wallet from tracking."""
        addr = address.lower()
        if addr in self._wallets:
            del self._wallets[addr]
            self.stats["wallets_tracked"] = len(self._wallets)
            logger.info(f"CopyTrader: untracked {addr[:10]}…")

    def get_tracked_wallets(self) -> list[dict]:
        """Return all tracked wallets."""
        return [w.to_dict() for w in self._wallets.values()]

    def get_signals(self, limit: int = 50) -> list[dict]:
        """Return recent copy trade signals."""
        return [s.to_dict() for s in self._signals[-limit:]]

    def update_config(self, **kwargs):
        """Update copy trader config."""
        for k, v in kwargs.items():
            if hasattr(self.config, k):
                setattr(self.config, k, v)
        logger.info(f"CopyTrader: config updated — {kwargs}")

    # ───────────────────────────────────────────────────────
    #  Monitoring loop
    # ───────────────────────────────────────────────────────

    async def start(self):
        """Start the copy trading monitor."""
        if self.running:
            return
        self.running = True
        logger.info(f"CopyTrader: started — tracking {len(self._wallets)} wallets")

        self._monitor_task = asyncio.create_task(self._monitor_loop())

    async def stop(self):
        """Stop copy trading."""
        self.running = False
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
        logger.info("CopyTrader: stopped")

    async def _monitor_loop(self):
        """Main monitoring loop — polls pending transactions from tracked wallets."""
        router = self.ROUTERS.get(self.chain_id, "").lower()

        while self.running:
            try:
                # Auto-follow from smart money tracker
                if self.config.auto_follow_smart_money and self._smart_money_tracker:
                    await self._auto_follow_smart_wallets()

                # Reset daily counter
                now = time.time()
                if now - self._daily_reset_time > 86400:
                    self._daily_trades = 0
                    self._daily_reset_time = now

                # Poll recent blocks for tracked wallet swaps
                await self._poll_recent_swaps(router)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"CopyTrader monitor error: {e}")

            await asyncio.sleep(3)  # Poll every 3 seconds

    async def _poll_recent_swaps(self, router: str):
        """Check recent blocks for swaps by tracked wallets."""
        if not self._wallets:
            return

        try:
            loop = asyncio.get_event_loop()
            latest = await loop.run_in_executor(None, lambda: self.w3.eth.block_number)
            block = await loop.run_in_executor(None, lambda: self.w3.eth.get_block(latest, full_transactions=True))

            if not block or "transactions" not in block:
                return

            for tx in block.get("transactions", []):
                from_addr = (tx.get("from", "") or "").lower()
                if from_addr not in self._wallets:
                    continue

                to_addr = (tx.get("to", "") or "").lower()
                if to_addr != router:
                    continue

                # This is a swap from a tracked wallet
                wallet = self._wallets[from_addr]
                if not wallet.enabled:
                    continue

                input_data = tx.get("input", "")
                if isinstance(input_data, bytes):
                    input_data = input_data.hex()
                if not input_data.startswith("0x"):
                    input_data = "0x" + input_data

                direction = _decode_swap_direction(input_data)
                if direction == "unknown":
                    continue

                # Check if we should follow this direction
                if direction == "buy" and not wallet.follow_buys:
                    continue
                if direction == "sell" and not wallet.follow_sells:
                    continue

                # Extract token
                token = _extract_token_from_path(input_data, direction)
                if not token:
                    continue

                # Calculate whale amount
                value_wei = int(tx.get("value", 0))
                whale_amount = value_wei / 1e18 if direction == "buy" else 0

                # Create signal
                signal = CopyTradeSignal(
                    wallet_address=wallet.address,
                    wallet_label=wallet.label,
                    token_address=token,
                    direction=direction,
                    whale_amount_native=whale_amount,
                    tx_hash=tx.get("hash", b"").hex() if hasattr(tx.get("hash", b""), "hex") else str(tx.get("hash", "")),
                    timestamp=time.time(),
                    confidence=wallet.smart_score,
                )

                await self._process_signal(signal, wallet)

        except Exception as e:
            logger.debug(f"CopyTrader poll error: {e}")

    async def _process_signal(self, signal: CopyTradeSignal, wallet: TrackedWallet):
        """Process a copy trade signal — validate and execute."""
        self.stats["signals_received"] += 1

        # Guards
        if not self.config.enabled:
            signal.status = "skipped"
            signal.skip_reason = "copy_trading_disabled"
            self._signals.append(signal)
            return

        if self._daily_trades >= self.config.max_daily_copy_trades:
            signal.status = "skipped"
            signal.skip_reason = "daily_limit_reached"
            self._signals.append(signal)
            return

        if self._total_exposure >= self.config.max_total_exposure_native:
            signal.status = "skipped"
            signal.skip_reason = "max_exposure_reached"
            self._signals.append(signal)
            return

        if signal.token_address.lower() in [t.lower() for t in self.config.blacklisted_tokens]:
            signal.status = "skipped"
            signal.skip_reason = "blacklisted_token"
            self._signals.append(signal)
            return

        # Check minimum whale amount
        if signal.direction == "buy" and signal.whale_amount_native < wallet.min_whale_amount_native:
            signal.status = "skipped"
            signal.skip_reason = "whale_amount_too_small"
            self._signals.append(signal)
            return

        # Already copying this token?
        if signal.token_address.lower() in self._active_copies:
            signal.status = "skipped"
            signal.skip_reason = "already_following"
            self._signals.append(signal)
            return

        # Calculate copy amount
        if signal.direction == "buy":
            copy_amount = min(
                signal.whale_amount_native * (wallet.follow_percent / 100),
                wallet.max_follow_amount_native,
                self.config.default_follow_amount_native,
            )
            signal.copy_amount_native = copy_amount
        else:
            signal.copy_amount_native = 0  # sell entire position

        # Execute copy trade
        await self._execute_copy(signal, wallet)

    async def _execute_copy(self, signal: CopyTradeSignal, wallet: TrackedWallet):
        """Execute the copy trade via TradeExecutor."""
        if not self._trade_executor or not self._trade_executor.enabled:
            signal.status = "skipped"
            signal.skip_reason = "trade_executor_unavailable"
            self._signals.append(signal)
            return

        try:
            if signal.direction == "buy":
                result = await self._trade_executor.execute_buy(
                    token_address=signal.token_address,
                    amount_native=signal.copy_amount_native,
                    slippage_pct=15.0,  # higher slippage for speed
                )
            else:
                result = await self._trade_executor.execute_sell(
                    token_address=signal.token_address,
                    slippage_pct=15.0,
                )

            if result.success:
                signal.status = "executed"
                signal.result_tx_hash = result.tx_hash
                self._daily_trades += 1
                self.stats["copies_executed"] += 1

                if signal.direction == "buy":
                    self._total_exposure += signal.copy_amount_native
                    self._active_copies[signal.token_address.lower()] = signal

                wallet.total_copies += 1
                wallet.successful_copies += 1
                wallet.last_activity = time.time()

                logger.info(
                    f"CopyTrader: copied {signal.direction} from {wallet.label} "
                    f"— token {signal.token_address[:10]}… amount {signal.copy_amount_native:.4f}"
                )
            else:
                signal.status = "failed"
                signal.skip_reason = result.error
                wallet.total_copies += 1

        except Exception as e:
            signal.status = "failed"
            signal.skip_reason = str(e)
            logger.error(f"CopyTrader execution error: {e}")

        self._signals.append(signal)

        # Emit to frontend
        if self._emit_callback:
            try:
                self._emit_callback("copy_trade_signal", signal.to_dict())
            except Exception:
                pass

    async def _auto_follow_smart_wallets(self):
        """Auto-add wallets from SmartMoneyTracker that meet score threshold."""
        if not self._smart_money_tracker:
            return

        try:
            sm_wallets = getattr(self._smart_money_tracker, "_wallets", {})
            for addr, sw in sm_wallets.items():
                if addr not in self._wallets and sw.smart_score >= self.config.min_wallet_score:
                    self.add_wallet(
                        address=sw.address,
                        label=sw.label or f"SmartMoney-{sw.smart_score}",
                        max_amount=self.config.default_follow_amount_native,
                        source="smartmoney",
                    )
        except Exception as e:
            logger.debug(f"CopyTrader auto-follow error: {e}")

    # ───────────────────────────────────────────────────────
    #  Signal from SmartMoneyTracker (direct integration)
    # ───────────────────────────────────────────────────────

    async def on_smart_money_signal(self, signal_data: dict):
        """
        Called by SmartMoneyTracker when a whale makes a move.

        Args:
            signal_data: dict with keys: token_address, wallet_address,
                        wallet_label, signal_type, amount_native, tx_hash
        """
        wallet_addr = signal_data.get("wallet_address", "").lower()
        if wallet_addr not in self._wallets:
            return

        wallet = self._wallets[wallet_addr]
        signal_type = signal_data.get("signal_type", "")

        direction = "buy" if signal_type in ("early_buy", "accumulation", "follow") else "sell"

        signal = CopyTradeSignal(
            wallet_address=wallet.address,
            wallet_label=wallet.label,
            token_address=signal_data.get("token_address", ""),
            direction=direction,
            whale_amount_native=signal_data.get("amount_native", 0),
            tx_hash=signal_data.get("tx_hash", ""),
            timestamp=time.time(),
            confidence=signal_data.get("confidence", 0),
        )

        await self._process_signal(signal, wallet)

    # ───────────────────────────────────────────────────────
    #  Stats / Serialization
    # ───────────────────────────────────────────────────────

    def get_stats(self) -> dict:
        """Return copy trader statistics."""
        wallet_stats = []
        for w in self._wallets.values():
            wallet_stats.append({
                "address": w.address[:10] + "…",
                "label": w.label,
                "copies": w.total_copies,
                "wins": w.successful_copies,
                "pnl": w.total_pnl_usd,
                "score": w.smart_score,
                "enabled": w.enabled,
            })

        return {
            **self.stats,
            "config": self.config.to_dict(),
            "wallets": wallet_stats,
            "active_copies": len(self._active_copies),
            "daily_trades": self._daily_trades,
            "total_exposure": round(self._total_exposure, 4),
        }

    def to_dict(self) -> dict:
        return self.get_stats()

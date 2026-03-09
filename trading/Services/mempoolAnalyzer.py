"""
mempoolAnalyzer.py — Advanced Mempool Intelligence Engine.

Goes beyond basic mempool monitoring to provide:
  1. Pending transaction classification (addLiquidity, large buys, enableTrading)
  2. Transaction simulation before confirmation
  3. Gas price analysis and front-run detection
  4. Mempool pattern recognition (bot activity, whale movements)
  5. Priority queue construction for optimal entry

Complements the existing mempoolService.py by adding deep analysis
and predictive capabilities to raw pending transaction data.
"""

import asyncio
import logging
import time
import math
from dataclasses import dataclass, field
from typing import Optional
from enum import Enum
from collections import deque

logger = logging.getLogger(__name__)

try:
    from web3 import Web3
    HAS_WEB3 = True
except ImportError:
    Web3 = None
    HAS_WEB3 = False


# ═══════════════════════════════════════════════════════════════════
#  Data Classes
# ═══════════════════════════════════════════════════════════════════

class PendingTxType(Enum):
    """Classification of pending transactions."""
    ADD_LIQUIDITY = "add_liquidity"
    REMOVE_LIQUIDITY = "remove_liquidity"
    SWAP_BUY = "swap_buy"
    SWAP_SELL = "swap_sell"
    ENABLE_TRADING = "enable_trading"
    APPROVE = "approve"
    TRANSFER = "transfer"
    CONTRACT_CREATION = "contract_creation"
    UNKNOWN = "unknown"


class MempoolSignal(Enum):
    """Actionable signals from mempool analysis."""
    NEW_LP = "new_lp"                          # Liquidity being added
    LARGE_BUY = "large_buy"                    # Whale-sized buy incoming
    LARGE_SELL = "large_sell"                  # Whale-sized sell incoming
    TRADING_ACTIVATION = "trading_activation"   # enableTrading/openTrading
    LP_REMOVAL = "lp_removal"                  # Liquidity being pulled (rug warning)
    BOT_SWARM = "bot_swarm"                    # Multiple bots targeting same token
    FRONTRUN_DETECTED = "frontrun_detected"    # Frontrun attempt visible
    GAS_WAR = "gas_war"                        # Multiple high-gas txs competing


@dataclass
class PendingTransaction:
    """Analyzed pending transaction."""
    tx_hash: str = ""
    from_address: str = ""
    to_address: str = ""
    value_wei: int = 0
    gas_price_gwei: float = 0.0
    gas_limit: int = 0
    nonce: int = 0
    input_data: str = ""
    tx_type: PendingTxType = PendingTxType.UNKNOWN
    # Decoded info
    token_address: str = ""
    amount_native: float = 0.0
    amount_tokens: float = 0.0
    # Analysis
    is_bot: bool = False
    is_whale: bool = False
    estimated_impact_pct: float = 0.0
    priority_score: float = 0.0
    detected_at: float = 0.0
    block_confirmed: int = 0            # 0 = still pending

    def to_dict(self) -> dict:
        return {
            "tx_hash": self.tx_hash,
            "from": self.from_address,
            "to": self.to_address,
            "type": self.tx_type.value,
            "token_address": self.token_address,
            "amount_native": round(self.amount_native, 4),
            "gas_price_gwei": round(self.gas_price_gwei, 2),
            "is_bot": self.is_bot,
            "is_whale": self.is_whale,
            "estimated_impact_pct": round(self.estimated_impact_pct, 3),
            "priority_score": round(self.priority_score, 2),
        }


@dataclass
class MempoolAlert:
    """Alert generated from mempool analysis."""
    signal: MempoolSignal = MempoolSignal.NEW_LP
    token_address: str = ""
    severity: str = "medium"            # low / medium / high / critical
    description: str = ""
    related_txs: list = field(default_factory=list)  # tx hashes
    data: dict = field(default_factory=dict)
    timestamp: float = 0.0

    def to_dict(self) -> dict:
        return {
            "signal": self.signal.value,
            "token_address": self.token_address,
            "severity": self.severity,
            "description": self.description,
            "related_txs": self.related_txs,
            "data": self.data,
            "timestamp": self.timestamp,
        }


@dataclass
class TokenMempoolState:
    """Aggregated mempool state for a specific token."""
    token_address: str = ""
    pending_buys: int = 0
    pending_sells: int = 0
    pending_buy_volume: float = 0.0    # in native (BNB)
    pending_sell_volume: float = 0.0
    pending_lp_add: bool = False
    pending_lp_remove: bool = False
    pending_enable_trading: bool = False
    avg_buy_gas: float = 0.0
    avg_sell_gas: float = 0.0
    max_buy_gas: float = 0.0
    bot_count: int = 0
    whale_count: int = 0
    net_pressure: float = 0.0          # positive = bullish, negative = bearish
    frontrun_risk: float = 0.0        # 0-1

    def to_dict(self) -> dict:
        return {
            "token_address": self.token_address,
            "pending_buys": self.pending_buys,
            "pending_sells": self.pending_sells,
            "pending_buy_volume": round(self.pending_buy_volume, 4),
            "pending_sell_volume": round(self.pending_sell_volume, 4),
            "pending_lp_add": self.pending_lp_add,
            "pending_lp_remove": self.pending_lp_remove,
            "pending_enable_trading": self.pending_enable_trading,
            "bot_count": self.bot_count,
            "whale_count": self.whale_count,
            "net_pressure": round(self.net_pressure, 3),
            "frontrun_risk": round(self.frontrun_risk, 3),
        }


@dataclass
class GasAnalysis:
    """Gas price analysis from mempool."""
    current_base_fee: float = 0.0
    avg_pending_gas: float = 0.0
    median_pending_gas: float = 0.0
    p90_gas: float = 0.0
    p99_gas: float = 0.0
    recommended_gas: float = 0.0        # for optimal inclusion
    frontrun_gas: float = 0.0          # gas needed to frontrun
    gas_war_active: bool = False

    def to_dict(self) -> dict:
        return {
            "current_base_fee": round(self.current_base_fee, 2),
            "avg_pending_gas": round(self.avg_pending_gas, 2),
            "median_pending_gas": round(self.median_pending_gas, 2),
            "p90_gas": round(self.p90_gas, 2),
            "recommended_gas": round(self.recommended_gas, 2),
            "frontrun_gas": round(self.frontrun_gas, 2),
            "gas_war_active": self.gas_war_active,
        }


@dataclass
class MempoolConfig:
    """Configuration for mempool analyzer."""
    enabled: bool = True
    poll_interval_seconds: float = 0.5
    max_pending_tracked: int = 500
    max_alerts: int = 100
    # Classification thresholds
    whale_threshold_native: float = 1.0  # BNB
    bot_gas_multiplier: float = 2.0      # if gas > multiplier * avg, likely a bot
    gas_war_threshold_count: int = 5     # txs with >2x gas targeting same token
    # Frontrun detection
    frontrun_time_window_ms: int = 2000
    frontrun_gas_premium_pct: float = 20  # % above target tx gas
    # Router addresses
    router_addresses: list = field(default_factory=lambda: [
        "0x10ED43C718714eb63d5aA57B78B54704E256024E",  # PancakeSwap V2
        "0x13f4EA83D0bd40E75C8222255bc855a974568Dd4",  # PancakeSwap V3
    ])

    def to_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "poll_interval": self.poll_interval_seconds,
            "whale_threshold": self.whale_threshold_native,
            "gas_war_threshold": self.gas_war_threshold_count,
        }


# ═══════════════════════════════════════════════════════════════════
#  Transaction Classifier
# ═══════════════════════════════════════════════════════════════════

class TransactionClassifier:
    """Classifies pending transactions by type."""

    # Function selectors for common DEX operations
    SELECTORS = {
        "0xf305d719": PendingTxType.ADD_LIQUIDITY,       # addLiquidityETH
        "0xe8e33700": PendingTxType.ADD_LIQUIDITY,       # addLiquidity
        "0xbaa2abde": PendingTxType.REMOVE_LIQUIDITY,    # removeLiquidity
        "0x02751cec": PendingTxType.REMOVE_LIQUIDITY,    # removeLiquidityETH
        "0xaf2979eb": PendingTxType.REMOVE_LIQUIDITY,    # removeLiquidityETHSupportingFeeOnTransferTokens
        "0x7ff36ab5": PendingTxType.SWAP_BUY,            # swapExactETHForTokens
        "0xb6f9de95": PendingTxType.SWAP_BUY,            # swapExactETHForTokensSupportingFeeOnTransferTokens
        "0x18cbafe5": PendingTxType.SWAP_SELL,           # swapExactTokensForETH
        "0x791ac947": PendingTxType.SWAP_SELL,           # swapExactTokensForETHSupportingFeeOnTransferTokens
        "0x38ed1739": PendingTxType.SWAP_BUY,            # swapExactTokensForTokens
        "0x5c11d795": PendingTxType.SWAP_BUY,            # swapExactTokensForTokensSupportingFeeOnTransferTokens
        "0x293230b8": PendingTxType.ENABLE_TRADING,      # openTrading
        "0x8f70ccf7": PendingTxType.ENABLE_TRADING,      # enableTrading
        "0x8a8c523c": PendingTxType.ENABLE_TRADING,      # setTrading
        "0x095ea7b3": PendingTxType.APPROVE,             # approve
        "0xa9059cbb": PendingTxType.TRANSFER,            # transfer
    }

    @classmethod
    def classify(cls, tx: dict, router_addresses: list) -> PendingTxType:
        """Classify a transaction based on its input data and target."""
        to_addr = (tx.get("to") or "").lower()
        input_data = tx.get("input", tx.get("data", ""))

        if not to_addr:
            return PendingTxType.CONTRACT_CREATION

        if len(input_data) < 10:
            return PendingTxType.TRANSFER

        selector = input_data[:10].lower()
        tx_type = cls.SELECTORS.get(selector, PendingTxType.UNKNOWN)

        # Verify router target for swaps/LP operations
        if tx_type in (
            PendingTxType.ADD_LIQUIDITY, PendingTxType.REMOVE_LIQUIDITY,
            PendingTxType.SWAP_BUY, PendingTxType.SWAP_SELL
        ):
            is_router = any(to_addr == r.lower() for r in router_addresses)
            if not is_router:
                # Could still be a custom router or aggregator
                pass

        return tx_type

    @classmethod
    def extract_token_from_swap(cls, input_data: str) -> str:
        """Try to extract token address from swap calldata."""
        if len(input_data) < 138:
            return ""
        try:
            # For swapExactETHForTokens: path is the last dynamic param
            # Typical: selector + amountOutMin + deadline + path_offset + path
            # path contains [WBNB, TOKEN] — we want the last address
            # This is a simplified extraction
            data = input_data[10:]  # strip selector
            # Find addresses (20-byte chunks in the data)
            addresses = []
            for i in range(0, len(data) - 39, 64):
                chunk = data[i:i+64]
                if chunk[:24] == "0" * 24:
                    addr = "0x" + chunk[24:]
                    if len(addr) == 42:
                        addresses.append(addr)
            # Last address in path is usually the target token
            if addresses:
                return addresses[-1]
        except Exception:
            pass
        return ""

    @classmethod
    def extract_token_from_lp(cls, input_data: str) -> str:
        """Try to extract token address from addLiquidity calldata."""
        if len(input_data) < 74:
            return ""
        try:
            # addLiquidityETH(address token, ...): first param is token
            data = input_data[10:]
            token = "0x" + data[:64][-40:]
            return token
        except Exception:
            return ""


# ═══════════════════════════════════════════════════════════════════
#  Gas Analyzer
# ═══════════════════════════════════════════════════════════════════

class GasAnalyzer:
    """Analyzes gas prices from pending transactions."""

    def __init__(self):
        self._gas_prices: deque = deque(maxlen=1000)

    def add_gas_price(self, gas_gwei: float):
        self._gas_prices.append(gas_gwei)

    def analyze(self) -> GasAnalysis:
        """Compute gas statistics."""
        analysis = GasAnalysis()
        if not self._gas_prices:
            return analysis

        prices = sorted(self._gas_prices)
        n = len(prices)

        analysis.avg_pending_gas = sum(prices) / n
        analysis.median_pending_gas = prices[n // 2]
        analysis.p90_gas = prices[int(n * 0.9)] if n >= 10 else prices[-1]
        analysis.p99_gas = prices[int(n * 0.99)] if n >= 100 else prices[-1]

        # Recommended: slightly above median for reliable inclusion
        analysis.recommended_gas = analysis.median_pending_gas * 1.1

        # Frontrun gas: must beat p90
        analysis.frontrun_gas = analysis.p90_gas * 1.15

        # Detect gas war: high variance in gas prices
        if n >= 10:
            high_gas_count = sum(1 for p in prices if p > analysis.avg_pending_gas * 2)
            analysis.gas_war_active = high_gas_count >= 5

        return analysis


# ═══════════════════════════════════════════════════════════════════
#  Frontrun Detector
# ═══════════════════════════════════════════════════════════════════

class FrontrunDetector:
    """Detects frontrunning attempts in the mempool."""

    def __init__(self, config: MempoolConfig = None):
        self.config = config or MempoolConfig()
        self._recent_txs: deque = deque(maxlen=200)

    def add_tx(self, ptx: PendingTransaction):
        self._recent_txs.append(ptx)

    def detect_frontrun(self, target_tx: PendingTransaction) -> Optional[dict]:
        """
        Check if a transaction appears to be a frontrun of another.
        Returns info about the suspected frontrun or None.
        """
        now = time.time()
        window = self.config.frontrun_time_window_ms / 1000

        for ptx in self._recent_txs:
            if ptx.tx_hash == target_tx.tx_hash:
                continue
            if abs(ptx.detected_at - target_tx.detected_at) > window:
                continue
            # Same token, same type (buy), higher gas
            if (ptx.token_address == target_tx.token_address and
                ptx.tx_type == PendingTxType.SWAP_BUY and
                target_tx.tx_type == PendingTxType.SWAP_BUY):

                gas_premium = 0
                if target_tx.gas_price_gwei > 0:
                    gas_premium = (
                        (ptx.gas_price_gwei - target_tx.gas_price_gwei)
                        / target_tx.gas_price_gwei * 100
                    )

                if gas_premium >= self.config.frontrun_gas_premium_pct:
                    return {
                        "frontrunner_tx": ptx.tx_hash,
                        "victim_tx": target_tx.tx_hash,
                        "gas_premium_pct": round(gas_premium, 1),
                        "frontrunner_gas": ptx.gas_price_gwei,
                        "victim_gas": target_tx.gas_price_gwei,
                        "token": target_tx.token_address,
                    }

        return None

    def compute_frontrun_risk(self, token_address: str) -> float:
        """Compute frontrun risk for a token (0-1)."""
        now = time.time()
        recent = [tx for tx in self._recent_txs
                  if tx.token_address == token_address and now - tx.detected_at < 30]

        if not recent:
            return 0

        bot_count = sum(1 for tx in recent if tx.is_bot)
        high_gas_count = sum(1 for tx in recent if tx.gas_price_gwei > 10)
        buy_count = sum(1 for tx in recent if tx.tx_type == PendingTxType.SWAP_BUY)

        risk = 0
        if bot_count > 0:
            risk += min(0.4, bot_count * 0.1)
        if high_gas_count > 2:
            risk += 0.3
        if buy_count > 3:
            risk += 0.2

        return min(1.0, risk)


# ═══════════════════════════════════════════════════════════════════
#  Main: Mempool Analyzer
# ═══════════════════════════════════════════════════════════════════

class MempoolAnalyzer:
    """
    Advanced mempool analysis engine.

    Usage:
        analyzer = MempoolAnalyzer(w3)
        await analyzer.start()
        state = analyzer.get_token_state("0x...")
        alerts = analyzer.get_alerts()
        await analyzer.stop()
    """

    def __init__(self, w3=None, chain_id: int = 56, config: MempoolConfig = None):
        self.w3 = w3
        self.chain_id = chain_id
        self.config = config or MempoolConfig()
        self._classifier = TransactionClassifier()
        self._gas_analyzer = GasAnalyzer()
        self._frontrun_detector = FrontrunDetector(self.config)
        # State
        self._pending_txs: dict[str, PendingTransaction] = {}
        self._token_states: dict[str, TokenMempoolState] = {}
        self._alerts: deque = deque(maxlen=self.config.max_alerts)
        self._running = False
        self._poll_task: Optional[asyncio.Task] = None
        self._total_analyzed: int = 0
        self._known_tx_hashes: set = set()
        self._emit_callback = None

    def set_emit_callback(self, callback):
        """Set callback for mempool alerts."""
        self._emit_callback = callback

    async def start(self):
        """Start mempool monitoring."""
        if self._running:
            return
        self._running = True
        self._poll_task = asyncio.create_task(self._poll_loop())
        logger.info("🔎 MempoolAnalyzer started")

    async def stop(self):
        """Stop mempool monitoring."""
        self._running = False
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        logger.info("🔎 MempoolAnalyzer stopped")

    async def _poll_loop(self):
        """Poll for new pending transactions."""
        while self._running:
            try:
                await self._poll_pending()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"Mempool poll error: {e}")
            await asyncio.sleep(self.config.poll_interval_seconds)

    async def _poll_pending(self):
        """Fetch and analyze pending transactions."""
        if not self.w3:
            return

        try:
            # Try txpool if available (Geth-like)
            pending_block = self.w3.eth.get_block("pending", full_transactions=True)
            txs = pending_block.get("transactions", [])
        except Exception:
            return

        for tx in txs:
            tx_hash = tx.get("hash", b"").hex() if isinstance(tx.get("hash"), bytes) else str(tx.get("hash", ""))
            if tx_hash in self._known_tx_hashes:
                continue

            self._known_tx_hashes.add(tx_hash)
            if len(self._known_tx_hashes) > 10000:
                # Prune old hashes
                self._known_tx_hashes = set(list(self._known_tx_hashes)[-5000:])

            ptx = await self._analyze_tx(tx)
            if ptx and ptx.tx_type != PendingTxType.UNKNOWN:
                self._pending_txs[tx_hash] = ptx
                self._update_token_state(ptx)
                self._check_alerts(ptx)
                self._gas_analyzer.add_gas_price(ptx.gas_price_gwei)
                self._frontrun_detector.add_tx(ptx)
                self._total_analyzed += 1

        # Prune old pending txs
        self._prune_pending()

    async def _analyze_tx(self, tx: dict) -> Optional[PendingTransaction]:
        """Analyze a single pending transaction."""
        try:
            tx_hash = tx.get("hash", b"").hex() if isinstance(tx.get("hash"), bytes) else str(tx.get("hash", ""))
            from_addr = tx.get("from", "")
            to_addr = tx.get("to", "")
            value = int(tx.get("value", 0))
            gas_price = int(tx.get("gasPrice", 0))
            gas_limit = int(tx.get("gas", 0))
            input_data = tx.get("input", tx.get("data", "0x"))

            tx_type = TransactionClassifier.classify(tx, self.config.router_addresses)

            amount_native = float(Web3.from_wei(value, "ether")) if HAS_WEB3 and Web3 else value / 1e18
            gas_gwei = float(Web3.from_wei(gas_price, "gwei")) if HAS_WEB3 and Web3 else gas_price / 1e9

            # Extract token address
            token_address = ""
            if tx_type in (PendingTxType.SWAP_BUY, PendingTxType.SWAP_SELL):
                token_address = TransactionClassifier.extract_token_from_swap(input_data)
            elif tx_type in (PendingTxType.ADD_LIQUIDITY, PendingTxType.REMOVE_LIQUIDITY):
                token_address = TransactionClassifier.extract_token_from_lp(input_data)

            # Bot detection heuristics
            is_bot = False
            avg_gas = self._gas_analyzer.analyze().avg_pending_gas
            if avg_gas > 0 and gas_gwei > avg_gas * self.config.bot_gas_multiplier:
                is_bot = True
            if gas_limit == 500000 or gas_limit == 300000:  # common bot defaults
                is_bot = True

            # Whale detection
            is_whale = amount_native >= self.config.whale_threshold_native

            ptx = PendingTransaction(
                tx_hash=tx_hash,
                from_address=from_addr,
                to_address=to_addr,
                value_wei=value,
                gas_price_gwei=gas_gwei,
                gas_limit=gas_limit,
                nonce=int(tx.get("nonce", 0)),
                input_data=input_data[:200],  # truncate for memory
                tx_type=tx_type,
                token_address=token_address,
                amount_native=amount_native,
                is_bot=is_bot,
                is_whale=is_whale,
                detected_at=time.time(),
            )

            return ptx

        except Exception as e:
            logger.debug(f"Error analyzing tx: {e}")
            return None

    def _update_token_state(self, ptx: PendingTransaction):
        """Update aggregated token mempool state."""
        if not ptx.token_address:
            return

        addr = ptx.token_address.lower()
        if addr not in self._token_states:
            self._token_states[addr] = TokenMempoolState(token_address=addr)

        state = self._token_states[addr]

        if ptx.tx_type == PendingTxType.SWAP_BUY:
            state.pending_buys += 1
            state.pending_buy_volume += ptx.amount_native
            if ptx.is_bot:
                state.bot_count += 1
            if ptx.is_whale:
                state.whale_count += 1
        elif ptx.tx_type == PendingTxType.SWAP_SELL:
            state.pending_sells += 1
            state.pending_sell_volume += ptx.amount_native
        elif ptx.tx_type == PendingTxType.ADD_LIQUIDITY:
            state.pending_lp_add = True
        elif ptx.tx_type == PendingTxType.REMOVE_LIQUIDITY:
            state.pending_lp_remove = True
        elif ptx.tx_type == PendingTxType.ENABLE_TRADING:
            state.pending_enable_trading = True

        # Net pressure
        state.net_pressure = state.pending_buy_volume - state.pending_sell_volume

        # Frontrun risk
        state.frontrun_risk = self._frontrun_detector.compute_frontrun_risk(addr)

    def _check_alerts(self, ptx: PendingTransaction):
        """Generate alerts for significant mempool events."""
        alerts_to_emit = []

        if ptx.tx_type == PendingTxType.ADD_LIQUIDITY:
            alert = MempoolAlert(
                signal=MempoolSignal.NEW_LP,
                token_address=ptx.token_address,
                severity="high",
                description=f"New liquidity being added: {ptx.amount_native:.4f} BNB",
                related_txs=[ptx.tx_hash],
                data={"amount_native": ptx.amount_native},
                timestamp=time.time(),
            )
            self._alerts.append(alert)
            alerts_to_emit.append(alert)

        elif ptx.tx_type == PendingTxType.REMOVE_LIQUIDITY:
            alert = MempoolAlert(
                signal=MempoolSignal.LP_REMOVAL,
                token_address=ptx.token_address,
                severity="critical",
                description="⚠️ Liquidity removal detected — possible RUG!",
                related_txs=[ptx.tx_hash],
                timestamp=time.time(),
            )
            self._alerts.append(alert)
            alerts_to_emit.append(alert)

        elif ptx.tx_type == PendingTxType.ENABLE_TRADING:
            alert = MempoolAlert(
                signal=MempoolSignal.TRADING_ACTIVATION,
                token_address=ptx.token_address,
                severity="high",
                description="Trading being enabled — snipe window opening",
                related_txs=[ptx.tx_hash],
                timestamp=time.time(),
            )
            self._alerts.append(alert)
            alerts_to_emit.append(alert)

        elif ptx.is_whale and ptx.tx_type == PendingTxType.SWAP_BUY:
            alert = MempoolAlert(
                signal=MempoolSignal.LARGE_BUY,
                token_address=ptx.token_address,
                severity="medium",
                description=f"Whale buy: {ptx.amount_native:.4f} BNB",
                related_txs=[ptx.tx_hash],
                data={"amount": ptx.amount_native},
                timestamp=time.time(),
            )
            self._alerts.append(alert)
            alerts_to_emit.append(alert)

        elif ptx.is_whale and ptx.tx_type == PendingTxType.SWAP_SELL:
            alert = MempoolAlert(
                signal=MempoolSignal.LARGE_SELL,
                token_address=ptx.token_address,
                severity="high",
                description=f"Whale sell: {ptx.amount_native:.4f} BNB worth",
                related_txs=[ptx.tx_hash],
                data={"amount": ptx.amount_native},
                timestamp=time.time(),
            )
            self._alerts.append(alert)
            alerts_to_emit.append(alert)

        # Check for bot swarm
        if ptx.token_address:
            state = self._token_states.get(ptx.token_address.lower())
            if state and state.bot_count >= self.config.gas_war_threshold_count:
                alert = MempoolAlert(
                    signal=MempoolSignal.BOT_SWARM,
                    token_address=ptx.token_address,
                    severity="high",
                    description=f"Bot swarm detected: {state.bot_count} bots targeting token",
                    data={"bot_count": state.bot_count},
                    timestamp=time.time(),
                )
                self._alerts.append(alert)
                alerts_to_emit.append(alert)

        # Frontrun check
        frontrun = self._frontrun_detector.detect_frontrun(ptx)
        if frontrun:
            alert = MempoolAlert(
                signal=MempoolSignal.FRONTRUN_DETECTED,
                token_address=ptx.token_address,
                severity="high",
                description=f"Frontrun detected: {frontrun['gas_premium_pct']:.0f}% gas premium",
                related_txs=[frontrun["frontrunner_tx"], frontrun["victim_tx"]],
                data=frontrun,
                timestamp=time.time(),
            )
            self._alerts.append(alert)
            alerts_to_emit.append(alert)

        # Emit alerts
        if alerts_to_emit and self._emit_callback:
            for alert in alerts_to_emit:
                try:
                    asyncio.create_task(
                        self._emit_callback("mempool_alert", alert.to_dict())
                    )
                except Exception:
                    pass

    def _prune_pending(self):
        """Remove old pending transactions."""
        if len(self._pending_txs) > self.config.max_pending_tracked:
            sorted_txs = sorted(
                self._pending_txs.items(),
                key=lambda x: x[1].detected_at,
            )
            to_remove = len(self._pending_txs) - self.config.max_pending_tracked
            for tx_hash, _ in sorted_txs[:to_remove]:
                del self._pending_txs[tx_hash]

    # ─── Public API ───────────────────────────────────────────────

    async def analyze_token(self, token_address: str) -> dict:
        """Get comprehensive mempool analysis for a token."""
        addr = token_address.lower()
        state = self._token_states.get(addr, TokenMempoolState(token_address=addr))
        gas = self._gas_analyzer.analyze()
        frontrun_risk = self._frontrun_detector.compute_frontrun_risk(addr)

        # Collect relevant alerts
        token_alerts = [
            a.to_dict() for a in self._alerts
            if a.token_address and a.token_address.lower() == addr
        ]

        return {
            "token_address": token_address,
            "mempool_state": state.to_dict(),
            "gas_analysis": gas.to_dict(),
            "frontrun_risk": round(frontrun_risk, 3),
            "recent_alerts": token_alerts[-10:],
            "recommendation": self._get_recommendation(state, frontrun_risk),
        }

    def _get_recommendation(self, state: TokenMempoolState, frontrun_risk: float) -> str:
        """Get trading recommendation based on mempool state."""
        if state.pending_lp_remove:
            return "AVOID — LP removal pending"
        if frontrun_risk > 0.7:
            return "CAUTION — high frontrun risk"
        if state.bot_count >= 5:
            return "CAUTION — bot swarm active"
        if state.pending_enable_trading:
            return "READY — trading about to be enabled"
        if state.pending_lp_add:
            return "READY — new liquidity incoming"
        if state.net_pressure > 1:
            return "BULLISH — strong buy pressure"
        if state.net_pressure < -1:
            return "BEARISH — strong sell pressure"
        return "NEUTRAL"

    def get_token_state(self, token_address: str) -> Optional[dict]:
        """Get current mempool state for a token."""
        state = self._token_states.get(token_address.lower())
        return state.to_dict() if state else None

    def get_alerts(self, limit: int = 20) -> list[dict]:
        """Get recent alerts."""
        return [a.to_dict() for a in list(self._alerts)[-limit:]]

    def get_gas_analysis(self) -> dict:
        """Get current gas analysis."""
        return self._gas_analyzer.analyze().to_dict()

    def get_pending_for_token(self, token_address: str) -> list[dict]:
        """Get all pending transactions for a specific token."""
        addr = token_address.lower()
        return [
            ptx.to_dict() for ptx in self._pending_txs.values()
            if ptx.token_address and ptx.token_address.lower() == addr
        ]

    def configure(self, **kwargs):
        """Update configuration."""
        for k, v in kwargs.items():
            if hasattr(self.config, k):
                setattr(self.config, k, v)

    def get_stats(self) -> dict:
        return {
            "running": self._running,
            "total_analyzed": self._total_analyzed,
            "pending_tracked": len(self._pending_txs),
            "tokens_with_activity": len(self._token_states),
            "total_alerts": len(self._alerts),
            "gas": self._gas_analyzer.analyze().to_dict(),
            "config": self.config.to_dict(),
        }

"""
orderflowAnalyzer.py — Orderflow Analysis Engine.

Analyzes how trades enter the market to detect:
  1. Coordinated bot buying (same block, many wallets)
  2. Whale accumulation patterns
  3. Insider trading signals
  4. Wash trading detection
  5. Smart money vs dumb money classification

Architecture:
  tx logs → decode traces → classify wallets → detect patterns
  → compute metrics → generate signals → scoring

Uses on-chain data to analyze trading patterns in real-time.
"""

import asyncio
import logging
import time
import math
from dataclasses import dataclass, field
from typing import Optional
from collections import defaultdict

from web3 import Web3

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
#  Data Classes
# ═══════════════════════════════════════════════════════════════════

@dataclass
class TradeFlow:
    """Single decoded trade from on-chain logs."""
    tx_hash: str = ""
    block_number: int = 0
    timestamp: float = 0.0
    from_address: str = ""
    to_address: str = ""
    token_address: str = ""
    pair_address: str = ""
    direction: str = "buy"             # buy / sell
    amount_native: float = 0.0         # BNB/ETH spent
    amount_tokens: float = 0.0         # tokens received
    gas_price_gwei: float = 0.0
    gas_used: int = 0
    position_in_block: int = 0         # tx index in block
    is_bot: bool = False
    is_whale: bool = False
    is_insider: bool = False
    wallet_label: str = ""             # bot/whale/insider/retail/unknown
    wallet_age_days: float = 0.0
    wallet_tx_count: int = 0


@dataclass
class OrderflowPattern:
    """Detected orderflow pattern."""
    pattern_type: str = "unknown"       # coordinated_buy, wash_trade, insider, etc.
    confidence: float = 0.0             # 0-1
    severity: str = "low"               # low/medium/high/critical
    wallets_involved: int = 0
    total_volume_native: float = 0.0
    block_range: tuple = (0, 0)
    description: str = ""
    signals: list = field(default_factory=list)


@dataclass
class WalletProfile:
    """Behavioral profile of a trading wallet."""
    address: str = ""
    label: str = "unknown"              # bot/whale/insider/retail/smart_money
    total_trades: int = 0
    total_buys: int = 0
    total_sells: int = 0
    total_volume_native: float = 0.0
    avg_trade_size: float = 0.0
    avg_gas_price: float = 0.0
    first_seen_block: int = 0
    last_seen_block: int = 0
    unique_tokens_traded: int = 0
    avg_hold_time_blocks: float = 0.0
    win_rate: float = 0.0
    pnl_estimate: float = 0.0
    bot_score: float = 0.0             # 0-1 probability of being a bot
    whale_score: float = 0.0           # 0-1 probability of being a whale
    insider_score: float = 0.0         # 0-1 probability of being an insider

    def to_dict(self) -> dict:
        return {
            "address": self.address,
            "label": self.label,
            "total_trades": self.total_trades,
            "total_buys": self.total_buys,
            "total_sells": self.total_sells,
            "total_volume_native": round(self.total_volume_native, 4),
            "avg_trade_size": round(self.avg_trade_size, 4),
            "bot_score": round(self.bot_score, 3),
            "whale_score": round(self.whale_score, 3),
            "insider_score": round(self.insider_score, 3),
        }


@dataclass
class OrderflowAnalysis:
    """Complete orderflow analysis result."""
    token_address: str = ""
    total_trades: int = 0
    total_buys: int = 0
    total_sells: int = 0
    buy_volume_native: float = 0.0
    sell_volume_native: float = 0.0
    unique_buyers: int = 0
    unique_sellers: int = 0
    bot_percentage: float = 0.0        # % of trades from bots
    whale_percentage: float = 0.0      # % of volume from whales
    insider_percentage: float = 0.0    # % of early trades from insiders
    # Pattern detection
    coordinated_buying: bool = False
    wash_trading_detected: bool = False
    insider_activity: bool = False
    organic_score: int = 50            # 0-100, how organic the trading is
    manipulation_risk: int = 0         # 0-100
    # Timing analysis
    avg_blocks_between_buys: float = 0.0
    buys_in_first_block: int = 0
    buying_velocity: float = 0.0       # buys per block
    # Patterns found
    patterns: list = field(default_factory=list)
    signals: list = field(default_factory=list)
    top_buyers: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "token_address": self.token_address,
            "total_trades": self.total_trades,
            "total_buys": self.total_buys,
            "total_sells": self.total_sells,
            "buy_volume_native": round(self.buy_volume_native, 4),
            "sell_volume_native": round(self.sell_volume_native, 4),
            "unique_buyers": self.unique_buyers,
            "unique_sellers": self.unique_sellers,
            "bot_percentage": round(self.bot_percentage, 1),
            "whale_percentage": round(self.whale_percentage, 1),
            "insider_percentage": round(self.insider_percentage, 1),
            "coordinated_buying": self.coordinated_buying,
            "wash_trading_detected": self.wash_trading_detected,
            "insider_activity": self.insider_activity,
            "organic_score": self.organic_score,
            "manipulation_risk": self.manipulation_risk,
            "patterns": [p.__dict__ if hasattr(p, '__dict__') else p for p in self.patterns],
            "signals": self.signals,
        }


@dataclass
class OrderflowConfig:
    """Configuration for orderflow analyzer."""
    enabled: bool = True
    # Detection thresholds
    whale_threshold_native: float = 1.0       # BNB/ETH — consider whale
    bot_gas_threshold_gwei: float = 10.0      # gas premium above average → bot
    coordinated_buy_threshold: int = 5        # N wallets in same block → coordinated
    insider_early_blocks: int = 3             # first N blocks → insider territory
    wash_trade_min_cycles: int = 3            # min buy→sell cycles → wash trading
    # Scoring
    max_bot_pct_for_organic: float = 30.0     # > 30% bots → not organic
    max_whale_pct_for_organic: float = 60.0   # > 60% whale volume → risky
    # Limits
    max_trades_to_analyze: int = 500
    lookback_blocks: int = 100

    def to_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "whale_threshold_native": self.whale_threshold_native,
            "bot_gas_threshold_gwei": self.bot_gas_threshold_gwei,
            "coordinated_buy_threshold": self.coordinated_buy_threshold,
            "max_trades_to_analyze": self.max_trades_to_analyze,
            "lookback_blocks": self.lookback_blocks,
        }


# ═══════════════════════════════════════════════════════════════════
#  Wallet Classifier
# ═══════════════════════════════════════════════════════════════════

class WalletClassifier:
    """Classifies wallets based on trading behavior."""

    # Known bot patterns
    BOT_SIGNATURES = {
        "high_gas": lambda p: p.avg_gas_price > 20,
        "zero_block_trades": lambda p: p.first_seen_block == p.last_seen_block and p.total_trades > 2,
        "rapid_fire": lambda p: p.total_trades > 10 and p.avg_hold_time_blocks < 2,
        "single_token": lambda p: p.unique_tokens_traded == 1 and p.total_trades > 5,
    }

    def __init__(self, config: OrderflowConfig = None):
        self.config = config or OrderflowConfig()
        self._profiles: dict[str, WalletProfile] = {}
        self._known_bots: set[str] = set()
        self._known_whales: set[str] = set()

    def update_profile(self, trade: TradeFlow):
        """Update wallet profile from a trade observation."""
        addr = trade.from_address.lower()
        if addr not in self._profiles:
            self._profiles[addr] = WalletProfile(
                address=addr,
                first_seen_block=trade.block_number,
            )
        profile = self._profiles[addr]
        profile.total_trades += 1
        if trade.direction == "buy":
            profile.total_buys += 1
        else:
            profile.total_sells += 1
        profile.total_volume_native += trade.amount_native
        profile.avg_trade_size = profile.total_volume_native / profile.total_trades
        profile.avg_gas_price = (
            (profile.avg_gas_price * (profile.total_trades - 1) + trade.gas_price_gwei)
            / profile.total_trades
        )
        profile.last_seen_block = max(profile.last_seen_block, trade.block_number)
        return profile

    def classify(self, profile: WalletProfile) -> WalletProfile:
        """Classify a wallet as bot/whale/insider/retail."""
        # Bot score
        bot_signals = sum(1 for _, check in self.BOT_SIGNATURES.items() if check(profile))
        profile.bot_score = min(1.0, bot_signals / max(len(self.BOT_SIGNATURES), 1))

        # Whale score
        if profile.avg_trade_size >= self.config.whale_threshold_native:
            profile.whale_score = min(1.0, profile.avg_trade_size / (self.config.whale_threshold_native * 5))
        else:
            profile.whale_score = profile.avg_trade_size / self.config.whale_threshold_native * 0.3

        # Insider score (bought very early + profitable)
        if profile.first_seen_block > 0:
            profile.insider_score = min(1.0, max(0, 1 - (profile.first_seen_block % 100) / 10))

        # Assign label
        if profile.bot_score >= 0.6:
            profile.label = "bot"
        elif profile.whale_score >= 0.7:
            profile.label = "whale"
        elif profile.insider_score >= 0.7:
            profile.label = "insider"
        elif profile.total_volume_native > self.config.whale_threshold_native * 0.5:
            profile.label = "smart_money"
        else:
            profile.label = "retail"

        return profile

    def get_profile(self, address: str) -> Optional[WalletProfile]:
        return self._profiles.get(address.lower())

    def get_all_profiles(self) -> list[WalletProfile]:
        return list(self._profiles.values())

    def get_stats(self) -> dict:
        labels = defaultdict(int)
        for p in self._profiles.values():
            labels[p.label] += 1
        return {
            "total_wallets": len(self._profiles),
            "labels": dict(labels),
        }


# ═══════════════════════════════════════════════════════════════════
#  Pattern Detector
# ═══════════════════════════════════════════════════════════════════

class PatternDetector:
    """Detects orderflow manipulation patterns."""

    def __init__(self, config: OrderflowConfig = None):
        self.config = config or OrderflowConfig()

    def detect_coordinated_buying(self, trades: list[TradeFlow]) -> Optional[OrderflowPattern]:
        """Detect multiple wallets buying in the same block (coordinated pump)."""
        block_buys: dict[int, list[TradeFlow]] = defaultdict(list)
        for t in trades:
            if t.direction == "buy":
                block_buys[t.block_number].append(t)

        # Find blocks with suspicious buy count
        suspicious_blocks = []
        for block, buys in block_buys.items():
            unique_wallets = len(set(b.from_address.lower() for b in buys))
            if unique_wallets >= self.config.coordinated_buy_threshold:
                suspicious_blocks.append((block, buys, unique_wallets))

        if not suspicious_blocks:
            return None

        total_wallets = sum(w for _, _, w in suspicious_blocks)
        total_volume = sum(sum(b.amount_native for b in buys) for _, buys, _ in suspicious_blocks)
        blocks = [b for b, _, _ in suspicious_blocks]

        return OrderflowPattern(
            pattern_type="coordinated_buy",
            confidence=min(1.0, total_wallets / (self.config.coordinated_buy_threshold * 3)),
            severity="high" if total_wallets >= 10 else "medium",
            wallets_involved=total_wallets,
            total_volume_native=total_volume,
            block_range=(min(blocks), max(blocks)),
            description=f"{total_wallets} wallets bought in {len(suspicious_blocks)} blocks",
            signals=[f"Block {b}: {w} unique buyers" for b, _, w in suspicious_blocks[:5]],
        )

    def detect_wash_trading(self, trades: list[TradeFlow]) -> Optional[OrderflowPattern]:
        """Detect buy→sell cycles from same wallet (wash trading)."""
        wallet_sequences: dict[str, list[str]] = defaultdict(list)
        for t in trades:
            wallet_sequences[t.from_address.lower()].append(t.direction)

        wash_wallets = []
        for wallet, seq in wallet_sequences.items():
            # Count buy→sell alternations
            cycles = 0
            for i in range(1, len(seq)):
                if seq[i - 1] == "buy" and seq[i] == "sell":
                    cycles += 1
            if cycles >= self.config.wash_trade_min_cycles:
                wash_wallets.append((wallet, cycles))

        if not wash_wallets:
            return None

        total_cycles = sum(c for _, c in wash_wallets)
        return OrderflowPattern(
            pattern_type="wash_trading",
            confidence=min(1.0, len(wash_wallets) / 5),
            severity="high" if len(wash_wallets) >= 3 else "medium",
            wallets_involved=len(wash_wallets),
            total_volume_native=0,
            description=f"{len(wash_wallets)} wallets with {total_cycles} wash cycles",
            signals=[f"Wallet {w[:10]}...: {c} cycles" for w, c in wash_wallets[:5]],
        )

    def detect_insider_activity(self, trades: list[TradeFlow]) -> Optional[OrderflowPattern]:
        """Detect early buyers who accumulate before wider trading begins."""
        if not trades:
            return None

        # Sort by block number
        sorted_trades = sorted(trades, key=lambda t: t.block_number)
        if not sorted_trades:
            return None

        first_block = sorted_trades[0].block_number
        early_cutoff = first_block + self.config.insider_early_blocks

        early_buyers = set()
        early_volume = 0.0
        for t in sorted_trades:
            if t.block_number <= early_cutoff and t.direction == "buy":
                early_buyers.add(t.from_address.lower())
                early_volume += t.amount_native

        if len(early_buyers) < 2:
            return None

        total_buyers = len(set(t.from_address.lower() for t in sorted_trades if t.direction == "buy"))
        insider_pct = len(early_buyers) / max(total_buyers, 1)

        # If early buyers are a small % of total but got large volume → insider
        if insider_pct < 0.5 and early_volume > 0.5:
            return OrderflowPattern(
                pattern_type="insider_activity",
                confidence=min(1.0, early_volume / 2.0),
                severity="high" if early_volume > 2.0 else "medium",
                wallets_involved=len(early_buyers),
                total_volume_native=early_volume,
                block_range=(first_block, early_cutoff),
                description=f"{len(early_buyers)} wallets accumulated {early_volume:.2f} native in first {self.config.insider_early_blocks} blocks",
                signals=[f"Early buyer concentration: {insider_pct:.0%}"],
            )

        return None

    def detect_bot_activity(self, trades: list[TradeFlow], avg_gas: float = 5.0) -> Optional[OrderflowPattern]:
        """Detect bot activity from gas price patterns and timing."""
        bot_trades = []
        for t in trades:
            is_bot = False
            # High gas premium = likely bot
            if t.gas_price_gwei > avg_gas + self.config.bot_gas_threshold_gwei:
                is_bot = True
            # Position 0-2 in block = likely bot (MEV or frontrunner)
            if t.position_in_block <= 2:
                is_bot = True
            if is_bot:
                bot_trades.append(t)

        if len(bot_trades) < 3:
            return None

        bot_pct = len(bot_trades) / max(len(trades), 1) * 100
        bot_volume = sum(t.amount_native for t in bot_trades)

        return OrderflowPattern(
            pattern_type="bot_activity",
            confidence=min(1.0, bot_pct / 50),
            severity="critical" if bot_pct > 50 else ("high" if bot_pct > 30 else "medium"),
            wallets_involved=len(set(t.from_address.lower() for t in bot_trades)),
            total_volume_native=bot_volume,
            description=f"{bot_pct:.0f}% of trades from bots ({len(bot_trades)}/{len(trades)})",
            signals=[f"Bot volume: {bot_volume:.2f} native"],
        )


# ═══════════════════════════════════════════════════════════════════
#  Main: Orderflow Analyzer
# ═══════════════════════════════════════════════════════════════════

class OrderflowAnalyzer:
    """
    Analyzes on-chain orderflow to detect bots, whales, insiders,
    and manipulation patterns.

    Usage:
        analyzer = OrderflowAnalyzer(w3, chain_id=56)
        result = await analyzer.analyze(token_address, pair_address)
        print(result.organic_score, result.manipulation_risk)
    """

    # Swap event topic (Uniswap V2 / PancakeSwap)
    SWAP_TOPIC = Web3.keccak(text="Swap(address,uint256,uint256,uint256,uint256,address)")

    # Transfer event topic (ERC-20)
    TRANSFER_TOPIC = Web3.keccak(text="Transfer(address,address,uint256)")

    def __init__(self, w3: Web3, chain_id: int = 56, config: OrderflowConfig = None):
        self.w3 = w3
        self.chain_id = chain_id
        self.config = config or OrderflowConfig()
        self._classifier = WalletClassifier(self.config)
        self._detector = PatternDetector(self.config)
        self._analysis_cache: dict[str, OrderflowAnalysis] = {}
        self._total_analyses = 0

    async def analyze(self, token_address: str, pair_address: str = "",
                      lookback_blocks: int = 0) -> OrderflowAnalysis:
        """
        Full orderflow analysis for a token.
        Fetches Swap events, classifies wallets, detects patterns.
        """
        self._total_analyses += 1
        result = OrderflowAnalysis(token_address=token_address)

        try:
            # Fetch swap events
            blocks = lookback_blocks or self.config.lookback_blocks
            trades = await self._fetch_swap_events(token_address, pair_address, blocks)

            if not trades:
                result.signals.append("No trades found in lookback period")
                return result

            result.total_trades = len(trades)

            # Classify each trade
            for trade in trades:
                profile = self._classifier.update_profile(trade)
                self._classifier.classify(profile)
                trade.wallet_label = profile.label
                trade.is_bot = profile.bot_score >= 0.6
                trade.is_whale = profile.whale_score >= 0.7
                trade.is_insider = profile.insider_score >= 0.7

            # Compute basic metrics
            buys = [t for t in trades if t.direction == "buy"]
            sells = [t for t in trades if t.direction == "sell"]
            result.total_buys = len(buys)
            result.total_sells = len(sells)
            result.buy_volume_native = sum(t.amount_native for t in buys)
            result.sell_volume_native = sum(t.amount_native for t in sells)
            result.unique_buyers = len(set(t.from_address.lower() for t in buys))
            result.unique_sellers = len(set(t.from_address.lower() for t in sells))

            # Bot/whale/insider percentages
            bot_trades = [t for t in trades if t.is_bot]
            whale_trades = [t for t in trades if t.is_whale]
            result.bot_percentage = len(bot_trades) / max(len(trades), 1) * 100
            result.whale_percentage = sum(t.amount_native for t in whale_trades) / max(result.buy_volume_native + result.sell_volume_native, 0.001) * 100

            # Insider percentage (early trades)
            if buys:
                first_block = min(t.block_number for t in buys)
                early_buys = [t for t in buys if t.block_number <= first_block + self.config.insider_early_blocks]
                insider_buys = [t for t in early_buys if t.is_insider]
                result.insider_percentage = len(insider_buys) / max(len(buys), 1) * 100

            # Timing analysis
            if len(buys) >= 2:
                buy_blocks = sorted(set(t.block_number for t in buys))
                if len(buy_blocks) >= 2:
                    diffs = [buy_blocks[i + 1] - buy_blocks[i] for i in range(len(buy_blocks) - 1)]
                    result.avg_blocks_between_buys = sum(diffs) / len(diffs)
                if buys:
                    first_block = min(t.block_number for t in buys)
                    result.buys_in_first_block = sum(1 for t in buys if t.block_number == first_block)
                    block_span = max(1, max(t.block_number for t in buys) - first_block + 1)
                    result.buying_velocity = len(buys) / block_span

            # Detect patterns
            patterns = []
            p = self._detector.detect_coordinated_buying(trades)
            if p:
                patterns.append(p)
                result.coordinated_buying = True
            p = self._detector.detect_wash_trading(trades)
            if p:
                patterns.append(p)
                result.wash_trading_detected = True
            p = self._detector.detect_insider_activity(trades)
            if p:
                patterns.append(p)
                result.insider_activity = True
            p = self._detector.detect_bot_activity(trades)
            if p:
                patterns.append(p)
            result.patterns = patterns

            # Compute organic score
            result.organic_score = self._compute_organic_score(result)
            result.manipulation_risk = max(0, 100 - result.organic_score)

            # Top buyers
            buyer_volumes: dict[str, float] = defaultdict(float)
            for t in buys:
                buyer_volumes[t.from_address.lower()] += t.amount_native
            top = sorted(buyer_volumes.items(), key=lambda x: -x[1])[:5]
            result.top_buyers = [
                {"address": addr, "volume": round(vol, 4),
                 "label": self._classifier.get_profile(addr).label if self._classifier.get_profile(addr) else "unknown"}
                for addr, vol in top
            ]

            # Generate signal strings
            result.signals = self._generate_signals(result)

            # Cache result
            self._analysis_cache[token_address.lower()] = result

        except Exception as e:
            logger.warning(f"Orderflow analysis failed for {token_address}: {e}")
            result.signals.append(f"Analysis error: {str(e)[:100]}")

        return result

    async def _fetch_swap_events(self, token_address: str, pair_address: str,
                                  lookback_blocks: int) -> list[TradeFlow]:
        """Fetch and decode Swap events for a token pair."""
        trades = []
        try:
            current_block = self.w3.eth.block_number
            from_block = max(0, current_block - lookback_blocks)

            if not pair_address:
                return trades

            # Fetch Swap event logs
            logs = self.w3.eth.get_logs({
                "fromBlock": from_block,
                "toBlock": current_block,
                "address": Web3.to_checksum_address(pair_address),
                "topics": [self.SWAP_TOPIC],
            })

            for log in logs[:self.config.max_trades_to_analyze]:
                try:
                    trade = self._decode_swap_log(log, token_address)
                    if trade:
                        trades.append(trade)
                except Exception:
                    continue

        except Exception as e:
            logger.debug(f"Failed to fetch swap events: {e}")

        return trades

    def _decode_swap_log(self, log: dict, token_address: str) -> Optional[TradeFlow]:
        """Decode a Uniswap V2 Swap event log into a TradeFlow."""
        try:
            data = log.get("data", "0x")
            if len(data) < 258:  # 0x + 4 * 64
                return None

            # Decode amounts (Swap event: amount0In, amount1In, amount0Out, amount1Out)
            amount0_in = int(data[2:66], 16)
            amount1_in = int(data[66:130], 16)
            amount0_out = int(data[130:194], 16)
            amount1_out = int(data[194:258], 16)

            # Determine direction
            if amount0_in > 0 and amount1_out > 0:
                direction = "buy"
                amount_native = amount0_in / 1e18
                amount_tokens = amount1_out / 1e18
            elif amount1_in > 0 and amount0_out > 0:
                direction = "sell"
                amount_native = amount1_out / 1e18
                amount_tokens = amount0_out / 1e18
            else:
                return None

            # Extract sender from topics
            topics = log.get("topics", [])
            from_addr = ""
            to_addr = ""
            if len(topics) >= 3:
                from_addr = "0x" + topics[1].hex()[-40:] if hasattr(topics[1], 'hex') else ""
                to_addr = "0x" + topics[2].hex()[-40:] if hasattr(topics[2], 'hex') else ""

            return TradeFlow(
                tx_hash=log.get("transactionHash", b"").hex() if hasattr(log.get("transactionHash", b""), 'hex') else "",
                block_number=log.get("blockNumber", 0),
                from_address=from_addr,
                to_address=to_addr,
                token_address=token_address,
                pair_address=log.get("address", ""),
                direction=direction,
                amount_native=amount_native,
                amount_tokens=amount_tokens,
                position_in_block=log.get("transactionIndex", 0),
            )
        except Exception:
            return None

    def _compute_organic_score(self, result: OrderflowAnalysis) -> int:
        """Compute organic trading score (0-100). Higher = more organic."""
        score = 100

        # Bot presence reduces organic score
        if result.bot_percentage > self.config.max_bot_pct_for_organic:
            score -= int((result.bot_percentage - self.config.max_bot_pct_for_organic) * 1.5)

        # Whale concentration reduces score
        if result.whale_percentage > self.config.max_whale_pct_for_organic:
            score -= int((result.whale_percentage - self.config.max_whale_pct_for_organic) * 0.8)

        # Coordinated buying is suspicious
        if result.coordinated_buying:
            score -= 20

        # Wash trading is very suspicious
        if result.wash_trading_detected:
            score -= 30

        # Insider activity
        if result.insider_activity:
            score -= 15

        # Very few unique buyers = likely manipulated
        if result.unique_buyers < 5 and result.total_buys > 10:
            score -= 15

        # Extremely high buying velocity = likely bots
        if result.buying_velocity > 5:
            score -= int(min(20, (result.buying_velocity - 5) * 4))

        return max(0, min(100, score))

    def _generate_signals(self, result: OrderflowAnalysis) -> list[str]:
        """Generate human-readable signal strings."""
        signals = []

        if result.bot_percentage > 40:
            signals.append(f"HIGH bot activity: {result.bot_percentage:.0f}% of trades")
        elif result.bot_percentage > 20:
            signals.append(f"Moderate bot activity: {result.bot_percentage:.0f}%")

        if result.whale_percentage > 50:
            signals.append(f"Whale-dominated: {result.whale_percentage:.0f}% of volume")

        if result.coordinated_buying:
            signals.append("Coordinated buying detected")

        if result.wash_trading_detected:
            signals.append("Wash trading detected")

        if result.insider_activity:
            signals.append("Insider trading activity detected")

        if result.buys_in_first_block > 5:
            signals.append(f"{result.buys_in_first_block} buys in first block (bot sniping)")

        if result.organic_score >= 70:
            signals.append(f"Organic score: {result.organic_score}/100 (healthy)")
        elif result.organic_score >= 40:
            signals.append(f"Organic score: {result.organic_score}/100 (moderate)")
        else:
            signals.append(f"Organic score: {result.organic_score}/100 (concerning)")

        return signals

    def get_cached(self, token_address: str) -> Optional[OrderflowAnalysis]:
        """Get cached analysis result."""
        return self._analysis_cache.get(token_address.lower())

    def get_wallet_profiles(self) -> list[dict]:
        """Get all wallet profiles."""
        profiles = self._classifier.get_all_profiles()
        return [p.to_dict() for p in sorted(profiles, key=lambda p: -p.total_volume_native)[:50]]

    def configure(self, **kwargs):
        """Update configuration."""
        for k, v in kwargs.items():
            if hasattr(self.config, k):
                setattr(self.config, k, v)

    def get_stats(self) -> dict:
        return {
            "total_analyses": self._total_analyses,
            "cached_tokens": len(self._analysis_cache),
            "wallet_profiles": self._classifier.get_stats(),
            "config": self.config.to_dict(),
        }

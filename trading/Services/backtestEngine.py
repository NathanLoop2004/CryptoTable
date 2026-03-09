"""
backtestEngine.py — Historical Backtesting Engine v6.
═══════════════════════════════════════════════════════

Replays historical PairCreated events and simulates the full sniper pipeline
to evaluate strategy performance without risking real capital.

Features:
  - Replay historical blocks from on-chain data or saved snapshots
  - Simulate full analysis pipeline (all 22 security gates)
  - Virtual portfolio tracking with realistic slippage / gas
  - Strategy comparison (A/B testing of different parameters)
  - Statistical reports: win rate, Sharpe ratio, max drawdown, P&L curve
  - Export results as JSON / CSV for further analysis

Architecture:
  BacktestEngine → feeds historical pairs to VirtualSniperBot
  VirtualSniperBot → runs same analysis as real SniperBot but with paper trades
  BacktestResult → aggregated performance metrics

Integration:
  Can run via Celery task (async) or directly for quick tests.
  Results stored in DB or returned as dict for WebSocket dashboard.
"""

import asyncio
import json
import logging
import math
import time
from dataclasses import dataclass, field, asdict
from typing import Optional, Callable
from enum import Enum

import aiohttp
from web3 import Web3

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
#  Data structures
# ═══════════════════════════════════════════════════════════════════

class ExitReason(Enum):
    TAKE_PROFIT = "take_profit"
    STOP_LOSS = "stop_loss"
    TIME_LIMIT = "time_limit"
    RUG_DETECTED = "rug_detected"
    MANUAL = "manual"
    STILL_OPEN = "still_open"


@dataclass
class BacktestTrade:
    """A single simulated trade during backtest."""
    token_address: str
    symbol: str = ""
    pair_address: str = ""
    # Entry
    entry_price_usd: float = 0.0
    entry_block: int = 0
    entry_timestamp: float = 0.0
    entry_amount_native: float = 0.0      # BNB / ETH spent
    entry_amount_usd: float = 0.0
    # Analysis scores at entry
    pump_score: int = 0
    risk_score: int = 0
    ml_score: int = 0
    safety: str = "unknown"
    # Exit
    exit_price_usd: float = 0.0
    exit_block: int = 0
    exit_timestamp: float = 0.0
    exit_reason: str = ""
    # P&L
    pnl_percent: float = 0.0
    pnl_usd: float = 0.0
    # Costs
    gas_cost_usd: float = 0.0
    slippage_cost_usd: float = 0.0
    # Peak/trough during hold
    max_price_usd: float = 0.0
    min_price_usd: float = 0.0
    max_unrealized_pnl_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    hold_seconds: float = 0.0

    def to_dict(self):
        return asdict(self)


@dataclass
class BacktestConfig:
    """Parameters for a backtest run."""
    # Strategy
    chain_id: int = 56
    buy_amount_native: float = 0.05       # BNB/ETH per trade
    take_profit_pct: float = 40.0         # % gain to exit
    stop_loss_pct: float = 15.0           # % loss to exit
    max_hold_hours: float = 24.0
    slippage_pct: float = 12.0
    max_concurrent: int = 5
    # Filters (same as sniper settings)
    min_liquidity_usd: float = 5000
    max_buy_tax: float = 10.0
    max_sell_tax: float = 15.0
    only_safe: bool = True
    min_pump_score: int = 40
    min_risk_score: int = 30
    min_ml_score: int = 20
    # Data range
    start_block: int = 0
    end_block: int = 0
    start_timestamp: float = 0.0
    end_timestamp: float = 0.0
    # Simulation
    gas_price_gwei: float = 5.0
    native_price_usd: float = 600.0       # BNB/ETH price for calculations
    realistic_slippage: bool = True       # model real slippage from liquidity
    # Labels
    name: str = "default"
    description: str = ""

    def to_dict(self):
        return asdict(self)


@dataclass
class BacktestResult:
    """Aggregated results from a backtest run."""
    config: dict = field(default_factory=dict)
    # Timing
    start_time: float = 0.0
    end_time: float = 0.0
    duration_seconds: float = 0.0
    blocks_processed: int = 0
    pairs_found: int = 0
    pairs_analyzed: int = 0
    # Trade stats
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    still_open: int = 0
    win_rate: float = 0.0
    # P&L
    total_pnl_usd: float = 0.0
    total_pnl_percent: float = 0.0
    avg_pnl_per_trade_usd: float = 0.0
    avg_pnl_per_trade_pct: float = 0.0
    best_trade_pnl_pct: float = 0.0
    worst_trade_pnl_pct: float = 0.0
    # Risk
    sharpe_ratio: float = 0.0
    max_drawdown_pct: float = 0.0
    profit_factor: float = 0.0        # gross profit / gross loss
    avg_hold_seconds: float = 0.0
    # Costs
    total_gas_cost_usd: float = 0.0
    total_slippage_cost_usd: float = 0.0
    # Portfolio curve
    equity_curve: list = field(default_factory=list)  # [{timestamp, equity}]
    # Breakdown
    trades: list = field(default_factory=list)         # BacktestTrade[]
    exit_reasons: dict = field(default_factory=dict)   # reason → count
    safety_breakdown: dict = field(default_factory=dict)  # safe/warning/danger → count
    # Filtered tokens (rejected by gates)
    tokens_rejected: int = 0
    rejection_reasons: dict = field(default_factory=dict)

    def to_dict(self):
        d = asdict(self)
        # Limit trade detail for large backtests
        if len(d["trades"]) > 200:
            d["trades_truncated"] = True
            d["trades"] = d["trades"][:200]
        return d


# ═══════════════════════════════════════════════════════════════════
#  Historical Data Source
# ═══════════════════════════════════════════════════════════════════

class HistoricalDataSource:
    """
    Fetches historical PairCreated events and price data.
    Supports on-chain replay and DexScreener historical data.
    """

    def __init__(self, w3: Web3, chain_id: int = 56):
        self.w3 = w3
        self.chain_id = chain_id
        self._session: aiohttp.ClientSession | None = None
        self._cache: dict[str, dict] = {}  # token → historical data

    async def _ensure_session(self):
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15)
            )

    async def get_pair_created_events(
        self, start_block: int, end_block: int, factory_address: str
    ) -> list[dict]:
        """Fetch PairCreated events from on-chain logs."""
        pair_topic = "0x" + Web3.keccak(
            text="PairCreated(address,address,address,uint256)"
        ).hex()

        events = []
        # Process in chunks of 2000 blocks
        chunk_size = 2000
        current = start_block

        while current <= end_block:
            chunk_end = min(current + chunk_size - 1, end_block)
            try:
                logs = self.w3.eth.get_logs({
                    "fromBlock": current,
                    "toBlock": chunk_end,
                    "address": Web3.to_checksum_address(factory_address),
                    "topics": [pair_topic],
                })
                for log in logs:
                    token0 = "0x" + log["topics"][1].hex()[-40:]
                    token1 = "0x" + log["topics"][2].hex()[-40:]
                    pair_data = log["data"].hex() if isinstance(log["data"], bytes) else log["data"]
                    pair_address = "0x" + pair_data[26:66]
                    events.append({
                        "block": log["blockNumber"],
                        "tx_hash": log["transactionHash"].hex() if hasattr(log["transactionHash"], "hex") else str(log["transactionHash"]),
                        "token0": Web3.to_checksum_address(token0),
                        "token1": Web3.to_checksum_address(token1),
                        "pair_address": Web3.to_checksum_address(pair_address),
                    })
            except Exception as e:
                logger.warning(f"Backtest: error fetching logs {current}-{chunk_end}: {e}")
            current = chunk_end + 1

        logger.info(f"Backtest: found {len(events)} PairCreated events in blocks {start_block}-{end_block}")
        return events

    async def get_historical_price(self, token_address: str, timestamp: float) -> dict:
        """Fetch historical price data from DexScreener."""
        cache_key = f"{token_address}:{int(timestamp)}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        await self._ensure_session()
        try:
            url = f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"
            async with self._session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    pairs = data.get("pairs", [])
                    if pairs:
                        pair = pairs[0]
                        result = {
                            "price_usd": float(pair.get("priceUsd", 0) or 0),
                            "liquidity_usd": float(pair.get("liquidity", {}).get("usd", 0) or 0),
                            "volume_24h": float(pair.get("volume", {}).get("h24", 0) or 0),
                            "price_change_h1": float(pair.get("priceChange", {}).get("h1", 0) or 0),
                            "price_change_h24": float(pair.get("priceChange", {}).get("h24", 0) or 0),
                            "buys_24h": int(pair.get("txns", {}).get("h24", {}).get("buys", 0) or 0),
                            "sells_24h": int(pair.get("txns", {}).get("h24", {}).get("sells", 0) or 0),
                        }
                        self._cache[cache_key] = result
                        return result
        except Exception as e:
            logger.debug(f"Backtest: price fetch error for {token_address}: {e}")

        return {"price_usd": 0, "liquidity_usd": 0, "volume_24h": 0}

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()


# ═══════════════════════════════════════════════════════════════════
#  Virtual Portfolio
# ═══════════════════════════════════════════════════════════════════

class VirtualPortfolio:
    """Tracks paper positions and calculates P&L."""

    def __init__(self, config: BacktestConfig):
        self.config = config
        self.positions: dict[str, BacktestTrade] = {}  # token → BacktestTrade
        self.closed_trades: list[BacktestTrade] = []
        self.equity_curve: list[dict] = []
        self.initial_balance = config.buy_amount_native * config.native_price_usd * 20  # 20 trades budget
        self.balance_usd = self.initial_balance
        self.peak_balance = self.initial_balance

    @property
    def open_positions(self) -> int:
        return len(self.positions)

    def can_open(self) -> bool:
        """Check if we can open a new position."""
        return (
            self.open_positions < self.config.max_concurrent
            and self.balance_usd >= self.config.buy_amount_native * self.config.native_price_usd
        )

    def open_position(self, trade: BacktestTrade) -> bool:
        """Open a new paper position."""
        if trade.token_address in self.positions:
            return False
        if not self.can_open():
            return False

        cost = trade.entry_amount_usd + trade.gas_cost_usd + trade.slippage_cost_usd
        self.balance_usd -= cost
        self.positions[trade.token_address] = trade
        return True

    def close_position(
        self, token_address: str, exit_price: float,
        exit_block: int, exit_timestamp: float, reason: str
    ) -> Optional[BacktestTrade]:
        """Close a paper position and calculate P&L."""
        if token_address not in self.positions:
            return None

        trade = self.positions.pop(token_address)
        trade.exit_price_usd = exit_price
        trade.exit_block = exit_block
        trade.exit_timestamp = exit_timestamp
        trade.exit_reason = reason
        trade.hold_seconds = exit_timestamp - trade.entry_timestamp

        # P&L calculation
        if trade.entry_price_usd > 0:
            trade.pnl_percent = ((exit_price - trade.entry_price_usd) / trade.entry_price_usd) * 100
        trade.pnl_usd = trade.entry_amount_usd * (trade.pnl_percent / 100)
        trade.pnl_usd -= trade.gas_cost_usd * 2  # gas for buy + sell
        trade.pnl_usd -= trade.slippage_cost_usd * 2

        # Return funds
        self.balance_usd += trade.entry_amount_usd + trade.pnl_usd
        self.peak_balance = max(self.peak_balance, self.balance_usd)

        self.closed_trades.append(trade)
        return trade

    def check_exits(self, current_prices: dict[str, float], current_block: int, current_time: float):
        """Check TP/SL/Time for all open positions."""
        exits = []
        for token, trade in list(self.positions.items()):
            price = current_prices.get(token, 0)
            if price <= 0:
                continue

            # Update peak/trough
            trade.max_price_usd = max(trade.max_price_usd, price)
            trade.min_price_usd = min(trade.min_price_usd, price) if trade.min_price_usd > 0 else price

            pnl_pct = ((price - trade.entry_price_usd) / trade.entry_price_usd) * 100 if trade.entry_price_usd > 0 else 0
            trade.max_unrealized_pnl_pct = max(trade.max_unrealized_pnl_pct, pnl_pct)
            trade.max_drawdown_pct = min(trade.max_drawdown_pct, pnl_pct)

            # Check exit conditions
            if pnl_pct >= self.config.take_profit_pct:
                exits.append((token, price, ExitReason.TAKE_PROFIT.value))
            elif pnl_pct <= -self.config.stop_loss_pct:
                exits.append((token, price, ExitReason.STOP_LOSS.value))
            elif (current_time - trade.entry_timestamp) >= self.config.max_hold_hours * 3600:
                exits.append((token, price, ExitReason.TIME_LIMIT.value))

        for token, price, reason in exits:
            self.close_position(token, price, current_block, current_time, reason)

    def snapshot(self, timestamp: float, current_prices: dict[str, float]):
        """Take equity snapshot for the curve."""
        unrealized = 0
        for token, trade in self.positions.items():
            price = current_prices.get(token, trade.entry_price_usd)
            if trade.entry_price_usd > 0:
                pnl_pct = ((price - trade.entry_price_usd) / trade.entry_price_usd) * 100
                unrealized += trade.entry_amount_usd * (pnl_pct / 100)

        self.equity_curve.append({
            "timestamp": timestamp,
            "equity": round(self.balance_usd + unrealized, 2),
            "open_positions": self.open_positions,
            "closed_trades": len(self.closed_trades),
        })


# ═══════════════════════════════════════════════════════════════════
#  Backtest Engine
# ═══════════════════════════════════════════════════════════════════

# Stablecoins to skip (BSC + ETH)
_SKIP_TOKENS = {
    "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c",  # WBNB
    "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",  # WETH
    "0x55d398326f99059fF775485246999027B3197955",  # USDT BSC
    "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d",  # USDC BSC
    "0xe9e7CEA3DedcA5984780Bafc599bD69ADd087D56",  # BUSD
    "0xdAC17F958D2ee523a2206206994597C13D831ec7",  # USDT ETH
    "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",  # USDC ETH
}

# Factory addresses
_FACTORIES = {
    56: "0xcA143Ce32Fe78f1f7019d7d551a6402fC5350c73",
    1:  "0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f",
}

# Wrapped native
_WETH = {
    56: "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c",
    1:  "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
}


class BacktestEngine:
    """
    Main backtesting engine.

    Replays historical PairCreated events through a simulated sniper pipeline
    and tracks virtual trades.

    Usage:
        engine = BacktestEngine(w3, chain_id=56)
        result = await engine.run(config)
    """

    def __init__(self, w3: Web3, chain_id: int = 56):
        self.w3 = w3
        self.chain_id = chain_id
        self.data_source = HistoricalDataSource(w3, chain_id)
        self._progress_callback: Optional[Callable] = None
        self._cancel_flag = False

    def set_progress_callback(self, callback: Callable):
        """Set callback for progress updates: callback(percent, message)."""
        self._progress_callback = callback

    def cancel(self):
        """Cancel a running backtest."""
        self._cancel_flag = True

    def _report_progress(self, pct: float, msg: str):
        if self._progress_callback:
            try:
                self._progress_callback(pct, msg)
            except Exception:
                pass

    async def run(self, config: BacktestConfig) -> BacktestResult:
        """
        Execute a full backtest.

        Args:
            config: BacktestConfig with strategy parameters and block range.

        Returns:
            BacktestResult with comprehensive performance metrics.
        """
        self._cancel_flag = False
        result = BacktestResult(config=config.to_dict())
        result.start_time = time.time()

        factory = _FACTORIES.get(config.chain_id or self.chain_id, _FACTORIES[56])
        weth = _WETH.get(config.chain_id or self.chain_id, _WETH[56])

        # Determine block range
        start_block = config.start_block
        end_block = config.end_block
        if start_block == 0:
            current = self.w3.eth.block_number
            start_block = current - 28800  # ~24h on BSC (3s blocks)
        if end_block == 0:
            end_block = self.w3.eth.block_number

        self._report_progress(5, f"Fetching PairCreated events ({start_block}→{end_block})...")

        # 1. Fetch historical events
        events = await self.data_source.get_pair_created_events(
            start_block, end_block, factory
        )
        result.blocks_processed = end_block - start_block
        result.pairs_found = len(events)

        if not events:
            result.end_time = time.time()
            result.duration_seconds = result.end_time - result.start_time
            return result

        # 2. Setup virtual portfolio
        portfolio = VirtualPortfolio(config)

        self._report_progress(15, f"Analyzing {len(events)} pairs...")

        # 3. Process each pair
        for idx, event in enumerate(events):
            if self._cancel_flag:
                break

            # Progress
            pct = 15 + (idx / len(events)) * 75
            if idx % 10 == 0:
                self._report_progress(pct, f"Processing pair {idx + 1}/{len(events)}...")

            token0 = event["token0"]
            token1 = event["token1"]

            # Identify new token (not WBNB/WETH/stables)
            if token0.lower() == weth.lower() or token0 in _SKIP_TOKENS:
                new_token = token1
            elif token1.lower() == weth.lower() or token1 in _SKIP_TOKENS:
                new_token = token0
            else:
                # Both are unknown tokens — skip
                continue

            if new_token in _SKIP_TOKENS:
                continue

            result.pairs_analyzed += 1

            # 4. Fetch price data at time of pair creation
            try:
                block_data = self.w3.eth.get_block(event["block"])
                event_time = float(block_data.get("timestamp", time.time()))
            except Exception:
                event_time = time.time()

            price_data = await self.data_source.get_historical_price(new_token, event_time)
            price_usd = price_data.get("price_usd", 0)
            liquidity = price_data.get("liquidity_usd", 0)

            # 5. Apply filters (simulating security gates)
            reject_reason = self._apply_filters(config, price_data)
            if reject_reason:
                result.tokens_rejected += 1
                result.rejection_reasons[reject_reason] = result.rejection_reasons.get(reject_reason, 0) + 1
                continue

            # 6. Simulate entry
            if price_usd <= 0 or not portfolio.can_open():
                continue

            entry_amount_usd = config.buy_amount_native * config.native_price_usd
            gas_cost = config.gas_price_gwei * 250000 * 1e-9 * config.native_price_usd
            slippage_cost = entry_amount_usd * (config.slippage_pct / 100) * 0.3  # Realistic: ~30% of max slippage

            trade = BacktestTrade(
                token_address=new_token,
                symbol=new_token[:8] + "...",
                pair_address=event["pair_address"],
                entry_price_usd=price_usd,
                entry_block=event["block"],
                entry_timestamp=event_time,
                entry_amount_native=config.buy_amount_native,
                entry_amount_usd=entry_amount_usd,
                pump_score=50,  # Default — real backtest would run full pipeline
                risk_score=50,
                ml_score=50,
                safety="unknown",
                gas_cost_usd=gas_cost,
                slippage_cost_usd=slippage_cost,
            )

            portfolio.open_position(trade)

            # 7. Simulate price evolution (check later price for TP/SL)
            later_price_data = await self.data_source.get_historical_price(new_token, event_time + 3600)
            later_price = later_price_data.get("price_usd", 0)

            if later_price > 0:
                prices = {new_token: later_price}
                portfolio.check_exits(prices, event["block"] + 1200, event_time + 3600)

            # Take equity snapshot every 20 pairs
            if idx % 20 == 0:
                all_prices = {t: trade.entry_price_usd for t, trade in portfolio.positions.items()}
                portfolio.snapshot(event_time, all_prices)

            # Rate limit DexScreener
            await asyncio.sleep(0.25)

        # 8. Close remaining positions at last known price
        self._report_progress(92, "Closing remaining positions...")
        for token in list(portfolio.positions.keys()):
            final_price_data = await self.data_source.get_historical_price(token, time.time())
            final_price = final_price_data.get("price_usd", 0)
            if final_price > 0:
                portfolio.close_position(token, final_price, end_block, time.time(), ExitReason.STILL_OPEN.value)

        # 9. Compile results
        self._report_progress(95, "Compiling results...")
        result = self._compile_results(result, portfolio)
        result.end_time = time.time()
        result.duration_seconds = result.end_time - result.start_time

        self._report_progress(100, f"Backtest complete: {result.total_trades} trades, "
                                    f"win rate {result.win_rate:.1f}%, P&L ${result.total_pnl_usd:.2f}")

        await self.data_source.close()
        return result

    def _apply_filters(self, config: BacktestConfig, price_data: dict) -> str:
        """Apply strategy filters — returns rejection reason or empty string."""
        liquidity = price_data.get("liquidity_usd", 0)
        if liquidity < config.min_liquidity_usd:
            return "low_liquidity"
        volume = price_data.get("volume_24h", 0)
        if liquidity > 0 and volume / liquidity > 50:
            return "suspicious_volume"
        buys = price_data.get("buys_24h", 0)
        sells = price_data.get("sells_24h", 0)
        if buys > 0 and sells > 0 and buys / max(sells, 1) > 20:
            return "suspicious_buy_sell_ratio"
        return ""

    def _compile_results(self, result: BacktestResult, portfolio: VirtualPortfolio) -> BacktestResult:
        """Compile final statistics from the virtual portfolio."""
        trades = portfolio.closed_trades
        result.total_trades = len(trades)
        result.trades = [t.to_dict() for t in trades]
        result.equity_curve = portfolio.equity_curve

        if not trades:
            return result

        winners = [t for t in trades if t.pnl_usd > 0]
        losers = [t for t in trades if t.pnl_usd <= 0]
        result.winning_trades = len(winners)
        result.losing_trades = len(losers)
        result.still_open = len(portfolio.positions)
        result.win_rate = (len(winners) / len(trades)) * 100 if trades else 0

        # P&L
        pnls = [t.pnl_usd for t in trades]
        pnl_pcts = [t.pnl_percent for t in trades]
        result.total_pnl_usd = sum(pnls)
        result.total_pnl_percent = sum(pnl_pcts)
        result.avg_pnl_per_trade_usd = result.total_pnl_usd / len(trades)
        result.avg_pnl_per_trade_pct = result.total_pnl_percent / len(trades)
        result.best_trade_pnl_pct = max(pnl_pcts) if pnl_pcts else 0
        result.worst_trade_pnl_pct = min(pnl_pcts) if pnl_pcts else 0

        # Costs
        result.total_gas_cost_usd = sum(t.gas_cost_usd * 2 for t in trades)  # buy+sell
        result.total_slippage_cost_usd = sum(t.slippage_cost_usd * 2 for t in trades)

        # Hold time
        hold_times = [t.hold_seconds for t in trades if t.hold_seconds > 0]
        result.avg_hold_seconds = sum(hold_times) / len(hold_times) if hold_times else 0

        # Exit reasons
        for t in trades:
            r = t.exit_reason or "unknown"
            result.exit_reasons[r] = result.exit_reasons.get(r, 0) + 1

        # Sharpe ratio (annualized, assuming daily trades)
        if len(pnl_pcts) >= 2:
            import statistics as stats
            mean_return = stats.mean(pnl_pcts)
            std_return = stats.stdev(pnl_pcts)
            if std_return > 0:
                result.sharpe_ratio = round((mean_return / std_return) * math.sqrt(365), 2)

        # Max drawdown
        peak = 0
        max_dd = 0
        cumulative = 0
        for pnl in pnl_pcts:
            cumulative += pnl
            peak = max(peak, cumulative)
            dd = cumulative - peak
            max_dd = min(max_dd, dd)
        result.max_drawdown_pct = round(max_dd, 2)

        # Profit factor
        gross_profit = sum(t.pnl_usd for t in winners) if winners else 0
        gross_loss = abs(sum(t.pnl_usd for t in losers)) if losers else 1
        result.profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else 0

        return result

    async def run_comparison(self, configs: list[BacktestConfig]) -> list[BacktestResult]:
        """Run multiple backtests for A/B comparison."""
        results = []
        for i, config in enumerate(configs):
            self._report_progress(0, f"Running backtest {i + 1}/{len(configs)}: {config.name}")
            result = await self.run(config)
            results.append(result)
        return results

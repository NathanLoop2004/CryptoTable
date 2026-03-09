"""
marketSimulator.py — Full Market Simulation Engine.

Advanced simulation beyond basic swap simulation:
  1. Slippage modeling (price impact curves)
  2. MEV attack simulation (frontrun, sandwich, backrun)
  3. Gas war simulation (priority fee auction)
  4. Liquidity depth profiling
  5. Multi-scenario Monte Carlo simulation

Architecture:
  pool state snapshot → simulate trade → apply MEV model
  → compute slippage → gas war resolution → final outcome

Prevents losses by testing strategies before live execution.
"""

import asyncio
import logging
import time
import math
import random
from dataclasses import dataclass, field
from typing import Optional
from enum import Enum

from web3 import Web3

logger = logging.getLogger(__name__)

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    np = None
    HAS_NUMPY = False


# ═══════════════════════════════════════════════════════════════════
#  Data Classes
# ═══════════════════════════════════════════════════════════════════

class SimScenario(Enum):
    """Simulation scenarios."""
    NORMAL = "normal"
    HIGH_VOLATILITY = "high_volatility"
    MEV_ATTACK = "mev_attack"
    GAS_WAR = "gas_war"
    LIQUIDITY_DRAIN = "liquidity_drain"
    WHALE_DUMP = "whale_dump"
    RUG_PULL = "rug_pull"


@dataclass
class PoolState:
    """Snapshot of a liquidity pool."""
    pair_address: str = ""
    token_address: str = ""
    reserve_native: float = 0.0         # BNB/ETH reserve
    reserve_token: float = 0.0          # token reserve
    fee_bps: int = 25                   # 0.25% for Uniswap V2
    price_native_per_token: float = 0.0
    price_token_per_native: float = 0.0
    k: float = 0.0                      # constant product (x * y = k)
    block_number: int = 0

    def __post_init__(self):
        if self.reserve_native > 0 and self.reserve_token > 0:
            self.price_native_per_token = self.reserve_native / self.reserve_token
            self.price_token_per_native = self.reserve_token / self.reserve_native
            self.k = self.reserve_native * self.reserve_token


@dataclass
class SlippageResult:
    """Result of slippage simulation."""
    amount_in: float = 0.0
    amount_out_ideal: float = 0.0       # without slippage
    amount_out_actual: float = 0.0      # with slippage
    price_impact_pct: float = 0.0       # price movement caused
    slippage_pct: float = 0.0           # actual slippage
    effective_price: float = 0.0
    pool_share_pct: float = 0.0         # % of pool used by trade

    def to_dict(self) -> dict:
        return {
            "amount_in": round(self.amount_in, 6),
            "amount_out_ideal": round(self.amount_out_ideal, 6),
            "amount_out_actual": round(self.amount_out_actual, 6),
            "price_impact_pct": round(self.price_impact_pct, 3),
            "slippage_pct": round(self.slippage_pct, 3),
            "effective_price": round(self.effective_price, 8),
            "pool_share_pct": round(self.pool_share_pct, 2),
        }


@dataclass
class MEVSimResult:
    """Result of MEV attack simulation."""
    attack_type: str = "none"           # frontrun/sandwich/backrun/none
    attacker_profit: float = 0.0        # attacker's profit in native
    victim_extra_slippage: float = 0.0  # additional slippage for victim
    victim_loss_native: float = 0.0     # victim's loss in native
    frontrun_gas_cost: float = 0.0
    sandwich_gas_cost: float = 0.0
    is_profitable_attack: bool = False
    protection_strategy: str = ""       # recommended protection

    def to_dict(self) -> dict:
        return {
            "attack_type": self.attack_type,
            "attacker_profit": round(self.attacker_profit, 6),
            "victim_extra_slippage": round(self.victim_extra_slippage, 3),
            "victim_loss_native": round(self.victim_loss_native, 6),
            "is_profitable_attack": self.is_profitable_attack,
            "protection_strategy": self.protection_strategy,
        }


@dataclass
class GasWarResult:
    """Result of gas war simulation."""
    optimal_gas_gwei: float = 0.0
    min_competitive_gas: float = 0.0
    probability_of_inclusion: float = 0.0
    estimated_position: int = 0
    gas_cost_native: float = 0.0
    competitors_estimated: int = 0

    def to_dict(self) -> dict:
        return {
            "optimal_gas_gwei": round(self.optimal_gas_gwei, 1),
            "min_competitive_gas": round(self.min_competitive_gas, 1),
            "probability_of_inclusion": round(self.probability_of_inclusion, 3),
            "estimated_position": self.estimated_position,
            "gas_cost_native": round(self.gas_cost_native, 6),
            "competitors_estimated": self.competitors_estimated,
        }


@dataclass
class LiquidityProfile:
    """Liquidity depth profile at various trade sizes."""
    trade_sizes: list = field(default_factory=list)       # [0.01, 0.05, 0.1, 0.5, 1, 5]
    slippage_at_size: list = field(default_factory=list)  # corresponding slippage %
    max_safe_trade: float = 0.0        # max trade with < 5% slippage
    depth_score: int = 50              # 0-100
    is_thin: bool = False

    def to_dict(self) -> dict:
        return {
            "trade_sizes": self.trade_sizes,
            "slippage_at_size": [round(s, 2) for s in self.slippage_at_size],
            "max_safe_trade": round(self.max_safe_trade, 4),
            "depth_score": self.depth_score,
            "is_thin": self.is_thin,
        }


@dataclass
class MonteCarloResult:
    """Result of Monte Carlo simulation."""
    scenarios_run: int = 0
    avg_pnl_pct: float = 0.0
    median_pnl_pct: float = 0.0
    worst_case_pnl: float = 0.0
    best_case_pnl: float = 0.0
    win_probability: float = 0.0
    expected_value: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown_pct: float = 0.0
    var_95: float = 0.0                # 95% Value at Risk
    scenario_breakdown: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "scenarios_run": self.scenarios_run,
            "avg_pnl_pct": round(self.avg_pnl_pct, 2),
            "median_pnl_pct": round(self.median_pnl_pct, 2),
            "worst_case_pnl": round(self.worst_case_pnl, 2),
            "best_case_pnl": round(self.best_case_pnl, 2),
            "win_probability": round(self.win_probability, 3),
            "expected_value": round(self.expected_value, 4),
            "sharpe_ratio": round(self.sharpe_ratio, 3),
            "max_drawdown_pct": round(self.max_drawdown_pct, 2),
            "var_95": round(self.var_95, 2),
            "scenario_breakdown": self.scenario_breakdown,
        }


@dataclass
class FullSimulationResult:
    """Complete simulation encompassing all sub-simulations."""
    token_address: str = ""
    timestamp: float = 0.0
    pool_state: Optional[PoolState] = None
    slippage: Optional[SlippageResult] = None
    mev_sim: Optional[MEVSimResult] = None
    gas_war: Optional[GasWarResult] = None
    liquidity_profile: Optional[LiquidityProfile] = None
    monte_carlo: Optional[MonteCarloResult] = None
    # Overall assessment
    risk_score: int = 50               # 0-100
    recommendation: str = "neutral"    # buy / cautious_buy / neutral / avoid
    signals: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "token_address": self.token_address,
            "risk_score": self.risk_score,
            "recommendation": self.recommendation,
            "slippage": self.slippage.to_dict() if self.slippage else {},
            "mev_sim": self.mev_sim.to_dict() if self.mev_sim else {},
            "gas_war": self.gas_war.to_dict() if self.gas_war else {},
            "liquidity": self.liquidity_profile.to_dict() if self.liquidity_profile else {},
            "monte_carlo": self.monte_carlo.to_dict() if self.monte_carlo else {},
            "signals": self.signals,
        }


@dataclass
class SimulationConfig:
    """Configuration for the market simulator."""
    enabled: bool = True
    # Slippage
    default_fee_bps: int = 25           # 0.25% for V2
    max_acceptable_slippage: float = 15.0
    # MEV
    avg_frontrun_gas_cost: float = 0.005  # BNB
    avg_sandwich_gas_cost: float = 0.01
    mev_profit_threshold: float = 0.002   # min attacker profit for attack
    # Gas war
    base_gas_gwei: float = 3.0
    gas_war_multiplier: float = 2.5
    # Monte Carlo
    monte_carlo_scenarios: int = 1000
    price_volatility_daily: float = 0.15  # 15% daily volatility
    # Liquidity
    liquidity_test_sizes: list = field(default_factory=lambda: [0.01, 0.05, 0.1, 0.5, 1.0, 5.0])
    max_safe_slippage: float = 5.0

    def to_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "default_fee_bps": self.default_fee_bps,
            "max_acceptable_slippage": self.max_acceptable_slippage,
            "monte_carlo_scenarios": self.monte_carlo_scenarios,
            "price_volatility_daily": self.price_volatility_daily,
        }


# ═══════════════════════════════════════════════════════════════════
#  AMM Math Engine
# ═══════════════════════════════════════════════════════════════════

class AMMEngine:
    """Uniswap V2 constant-product AMM math."""

    @staticmethod
    def get_amount_out(amount_in: float, reserve_in: float, reserve_out: float,
                       fee_bps: int = 25) -> float:
        """Calculate output amount using x*y=k with fee."""
        if amount_in <= 0 or reserve_in <= 0 or reserve_out <= 0:
            return 0.0
        fee_factor = (10000 - fee_bps) / 10000
        amount_in_with_fee = amount_in * fee_factor
        numerator = amount_in_with_fee * reserve_out
        denominator = reserve_in + amount_in_with_fee
        return numerator / denominator

    @staticmethod
    def get_price_impact(amount_in: float, reserve_in: float, reserve_out: float,
                         fee_bps: int = 25) -> float:
        """Calculate price impact percentage."""
        if reserve_in <= 0 or reserve_out <= 0:
            return 100.0
        spot_price = reserve_out / reserve_in
        amount_out = AMMEngine.get_amount_out(amount_in, reserve_in, reserve_out, fee_bps)
        if amount_in <= 0:
            return 0.0
        effective_price = amount_out / amount_in
        if spot_price <= 0:
            return 100.0
        return max(0, (1 - effective_price / spot_price) * 100)

    @staticmethod
    def simulate_swap(pool: PoolState, amount_in_native: float,
                      direction: str = "buy") -> SlippageResult:
        """Simulate a swap and compute slippage."""
        result = SlippageResult(amount_in=amount_in_native)

        if direction == "buy":
            # Native → Token
            reserve_in = pool.reserve_native
            reserve_out = pool.reserve_token
        else:
            # Token → Native
            reserve_in = pool.reserve_token
            reserve_out = pool.reserve_native

        if reserve_in <= 0 or reserve_out <= 0:
            return result

        # Ideal output (no slippage)
        spot_price = reserve_out / reserve_in
        result.amount_out_ideal = amount_in_native * spot_price

        # Actual output (with AMM slippage)
        result.amount_out_actual = AMMEngine.get_amount_out(
            amount_in_native, reserve_in, reserve_out, pool.fee_bps,
        )

        # Slippage
        if result.amount_out_ideal > 0:
            result.slippage_pct = (1 - result.amount_out_actual / result.amount_out_ideal) * 100

        # Price impact
        result.price_impact_pct = AMMEngine.get_price_impact(
            amount_in_native, reserve_in, reserve_out, pool.fee_bps,
        )

        # Effective price
        if result.amount_out_actual > 0:
            result.effective_price = amount_in_native / result.amount_out_actual

        # Pool share
        result.pool_share_pct = (amount_in_native / reserve_in) * 100 if reserve_in > 0 else 0

        return result


# ═══════════════════════════════════════════════════════════════════
#  MEV Simulator
# ═══════════════════════════════════════════════════════════════════

class MEVSimulator:
    """Simulates MEV attacks to estimate risk."""

    def __init__(self, config: SimulationConfig = None):
        self.config = config or SimulationConfig()

    def simulate_frontrun(self, pool: PoolState, victim_amount: float) -> MEVSimResult:
        """Simulate a frontrun attack."""
        result = MEVSimResult(attack_type="frontrun")

        # Attacker buys before victim
        attacker_amount = victim_amount * 0.5  # typical frontrun size
        attacker_out = AMMEngine.get_amount_out(
            attacker_amount, pool.reserve_native, pool.reserve_token, pool.fee_bps,
        )

        # Pool state after attacker buy
        new_reserve_native = pool.reserve_native + attacker_amount
        new_reserve_token = pool.reserve_token - attacker_out

        # Victim buys at worse price
        victim_out = AMMEngine.get_amount_out(
            victim_amount, new_reserve_native, new_reserve_token, pool.fee_bps,
        )

        # Pool state after victim buy
        new_reserve_native2 = new_reserve_native + victim_amount
        new_reserve_token2 = new_reserve_token - victim_out

        # Attacker sells back
        attacker_sell = AMMEngine.get_amount_out(
            attacker_out, new_reserve_token2, new_reserve_native2, pool.fee_bps,
        )

        # Calculate profit
        result.attacker_profit = attacker_sell - attacker_amount - self.config.avg_frontrun_gas_cost
        result.is_profitable_attack = result.attacker_profit > self.config.mev_profit_threshold

        # Victim extra slippage
        normal_out = AMMEngine.get_amount_out(
            victim_amount, pool.reserve_native, pool.reserve_token, pool.fee_bps,
        )
        if normal_out > 0:
            result.victim_extra_slippage = (1 - victim_out / normal_out) * 100
        result.victim_loss_native = (normal_out - victim_out) * pool.price_native_per_token if pool.price_native_per_token > 0 else 0
        result.frontrun_gas_cost = self.config.avg_frontrun_gas_cost
        result.protection_strategy = "flashbots" if result.is_profitable_attack else "none"

        return result

    def simulate_sandwich(self, pool: PoolState, victim_amount: float) -> MEVSimResult:
        """Simulate a sandwich attack (frontrun + backrun)."""
        result = MEVSimResult(attack_type="sandwich")

        # Front-run: attacker buys
        front_amount = victim_amount * 0.8
        front_out = AMMEngine.get_amount_out(
            front_amount, pool.reserve_native, pool.reserve_token, pool.fee_bps,
        )

        # Pool state after frontrun
        r_native_1 = pool.reserve_native + front_amount
        r_token_1 = pool.reserve_token - front_out

        # Victim trade
        victim_out = AMMEngine.get_amount_out(
            victim_amount, r_native_1, r_token_1, pool.fee_bps,
        )

        # Pool state after victim
        r_native_2 = r_native_1 + victim_amount
        r_token_2 = r_token_1 - victim_out

        # Back-run: attacker sells
        back_out = AMMEngine.get_amount_out(
            front_out, r_token_2, r_native_2, pool.fee_bps,
        )

        # Profit
        total_gas = self.config.avg_sandwich_gas_cost
        result.attacker_profit = back_out - front_amount - total_gas
        result.is_profitable_attack = result.attacker_profit > self.config.mev_profit_threshold

        # Victim impact
        normal_out = AMMEngine.get_amount_out(
            victim_amount, pool.reserve_native, pool.reserve_token, pool.fee_bps,
        )
        if normal_out > 0:
            result.victim_extra_slippage = (1 - victim_out / normal_out) * 100
        result.victim_loss_native = max(0, (normal_out - victim_out) * pool.price_native_per_token) if pool.price_native_per_token > 0 else 0
        result.sandwich_gas_cost = total_gas
        result.protection_strategy = "private_mempool" if result.is_profitable_attack else "none"

        return result


# ═══════════════════════════════════════════════════════════════════
#  Gas War Simulator
# ═══════════════════════════════════════════════════════════════════

class GasWarSimulator:
    """Simulates gas competition for block inclusion."""

    def __init__(self, config: SimulationConfig = None):
        self.config = config or SimulationConfig()

    def simulate(self, base_gas_gwei: float = 0, competitors: int = 5,
                 urgency: float = 0.8) -> GasWarResult:
        """Simulate gas war given expected competitors."""
        base = base_gas_gwei or self.config.base_gas_gwei
        result = GasWarResult()

        # Estimate competitor gas bids (log-normal distribution)
        competitor_bids = []
        for _ in range(max(competitors, 1)):
            bid = base * (1 + random.expovariate(1.0) * 0.5)
            competitor_bids.append(bid)
        competitor_bids.sort()

        result.competitors_estimated = len(competitor_bids)

        # Target position based on urgency
        target_position = max(1, int((1 - urgency) * len(competitor_bids)))

        if competitor_bids:
            result.min_competitive_gas = competitor_bids[0]
            result.optimal_gas_gwei = competitor_bids[-1] * 1.1 if urgency > 0.9 else \
                competitor_bids[min(target_position, len(competitor_bids) - 1)] * 1.05
        else:
            result.optimal_gas_gwei = base * 1.2
            result.min_competitive_gas = base

        result.estimated_position = target_position
        result.gas_cost_native = result.optimal_gas_gwei * 21000 / 1e9  # basic tx gas
        result.probability_of_inclusion = min(1.0, urgency * 0.95 + 0.05)

        return result


# ═══════════════════════════════════════════════════════════════════
#  Liquidity Profiler
# ═══════════════════════════════════════════════════════════════════

class LiquidityProfiler:
    """Profiles liquidity depth at various trade sizes."""

    def __init__(self, config: SimulationConfig = None):
        self.config = config or SimulationConfig()

    def profile(self, pool: PoolState) -> LiquidityProfile:
        """Build liquidity depth profile."""
        result = LiquidityProfile()
        sizes = list(self.config.liquidity_test_sizes)
        result.trade_sizes = sizes
        slippages = []

        max_safe = 0.0
        for size in sizes:
            slippage = AMMEngine.get_price_impact(
                size, pool.reserve_native, pool.reserve_token, pool.fee_bps,
            )
            slippages.append(slippage)
            if slippage < self.config.max_safe_slippage:
                max_safe = size

        result.slippage_at_size = slippages
        result.max_safe_trade = max_safe

        # Depth score
        if pool.reserve_native <= 0.1:
            result.depth_score = 5
            result.is_thin = True
        elif pool.reserve_native <= 1.0:
            result.depth_score = 20
            result.is_thin = True
        elif pool.reserve_native <= 10:
            result.depth_score = 50
        elif pool.reserve_native <= 50:
            result.depth_score = 75
        else:
            result.depth_score = 95

        # Adjust based on slippage at standard size (0.1 native)
        standard_slip = slippages[2] if len(slippages) > 2 else 50
        if standard_slip > 10:
            result.depth_score = max(5, result.depth_score - 30)
            result.is_thin = True
        elif standard_slip > 5:
            result.depth_score = max(10, result.depth_score - 15)

        return result


# ═══════════════════════════════════════════════════════════════════
#  Monte Carlo Simulator
# ═══════════════════════════════════════════════════════════════════

class MonteCarloSimulator:
    """Runs Monte Carlo simulations for expected outcomes."""

    def __init__(self, config: SimulationConfig = None):
        self.config = config or SimulationConfig()

    def simulate(self, entry_price: float, take_profit_pct: float = 50.0,
                 stop_loss_pct: float = 15.0, hold_hours: float = 24,
                 volatility: float = 0, mev_risk: float = 0.1,
                 scenarios: int = 0) -> MonteCarloResult:
        """
        Run Monte Carlo simulations of a trade.
        Simulates price paths with random walk + drift.
        """
        n_scenarios = scenarios or self.config.monte_carlo_scenarios
        vol = volatility or self.config.price_volatility_daily
        result = MonteCarloResult(scenarios_run=n_scenarios)

        pnls = []
        scenario_counts = {s.value: 0 for s in SimScenario}

        for _ in range(n_scenarios):
            # Random scenario weights
            scenario = self._pick_scenario(mev_risk)
            scenario_counts[scenario.value] = scenario_counts.get(scenario.value, 0) + 1

            pnl = self._simulate_one(
                entry_price, take_profit_pct, stop_loss_pct,
                hold_hours, vol, scenario,
            )
            pnls.append(pnl)

        if not pnls:
            return result

        pnls.sort()
        result.avg_pnl_pct = sum(pnls) / len(pnls)
        result.median_pnl_pct = pnls[len(pnls) // 2]
        result.worst_case_pnl = pnls[0]
        result.best_case_pnl = pnls[-1]
        result.win_probability = sum(1 for p in pnls if p > 0) / len(pnls)
        result.expected_value = result.avg_pnl_pct / 100
        result.var_95 = pnls[int(0.05 * len(pnls))] if len(pnls) > 20 else result.worst_case_pnl

        # Sharpe ratio
        if len(pnls) > 1:
            mean_r = sum(pnls) / len(pnls)
            std_r = math.sqrt(sum((p - mean_r) ** 2 for p in pnls) / (len(pnls) - 1))
            result.sharpe_ratio = mean_r / std_r if std_r > 0 else 0

        # Max drawdown
        peak = 100
        max_dd = 0
        for pnl in pnls:
            equity = 100 + pnl
            peak = max(peak, equity)
            dd = (peak - equity) / peak * 100
            max_dd = max(max_dd, dd)
        result.max_drawdown_pct = max_dd

        result.scenario_breakdown = scenario_counts

        return result

    def _pick_scenario(self, mev_risk: float) -> SimScenario:
        """Randomly pick a scenario weighted by risk."""
        r = random.random()
        if r < mev_risk * 0.3:
            return SimScenario.MEV_ATTACK
        elif r < mev_risk * 0.3 + 0.05:
            return SimScenario.RUG_PULL
        elif r < 0.15:
            return SimScenario.HIGH_VOLATILITY
        elif r < 0.20:
            return SimScenario.GAS_WAR
        elif r < 0.25:
            return SimScenario.WHALE_DUMP
        elif r < 0.30:
            return SimScenario.LIQUIDITY_DRAIN
        else:
            return SimScenario.NORMAL

    def _simulate_one(self, entry_price: float, tp_pct: float, sl_pct: float,
                      hold_hours: float, volatility: float,
                      scenario: SimScenario) -> float:
        """Simulate one trade path and return PnL %."""
        hourly_vol = volatility / math.sqrt(24)
        price = entry_price
        steps = max(1, int(hold_hours))

        # Scenario adjustments
        drift = 0.0
        vol_mult = 1.0
        if scenario == SimScenario.HIGH_VOLATILITY:
            vol_mult = 2.5
        elif scenario == SimScenario.MEV_ATTACK:
            drift = -0.02  # negative drift from MEV loss
        elif scenario == SimScenario.RUG_PULL:
            # Instant rug at random point
            rug_step = random.randint(0, steps)
            for i in range(steps):
                if i == rug_step:
                    return -95.0  # near total loss
                change = random.gauss(0, hourly_vol * vol_mult)
                price *= (1 + change)
            return -95.0
        elif scenario == SimScenario.WHALE_DUMP:
            drift = -0.04
        elif scenario == SimScenario.LIQUIDITY_DRAIN:
            drift = -0.03
            vol_mult = 1.5
        elif scenario == SimScenario.GAS_WAR:
            drift = -0.01  # gas cost eats into profit

        for _ in range(steps):
            change = random.gauss(drift, hourly_vol * vol_mult)
            price *= (1 + change)
            pnl = (price - entry_price) / entry_price * 100

            # Check TP/SL
            if pnl >= tp_pct:
                return tp_pct
            if pnl <= -sl_pct:
                return -sl_pct

        # End of hold period
        return (price - entry_price) / entry_price * 100


# ═══════════════════════════════════════════════════════════════════
#  Main: Market Simulator
# ═══════════════════════════════════════════════════════════════════

class MarketSimulator:
    """
    Full market simulation engine.
    Combines slippage, MEV, gas war, liquidity, and Monte Carlo simulations.

    Usage:
        simulator = MarketSimulator(w3, chain_id=56)
        result = await simulator.simulate(token_address, pair_address, amount_native=0.1)
        print(result.risk_score, result.recommendation)
    """

    def __init__(self, w3: Web3 = None, chain_id: int = 56, config: SimulationConfig = None):
        self.w3 = w3
        self.chain_id = chain_id
        self.config = config or SimulationConfig()
        self._amm = AMMEngine()
        self._mev_sim = MEVSimulator(self.config)
        self._gas_sim = GasWarSimulator(self.config)
        self._liq_profiler = LiquidityProfiler(self.config)
        self._mc_sim = MonteCarloSimulator(self.config)
        self._total_simulations = 0
        self._simulation_cache: dict[str, FullSimulationResult] = {}

    async def simulate(self, token_address: str, pair_address: str = "",
                       amount_native: float = 0.1,
                       take_profit_pct: float = 50.0,
                       stop_loss_pct: float = 15.0,
                       hold_hours: float = 24,
                       mev_risk: float = 0.1) -> FullSimulationResult:
        """
        Run full market simulation for a token.
        Returns comprehensive risk assessment.
        """
        self._total_simulations += 1
        result = FullSimulationResult(
            token_address=token_address,
            timestamp=time.time(),
        )

        try:
            # Get pool state
            pool = await self._get_pool_state(token_address, pair_address)
            result.pool_state = pool

            if pool.reserve_native <= 0:
                result.signals.append("Cannot simulate: no pool reserves")
                result.risk_score = 90
                result.recommendation = "avoid"
                return result

            # 1. Slippage simulation
            result.slippage = self._amm.simulate_swap(pool, amount_native)

            # 2. MEV simulation
            frontrun = self._mev_sim.simulate_frontrun(pool, amount_native)
            sandwich = self._mev_sim.simulate_sandwich(pool, amount_native)
            # Pick worst case
            if sandwich.attacker_profit > frontrun.attacker_profit:
                result.mev_sim = sandwich
            else:
                result.mev_sim = frontrun

            # 3. Gas war simulation
            result.gas_war = self._gas_sim.simulate(
                base_gas_gwei=self.config.base_gas_gwei,
                competitors=5,
                urgency=0.7,
            )

            # 4. Liquidity profile
            result.liquidity_profile = self._liq_profiler.profile(pool)

            # 5. Monte Carlo
            entry_price = pool.price_native_per_token if pool.price_native_per_token > 0 else 1.0
            result.monte_carlo = self._mc_sim.simulate(
                entry_price=entry_price,
                take_profit_pct=take_profit_pct,
                stop_loss_pct=stop_loss_pct,
                hold_hours=hold_hours,
                mev_risk=mev_risk,
            )

            # Compute overall risk score & recommendation
            result.risk_score, result.recommendation = self._assess(result)
            result.signals = self._generate_signals(result)

            # Cache
            self._simulation_cache[token_address.lower()] = result

        except Exception as e:
            logger.warning(f"Market simulation failed for {token_address}: {e}")
            result.signals.append(f"Simulation error: {str(e)[:100]}")
            result.risk_score = 80
            result.recommendation = "avoid"

        return result

    async def _get_pool_state(self, token_address: str, pair_address: str) -> PoolState:
        """Fetch current pool reserves."""
        pool = PoolState(pair_address=pair_address, token_address=token_address)

        if not self.w3 or not pair_address:
            return pool

        try:
            PAIR_ABI = [{"constant": True, "inputs": [], "name": "getReserves",
                         "outputs": [{"name": "_reserve0", "type": "uint112"},
                                     {"name": "_reserve1", "type": "uint112"},
                                     {"name": "_blockTimestampLast", "type": "uint32"}],
                         "type": "function"},
                        {"constant": True, "inputs": [], "name": "token0",
                         "outputs": [{"name": "", "type": "address"}],
                         "type": "function"}]

            pair = self.w3.eth.contract(
                address=Web3.to_checksum_address(pair_address), abi=PAIR_ABI,
            )
            reserves = pair.functions.getReserves().call()
            token0 = pair.functions.token0().call()

            if token0.lower() == token_address.lower():
                pool.reserve_token = reserves[0] / 1e18
                pool.reserve_native = reserves[1] / 1e18
            else:
                pool.reserve_native = reserves[0] / 1e18
                pool.reserve_token = reserves[1] / 1e18

            pool.block_number = self.w3.eth.block_number
            pool.__post_init__()  # Recalculate derived values

        except Exception as e:
            logger.debug(f"Failed to get pool state: {e}")

        return pool

    def _assess(self, result: FullSimulationResult) -> tuple[int, str]:
        """Compute overall risk score and recommendation."""
        risk = 30  # baseline

        # Slippage risk
        if result.slippage and result.slippage.slippage_pct > 10:
            risk += 20
        elif result.slippage and result.slippage.slippage_pct > 5:
            risk += 10

        # MEV risk
        if result.mev_sim and result.mev_sim.is_profitable_attack:
            risk += 15
            if result.mev_sim.victim_extra_slippage > 5:
                risk += 10

        # Liquidity risk
        if result.liquidity_profile and result.liquidity_profile.is_thin:
            risk += 15

        # Monte Carlo risk
        if result.monte_carlo:
            if result.monte_carlo.win_probability < 0.3:
                risk += 20
            elif result.monte_carlo.win_probability < 0.5:
                risk += 10
            if result.monte_carlo.max_drawdown_pct > 80:
                risk += 10

        risk = max(0, min(100, risk))

        if risk <= 30:
            rec = "buy"
        elif risk <= 50:
            rec = "cautious_buy"
        elif risk <= 70:
            rec = "neutral"
        else:
            rec = "avoid"

        return risk, rec

    def _generate_signals(self, result: FullSimulationResult) -> list[str]:
        """Generate human-readable simulation signals."""
        signals = []

        if result.slippage:
            s = result.slippage
            if s.slippage_pct > 10:
                signals.append(f"HIGH slippage: {s.slippage_pct:.1f}% (impact: {s.price_impact_pct:.1f}%)")
            elif s.slippage_pct > 5:
                signals.append(f"Moderate slippage: {s.slippage_pct:.1f}%")

        if result.mev_sim:
            m = result.mev_sim
            if m.is_profitable_attack:
                signals.append(f"MEV vulnerable: {m.attack_type} profit={m.attacker_profit:.4f} native")

        if result.liquidity_profile:
            lp = result.liquidity_profile
            signals.append(f"Liquidity depth: {lp.depth_score}/100 (max safe: {lp.max_safe_trade:.2f} native)")

        if result.monte_carlo:
            mc = result.monte_carlo
            signals.append(f"Monte Carlo: win={mc.win_probability:.0%} avg={mc.avg_pnl_pct:+.1f}% VaR95={mc.var_95:+.1f}%")

        if result.gas_war:
            gw = result.gas_war
            signals.append(f"Gas: optimal={gw.optimal_gas_gwei:.1f} gwei")

        return signals

    def get_cached(self, token_address: str) -> Optional[FullSimulationResult]:
        """Get cached simulation result."""
        return self._simulation_cache.get(token_address.lower())

    def configure(self, **kwargs):
        """Update configuration."""
        for k, v in kwargs.items():
            if hasattr(self.config, k):
                setattr(self.config, k, v)

    def get_stats(self) -> dict:
        return {
            "total_simulations": self._total_simulations,
            "cached": len(self._simulation_cache),
            "config": self.config.to_dict(),
        }

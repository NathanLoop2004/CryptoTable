"""
autoStrategyGenerator.py — Automatic Strategy Generation Engine.

Quantitative trading approach:
  1. Define strategy building blocks (indicators, conditions, actions)
  2. Combinatorially generate candidate strategies
  3. Backtest each candidate
  4. Rank by risk-adjusted performance
  5. Select best for live deployment

Architecture:
  indicator library → strategy combinator → backtest runner
  → scoring engine → tournament selection → deployment

Similar to genetic programming applied to trading strategies.
"""

import asyncio
import logging
import time
import random
import math
from dataclasses import dataclass, field
from typing import Optional, Callable
from enum import Enum

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

class IndicatorType(Enum):
    """Available indicator types."""
    PUMP_SCORE = "pump_score"
    SOCIAL_SENTIMENT = "social_sentiment"
    ML_PREDICTION = "ml_prediction"
    WHALE_ACTIVITY = "whale_activity"
    LIQUIDITY_DEPTH = "liquidity_depth"
    VOLATILITY = "volatility"
    DEV_REPUTATION = "dev_reputation"
    MEV_RISK = "mev_risk"
    ORDERFLOW_ORGANIC = "orderflow_organic"
    PRICE_MOMENTUM = "price_momentum"
    HOLDER_GROWTH = "holder_growth"
    LP_LOCK_QUALITY = "lp_lock_quality"


class ConditionOp(Enum):
    """Comparison operators for strategy conditions."""
    GREATER_THAN = ">"
    LESS_THAN = "<"
    EQUAL = "=="
    GTE = ">="
    LTE = "<="
    BETWEEN = "between"


@dataclass
class Indicator:
    """A single indicator with its extraction logic."""
    name: str = ""
    indicator_type: IndicatorType = IndicatorType.PUMP_SCORE
    field_name: str = ""               # TokenInfo field to read
    weight: float = 1.0
    normalize_min: float = 0.0
    normalize_max: float = 100.0

    def extract(self, token_info) -> float:
        """Extract indicator value from token info."""
        val = getattr(token_info, self.field_name, 0)
        if isinstance(val, bool):
            return 100.0 if val else 0.0
        try:
            return float(val)
        except (TypeError, ValueError):
            return 0.0

    def normalize(self, value: float) -> float:
        """Normalize to 0-1 range."""
        if self.normalize_max == self.normalize_min:
            return 0.5
        return max(0, min(1, (value - self.normalize_min) / (self.normalize_max - self.normalize_min)))


@dataclass
class Condition:
    """A single condition in a strategy rule."""
    indicator_name: str = ""
    operator: str = ">="                # ConditionOp value
    threshold: float = 0.0
    threshold_high: float = 0.0        # for BETWEEN operator
    weight: float = 1.0

    def evaluate(self, value: float) -> bool:
        """Evaluate condition against a value."""
        if self.operator == ">=":
            return value >= self.threshold
        elif self.operator == ">":
            return value > self.threshold
        elif self.operator == "<=":
            return value <= self.threshold
        elif self.operator == "<":
            return value < self.threshold
        elif self.operator == "==":
            return abs(value - self.threshold) < 0.01
        elif self.operator == "between":
            return self.threshold <= value <= self.threshold_high
        return False

    def to_dict(self) -> dict:
        return {
            "indicator": self.indicator_name,
            "operator": self.operator,
            "threshold": self.threshold,
            "threshold_high": self.threshold_high,
            "weight": self.weight,
        }


@dataclass
class StrategyRule:
    """A set of conditions that form a trading rule."""
    name: str = ""
    conditions: list = field(default_factory=list)  # list[Condition]
    action: str = "buy"                # buy / skip / wait
    buy_amount_pct: float = 100.0      # % of allocated capital
    take_profit: float = 50.0
    stop_loss: float = 15.0
    slippage: float = 12.0
    min_conditions_met: int = 0        # 0 = all must be met

    def evaluate(self, indicator_values: dict[str, float]) -> tuple[bool, float]:
        """
        Evaluate all conditions.
        Returns (passes, confidence_score).
        """
        if not self.conditions:
            return False, 0.0

        met = 0
        total_weight = 0.0
        weighted_met = 0.0

        for cond in self.conditions:
            value = indicator_values.get(cond.indicator_name, 0)
            total_weight += cond.weight
            if cond.evaluate(value):
                met += 1
                weighted_met += cond.weight

        min_needed = self.min_conditions_met or len(self.conditions)
        passes = met >= min_needed
        confidence = weighted_met / total_weight if total_weight > 0 else 0

        return passes, confidence

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "conditions": [c.to_dict() for c in self.conditions],
            "action": self.action,
            "take_profit": self.take_profit,
            "stop_loss": self.stop_loss,
            "slippage": self.slippage,
            "min_conditions_met": self.min_conditions_met,
        }


@dataclass
class GeneratedStrategy:
    """A complete generated strategy with performance metrics."""
    name: str = ""
    generation: int = 0
    rules: list = field(default_factory=list)  # list[StrategyRule]
    # Performance from backtesting
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    total_pnl_pct: float = 0.0
    avg_pnl_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    sharpe_ratio: float = 0.0
    profit_factor: float = 0.0
    # Scoring
    fitness_score: float = 0.0         # composite performance score
    complexity: int = 0                # number of conditions

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "generation": self.generation,
            "rules": [r.to_dict() for r in self.rules],
            "total_trades": self.total_trades,
            "win_rate": round(self.win_rate, 3),
            "total_pnl_pct": round(self.total_pnl_pct, 2),
            "avg_pnl_pct": round(self.avg_pnl_pct, 2),
            "max_drawdown_pct": round(self.max_drawdown_pct, 2),
            "sharpe_ratio": round(self.sharpe_ratio, 3),
            "profit_factor": round(self.profit_factor, 2),
            "fitness_score": round(self.fitness_score, 4),
            "complexity": self.complexity,
        }


@dataclass
class GeneratorConfig:
    """Configuration for the auto strategy generator."""
    enabled: bool = True
    population_size: int = 20          # strategies per generation
    max_generations: int = 10
    max_rules_per_strategy: int = 3
    max_conditions_per_rule: int = 5
    min_conditions_per_rule: int = 2
    # Genetic operators
    mutation_rate: float = 0.2
    crossover_rate: float = 0.5
    elitism_count: int = 2             # top N preserved between generations
    # Fitness weights
    weight_pnl: float = 0.3
    weight_win_rate: float = 0.25
    weight_sharpe: float = 0.25
    weight_drawdown: float = 0.2
    # Backtesting
    min_trades_for_valid: int = 5
    tournament_size: int = 3

    def to_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "population_size": self.population_size,
            "max_generations": self.max_generations,
            "mutation_rate": self.mutation_rate,
            "crossover_rate": self.crossover_rate,
            "elitism_count": self.elitism_count,
        }


# ═══════════════════════════════════════════════════════════════════
#  Indicator Library
# ═══════════════════════════════════════════════════════════════════

class IndicatorLibrary:
    """Library of available indicators for strategy building."""

    INDICATORS = [
        Indicator("pump_score", IndicatorType.PUMP_SCORE, "pump_score", 1.0, 0, 100),
        Indicator("social_sentiment", IndicatorType.SOCIAL_SENTIMENT, "social_sentiment_score", 1.0, 0, 100),
        Indicator("ml_prediction", IndicatorType.ML_PREDICTION, "ml_pump_score", 1.0, 0, 100),
        Indicator("whale_buys", IndicatorType.WHALE_ACTIVITY, "whale_total_buys", 0.8, 0, 20),
        Indicator("whale_risk", IndicatorType.WHALE_ACTIVITY, "whale_risk_score", 0.7, 0, 100),
        Indicator("liquidity_usd", IndicatorType.LIQUIDITY_DEPTH, "dexscreener_liquidity", 1.0, 0, 500000),
        Indicator("volatility", IndicatorType.VOLATILITY, "volatility_score", 0.6, 0, 100),
        Indicator("dev_score", IndicatorType.DEV_REPUTATION, "dev_score", 0.9, 0, 100),
        Indicator("mev_frontrun_risk", IndicatorType.MEV_RISK, "mev_frontrun_risk", 0.7, 0, 1),
        Indicator("price_change_h1", IndicatorType.PRICE_MOMENTUM, "dexscreener_price_change_h1", 0.5, -50, 100),
        Indicator("holder_count", IndicatorType.HOLDER_GROWTH, "holder_count", 0.6, 0, 5000),
        Indicator("lp_lock_pct", IndicatorType.LP_LOCK_QUALITY, "lp_lock_percent", 1.0, 0, 100),
        Indicator("buy_tax", IndicatorType.PUMP_SCORE, "buy_tax", 0.8, 0, 30),
        Indicator("sell_tax", IndicatorType.PUMP_SCORE, "sell_tax", 0.8, 0, 30),
    ]

    @classmethod
    def get_all(cls) -> list[Indicator]:
        return list(cls.INDICATORS)

    @classmethod
    def get_by_name(cls, name: str) -> Optional[Indicator]:
        for ind in cls.INDICATORS:
            if ind.name == name:
                return ind
        return None

    @classmethod
    def get_names(cls) -> list[str]:
        return [i.name for i in cls.INDICATORS]

    @classmethod
    def extract_all(cls, token_info) -> dict[str, float]:
        """Extract all indicator values from token info."""
        values = {}
        for ind in cls.INDICATORS:
            values[ind.name] = ind.extract(token_info)
        return values


# ═══════════════════════════════════════════════════════════════════
#  Strategy Generator (Genetic Algorithm)
# ═══════════════════════════════════════════════════════════════════

class GeneticStrategyGenerator:
    """Generates and evolves strategies using genetic algorithm."""

    def __init__(self, config: GeneratorConfig = None):
        self.config = config or GeneratorConfig()
        self._indicator_names = IndicatorLibrary.get_names()
        self._generation = 0

    def generate_random_condition(self) -> Condition:
        """Create a random condition."""
        indicator = random.choice(self._indicator_names)
        ind_obj = IndicatorLibrary.get_by_name(indicator)
        if not ind_obj:
            return Condition(indicator_name=indicator, operator=">=", threshold=50)

        operators = [">=", ">", "<=", "<", "between"]
        op = random.choice(operators)

        lo = ind_obj.normalize_min
        hi = ind_obj.normalize_max
        threshold = random.uniform(lo, hi)

        cond = Condition(
            indicator_name=indicator,
            operator=op,
            threshold=round(threshold, 2),
            threshold_high=round(random.uniform(threshold, hi), 2) if op == "between" else 0,
            weight=round(random.uniform(0.5, 1.5), 2),
        )
        return cond

    def generate_random_rule(self) -> StrategyRule:
        """Create a random strategy rule."""
        n_conditions = random.randint(
            self.config.min_conditions_per_rule,
            self.config.max_conditions_per_rule,
        )
        conditions = [self.generate_random_condition() for _ in range(n_conditions)]

        rule = StrategyRule(
            name=f"rule_{random.randint(1000, 9999)}",
            conditions=conditions,
            action="buy",
            take_profit=round(random.uniform(20, 150), 1),
            stop_loss=round(random.uniform(5, 40), 1),
            slippage=round(random.uniform(5, 20), 1),
            min_conditions_met=random.randint(max(1, n_conditions - 1), n_conditions),
        )
        return rule

    def generate_random_strategy(self) -> GeneratedStrategy:
        """Create a random strategy."""
        n_rules = random.randint(1, self.config.max_rules_per_strategy)
        rules = [self.generate_random_rule() for _ in range(n_rules)]

        strategy = GeneratedStrategy(
            name=f"gen{self._generation}_strat_{random.randint(1000, 9999)}",
            generation=self._generation,
            rules=rules,
            complexity=sum(len(r.conditions) for r in rules),
        )
        return strategy

    def generate_population(self) -> list[GeneratedStrategy]:
        """Generate initial population."""
        return [self.generate_random_strategy() for _ in range(self.config.population_size)]

    def mutate(self, strategy: GeneratedStrategy) -> GeneratedStrategy:
        """Mutate a strategy by changing random conditions."""
        new_strategy = GeneratedStrategy(
            name=f"gen{self._generation}_mut_{random.randint(1000, 9999)}",
            generation=self._generation,
            rules=[],
            complexity=0,
        )

        for rule in strategy.rules:
            new_conditions = []
            for cond in rule.conditions:
                if random.random() < self.config.mutation_rate:
                    # Replace with random condition
                    new_conditions.append(self.generate_random_condition())
                else:
                    # Slightly perturb threshold
                    new_cond = Condition(
                        indicator_name=cond.indicator_name,
                        operator=cond.operator,
                        threshold=cond.threshold * random.uniform(0.8, 1.2),
                        threshold_high=cond.threshold_high * random.uniform(0.8, 1.2),
                        weight=cond.weight,
                    )
                    new_conditions.append(new_cond)

            new_rule = StrategyRule(
                name=rule.name,
                conditions=new_conditions,
                action=rule.action,
                take_profit=rule.take_profit * random.uniform(0.9, 1.1),
                stop_loss=rule.stop_loss * random.uniform(0.9, 1.1),
                slippage=rule.slippage,
                min_conditions_met=rule.min_conditions_met,
            )
            new_strategy.rules.append(new_rule)

        new_strategy.complexity = sum(len(r.conditions) for r in new_strategy.rules)
        return new_strategy

    def crossover(self, parent1: GeneratedStrategy, parent2: GeneratedStrategy) -> GeneratedStrategy:
        """Combine rules from two parents."""
        child = GeneratedStrategy(
            name=f"gen{self._generation}_cross_{random.randint(1000, 9999)}",
            generation=self._generation,
            rules=[],
        )

        # Take some rules from each parent
        all_rules = parent1.rules + parent2.rules
        n_rules = min(len(all_rules), self.config.max_rules_per_strategy)
        child.rules = random.sample(all_rules, n_rules) if n_rules <= len(all_rules) else list(all_rules)
        child.complexity = sum(len(r.conditions) for r in child.rules)

        return child

    def tournament_select(self, population: list[GeneratedStrategy]) -> GeneratedStrategy:
        """Tournament selection — pick best from random subset."""
        tournament = random.sample(population, min(self.config.tournament_size, len(population)))
        return max(tournament, key=lambda s: s.fitness_score)

    def evolve(self, population: list[GeneratedStrategy]) -> list[GeneratedStrategy]:
        """Evolve population to next generation."""
        self._generation += 1

        # Sort by fitness
        sorted_pop = sorted(population, key=lambda s: -s.fitness_score)

        # Elitism: keep top N
        new_pop = sorted_pop[:self.config.elitism_count]

        # Fill rest with crossover + mutation
        while len(new_pop) < self.config.population_size:
            if random.random() < self.config.crossover_rate and len(sorted_pop) >= 2:
                p1 = self.tournament_select(sorted_pop)
                p2 = self.tournament_select(sorted_pop)
                child = self.crossover(p1, p2)
            else:
                parent = self.tournament_select(sorted_pop)
                child = self.mutate(parent)

            new_pop.append(child)

        return new_pop


# ═══════════════════════════════════════════════════════════════════
#  Fitness Evaluator
# ═══════════════════════════════════════════════════════════════════

class FitnessEvaluator:
    """Evaluates strategy fitness against historical data."""

    def __init__(self, config: GeneratorConfig = None):
        self.config = config or GeneratorConfig()

    def evaluate(self, strategy: GeneratedStrategy, historical_tokens: list) -> GeneratedStrategy:
        """
        Evaluate strategy against historical token data.
        Each token in historical_tokens should have a token_info-like object
        and an actual_pnl field.
        """
        trades = []
        for token_data in historical_tokens:
            token_info = token_data.get("token_info")
            actual_pnl = token_data.get("actual_pnl", 0)
            if not token_info:
                continue

            # Extract indicators
            values = IndicatorLibrary.extract_all(token_info)

            # Evaluate strategy rules
            should_buy = False
            best_tp = 50.0
            best_sl = 15.0
            for rule in strategy.rules:
                passes, confidence = rule.evaluate(values)
                if passes and confidence >= 0.5:
                    should_buy = True
                    best_tp = rule.take_profit
                    best_sl = rule.stop_loss
                    break

            if should_buy:
                # Simulate PnL based on TP/SL
                if actual_pnl >= best_tp:
                    pnl = best_tp
                elif actual_pnl <= -best_sl:
                    pnl = -best_sl
                else:
                    pnl = actual_pnl

                trades.append(pnl)

        # Compute metrics
        strategy.total_trades = len(trades)
        if not trades:
            strategy.fitness_score = 0
            return strategy

        strategy.winning_trades = sum(1 for t in trades if t > 0)
        strategy.losing_trades = sum(1 for t in trades if t <= 0)
        strategy.win_rate = strategy.winning_trades / len(trades) if trades else 0
        strategy.total_pnl_pct = sum(trades)
        strategy.avg_pnl_pct = sum(trades) / len(trades) if trades else 0

        # Max drawdown
        peak = 0.0
        equity = 0.0
        max_dd = 0.0
        for pnl in trades:
            equity += pnl
            peak = max(peak, equity)
            dd = peak - equity
            max_dd = max(max_dd, dd)
        strategy.max_drawdown_pct = max_dd

        # Sharpe ratio
        if len(trades) > 1:
            mean_pnl = sum(trades) / len(trades)
            std_pnl = math.sqrt(sum((p - mean_pnl) ** 2 for p in trades) / (len(trades) - 1))
            strategy.sharpe_ratio = mean_pnl / std_pnl if std_pnl > 0 else 0
        else:
            strategy.sharpe_ratio = 0

        # Profit factor
        gross_profit = sum(t for t in trades if t > 0)
        gross_loss = abs(sum(t for t in trades if t < 0))
        strategy.profit_factor = gross_profit / gross_loss if gross_loss > 0 else (10.0 if gross_profit > 0 else 0)

        # Composite fitness score
        cfg = self.config
        fitness = 0
        # Normalize components
        pnl_norm = max(-1, min(1, strategy.avg_pnl_pct / 50))  # -1 to 1
        wr_norm = strategy.win_rate                               # 0 to 1
        sharpe_norm = max(-1, min(1, strategy.sharpe_ratio / 3))  # -1 to 1
        dd_norm = 1 - min(1, strategy.max_drawdown_pct / 100)    # 0 to 1 (lower dd = better)

        fitness = (
            cfg.weight_pnl * pnl_norm +
            cfg.weight_win_rate * wr_norm +
            cfg.weight_sharpe * sharpe_norm +
            cfg.weight_drawdown * dd_norm
        )

        # Penalty for too few trades
        if strategy.total_trades < self.config.min_trades_for_valid:
            fitness *= strategy.total_trades / self.config.min_trades_for_valid

        # Penalty for excessive complexity (Occam's razor)
        if strategy.complexity > 10:
            fitness *= 0.9

        strategy.fitness_score = round(fitness, 4)
        return strategy


# ═══════════════════════════════════════════════════════════════════
#  Main: Auto Strategy Generator
# ═══════════════════════════════════════════════════════════════════

class AutoStrategyGenerator:
    """
    Automatically generates, tests, and evolves trading strategies.

    Usage:
        generator = AutoStrategyGenerator()
        result = await generator.run(historical_data)
        best = result["best_strategy"]
    """

    def __init__(self, config: GeneratorConfig = None):
        self.config = config or GeneratorConfig()
        self._genetic = GeneticStrategyGenerator(self.config)
        self._evaluator = FitnessEvaluator(self.config)
        self._historical_data: list[dict] = []
        self._best_strategy: Optional[GeneratedStrategy] = None
        self._all_generations: list[list[GeneratedStrategy]] = []
        self._total_runs = 0
        self._emit_callback = None

    def set_emit_callback(self, callback):
        """Set callback for progress events."""
        self._emit_callback = callback

    def add_historical_data(self, token_info, actual_pnl: float):
        """Add a historical trade data point for backtesting strategies."""
        self._historical_data.append({
            "token_info": token_info,
            "actual_pnl": actual_pnl,
        })

    def add_historical_batch(self, data: list[dict]):
        """Add batch of historical data [{token_info, actual_pnl}]."""
        self._historical_data.extend(data)

    async def run(self, historical_data: list[dict] = None,
                  max_generations: int = 0) -> dict:
        """
        Run the genetic strategy generation loop.

        Returns dict with best_strategy, generation_stats, improvement_pct.
        """
        self._total_runs += 1
        data = historical_data or self._historical_data

        if len(data) < self.config.min_trades_for_valid:
            return {
                "status": "insufficient_data",
                "data_points": len(data),
                "min_required": self.config.min_trades_for_valid,
                "best_strategy": None,
            }

        generations = max_generations or self.config.max_generations

        # Initial population
        population = self._genetic.generate_population()

        # Evaluate initial population
        for strategy in population:
            self._evaluator.evaluate(strategy, data)

        best_ever = max(population, key=lambda s: s.fitness_score)
        initial_best = best_ever.fitness_score
        self._all_generations.append(sorted(population, key=lambda s: -s.fitness_score))

        gen_stats = []

        for gen in range(generations):
            # Evolve
            population = self._genetic.evolve(population)

            # Evaluate new generation
            for strategy in population:
                self._evaluator.evaluate(strategy, data)

            gen_best = max(population, key=lambda s: s.fitness_score)
            gen_avg = sum(s.fitness_score for s in population) / len(population)

            gen_stats.append({
                "generation": gen + 1,
                "best_fitness": round(gen_best.fitness_score, 4),
                "avg_fitness": round(gen_avg, 4),
                "best_win_rate": round(gen_best.win_rate, 3),
                "best_pnl": round(gen_best.total_pnl_pct, 2),
            })

            if gen_best.fitness_score > best_ever.fitness_score:
                best_ever = gen_best

            self._all_generations.append(sorted(population, key=lambda s: -s.fitness_score))

            if self._emit_callback:
                try:
                    await self._emit_callback("strategy_gen_progress", {
                        "generation": gen + 1,
                        "total_generations": generations,
                        "best_fitness": round(best_ever.fitness_score, 4),
                    })
                except Exception:
                    pass

        self._best_strategy = best_ever

        improvement = 0
        if initial_best != 0:
            improvement = ((best_ever.fitness_score - initial_best) / abs(initial_best)) * 100

        return {
            "status": "completed",
            "generations": generations,
            "population_size": self.config.population_size,
            "total_evaluated": generations * self.config.population_size,
            "best_strategy": best_ever.to_dict(),
            "improvement_pct": round(improvement, 2),
            "generation_stats": gen_stats,
        }

    def evaluate_token(self, token_info) -> dict:
        """
        Evaluate a live token against the best generated strategy.
        Returns buy/skip decision with confidence.
        """
        if not self._best_strategy:
            return {"decision": "skip", "reason": "no strategy generated yet", "confidence": 0}

        values = IndicatorLibrary.extract_all(token_info)

        for rule in self._best_strategy.rules:
            passes, confidence = rule.evaluate(values)
            if passes:
                return {
                    "decision": rule.action,
                    "confidence": round(confidence, 3),
                    "take_profit": rule.take_profit,
                    "stop_loss": rule.stop_loss,
                    "slippage": rule.slippage,
                    "strategy_name": self._best_strategy.name,
                    "strategy_fitness": self._best_strategy.fitness_score,
                }

        return {
            "decision": "skip",
            "reason": "no rules matched",
            "confidence": 0,
            "strategy_name": self._best_strategy.name,
        }

    def get_best_strategy(self) -> Optional[dict]:
        """Get the current best strategy."""
        if self._best_strategy:
            return self._best_strategy.to_dict()
        return None

    def get_top_strategies(self, n: int = 5) -> list[dict]:
        """Get top N strategies from the last generation."""
        if not self._all_generations:
            return []
        last_gen = self._all_generations[-1]
        return [s.to_dict() for s in last_gen[:n]]

    def configure(self, **kwargs):
        """Update configuration."""
        for k, v in kwargs.items():
            if hasattr(self.config, k):
                setattr(self.config, k, v)

    def get_stats(self) -> dict:
        return {
            "total_runs": self._total_runs,
            "historical_data_points": len(self._historical_data),
            "generations_completed": len(self._all_generations),
            "best_strategy": self._best_strategy.to_dict() if self._best_strategy else None,
            "config": self.config.to_dict(),
        }

    def reset(self):
        """Reset the generator."""
        self._best_strategy = None
        self._all_generations.clear()
        self._genetic._generation = 0

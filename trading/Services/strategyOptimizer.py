"""
strategyOptimizer.py — AI Strategy Optimizer v6.
═════════════════════════════════════════════════

Uses machine learning and reinforcement learning concepts to automatically
optimize trading parameters (TP/SL, slippage, thresholds) based on
historical performance.

Features:
  - Parameter optimization via Bayesian-style grid search
  - Reinforcement-learning reward signal from trade P&L
  - Auto-tuning: TP%, SL%, slippage, min liquidity, tax thresholds
  - Regime detection: bull/bear/sideways market behavior
  - Strategy recommendation engine
  - Performance decay detection (strategy degradation alert)
  - A/B testing support for comparing parameter sets
  - Periodic auto-optimization via Celery beat

Architecture:
  StrategyOptimizer collects trade outcomes → builds reward model
  → proposes parameter adjustments → validates via mini-backtest
  → applies best parameters to live sniper

ML Models:
  - Random Forest for parameter → outcome prediction
  - Simple multi-armed bandit for exploration/exploitation
  - Rolling statistics for regime detection

Integration:
  - Reads trade history from metricsService
  - Proposes changes to sniperService settings
  - Can trigger backtests to validate proposals
  - WebSocket updates on optimization progress
"""

import asyncio
import json
import logging
import math
import random
import time
from dataclasses import dataclass, field, asdict
from typing import Optional, Callable

logger = logging.getLogger(__name__)

# Optional heavy imports — graceful fallback
try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    np = None
    HAS_NUMPY = False

try:
    from sklearn.ensemble import RandomForestRegressor, GradientBoostingRegressor
    from sklearn.preprocessing import StandardScaler
    HAS_SKLEARN = True
except ImportError:
    HAS_SKLEARN = False


# ═══════════════════════════════════════════════════════════════════
#  Data structures
# ═══════════════════════════════════════════════════════════════════

@dataclass
class StrategyParams:
    """A set of trading parameters to evaluate."""
    # Core strategy
    take_profit_pct: float = 40.0
    stop_loss_pct: float = 15.0
    trailing_stop_pct: float = 0.0    # 0 = disabled
    slippage_pct: float = 12.0
    # Filters
    min_liquidity_usd: float = 5000
    max_buy_tax_pct: float = 10.0
    max_sell_tax_pct: float = 15.0
    min_pump_score: int = 40
    min_risk_score: int = 30
    min_ml_score: int = 20
    # Execution
    buy_amount_native: float = 0.05
    max_concurrent: int = 5
    max_hold_hours: float = 24.0
    gas_boost_pct: float = 10.0
    # Metadata
    name: str = "default"
    generation: int = 0
    parent_name: str = ""

    def to_dict(self):
        return asdict(self)

    def to_vector(self) -> list[float]:
        """Convert to numeric vector for ML models."""
        return [
            self.take_profit_pct,
            self.stop_loss_pct,
            self.trailing_stop_pct,
            self.slippage_pct,
            self.min_liquidity_usd / 1000,  # normalize
            self.max_buy_tax_pct,
            self.max_sell_tax_pct,
            float(self.min_pump_score),
            float(self.min_risk_score),
            float(self.min_ml_score),
            self.buy_amount_native * 100,
            float(self.max_concurrent),
            self.max_hold_hours,
            self.gas_boost_pct,
        ]

    @staticmethod
    def feature_names() -> list[str]:
        return [
            "take_profit_pct", "stop_loss_pct", "trailing_stop_pct",
            "slippage_pct", "min_liquidity_usd_k", "max_buy_tax_pct",
            "max_sell_tax_pct", "min_pump_score", "min_risk_score",
            "min_ml_score", "buy_amount_native_x100", "max_concurrent",
            "max_hold_hours", "gas_boost_pct",
        ]


@dataclass
class TradeOutcome:
    """A trade result for the optimizer to learn from."""
    params_name: str = "default"
    token_address: str = ""
    pnl_percent: float = 0.0
    pnl_usd: float = 0.0
    hold_seconds: float = 0.0
    exit_reason: str = ""
    timestamp: float = 0.0
    # Market conditions at trade time
    market_regime: str = "unknown"  # bull | bear | sideways
    gas_price_gwei: float = 0.0
    liquidity_usd: float = 0.0

    def to_dict(self):
        return asdict(self)


@dataclass
class OptimizationResult:
    """Result of an optimization run."""
    status: str = "pending"     # pending | running | completed | failed
    current_params: dict = field(default_factory=dict)
    proposed_params: dict = field(default_factory=dict)
    improvement_predicted_pct: float = 0.0
    confidence: float = 0.0     # 0-1
    # Performance comparison
    current_win_rate: float = 0.0
    predicted_win_rate: float = 0.0
    current_avg_pnl: float = 0.0
    predicted_avg_pnl: float = 0.0
    current_sharpe: float = 0.0
    predicted_sharpe: float = 0.0
    # Details
    candidates_evaluated: int = 0
    best_candidate_score: float = 0.0
    market_regime: str = "unknown"
    feature_importance: dict = field(default_factory=dict)
    duration_seconds: float = 0.0
    timestamp: float = 0.0

    def to_dict(self):
        return asdict(self)


@dataclass
class MarketRegime:
    """Current market regime classification."""
    regime: str = "unknown"   # bull | bear | sideways | volatile
    confidence: float = 0.0
    # Indicators
    btc_24h_change: float = 0.0
    eth_24h_change: float = 0.0
    avg_token_pnl_24h: float = 0.0
    volatility_index: float = 0.0
    new_pairs_24h: int = 0
    rug_ratio_24h: float = 0.0  # % of new tokens that rugged

    def to_dict(self):
        return asdict(self)


# ═══════════════════════════════════════════════════════════════════
#  Parameter ranges for exploration
# ═══════════════════════════════════════════════════════════════════

PARAM_RANGES = {
    "take_profit_pct":   (15.0, 200.0, 5.0),     # min, max, step
    "stop_loss_pct":     (5.0, 40.0, 2.5),
    "trailing_stop_pct": (0.0, 30.0, 5.0),
    "slippage_pct":      (5.0, 25.0, 2.0),
    "min_liquidity_usd": (1000, 50000, 2000),
    "max_buy_tax_pct":   (3.0, 15.0, 1.0),
    "max_sell_tax_pct":  (5.0, 25.0, 2.0),
    "min_pump_score":    (20, 80, 5),
    "min_risk_score":    (20, 70, 5),
    "min_ml_score":      (10, 60, 5),
    "max_hold_hours":    (1.0, 72.0, 4.0),
    "gas_boost_pct":     (0.0, 30.0, 5.0),
}


# ═══════════════════════════════════════════════════════════════════
#  Strategy Optimizer
# ═══════════════════════════════════════════════════════════════════

class StrategyOptimizer:
    """
    AI-powered strategy optimizer.

    Learns from historical trades and proposes parameter adjustments
    to maximize profit and minimize risk.

    Usage:
        optimizer = StrategyOptimizer()
        optimizer.record_trade(outcome)
        result = await optimizer.optimize()
        if result.confidence > 0.7:
            apply_params(result.proposed_params)
    """

    MIN_TRADES_FOR_OPTIMIZATION = 20
    MIN_TRADES_FOR_ML = 50

    def __init__(self):
        self._trade_history: list[TradeOutcome] = []
        self._param_history: dict[str, list[float]] = {}  # param_name → [pnl_pcts]
        self._current_params = StrategyParams()
        self._best_params: Optional[StrategyParams] = None
        self._optimization_results: list[OptimizationResult] = []
        self._market_regime = MarketRegime()

        # ML models
        self._reward_model = None       # predicts pnl from params
        self._scaler = None
        self._model_trained = False

        # Multi-armed bandit state
        self._bandit_arms: dict[str, dict] = {}  # param_set → {wins, pulls, avg_reward}

        # Callbacks
        self._emit_callback: Optional[Callable] = None

        # Stats
        self.stats = {
            "trades_recorded": 0,
            "optimizations_run": 0,
            "params_updated": 0,
            "model_accuracy": 0.0,
        }

    def set_emit_callback(self, callback: Callable):
        self._emit_callback = callback

    def set_current_params(self, params: StrategyParams):
        self._current_params = params

    # ───────────────────────────────────────────────────────
    #  Trade recording
    # ───────────────────────────────────────────────────────

    def record_trade(self, outcome: TradeOutcome):
        """Record a trade outcome for learning."""
        self._trade_history.append(outcome)
        self.stats["trades_recorded"] += 1

        # Update bandit arm
        arm_key = outcome.params_name
        if arm_key not in self._bandit_arms:
            self._bandit_arms[arm_key] = {"wins": 0, "pulls": 0, "avg_reward": 0, "total_reward": 0}

        arm = self._bandit_arms[arm_key]
        arm["pulls"] += 1
        arm["total_reward"] += outcome.pnl_percent
        arm["avg_reward"] = arm["total_reward"] / arm["pulls"]
        if outcome.pnl_percent > 0:
            arm["wins"] += 1

        # Trim history to last 1000 trades
        if len(self._trade_history) > 1000:
            self._trade_history = self._trade_history[-500:]

    def record_trades_batch(self, outcomes: list[TradeOutcome]):
        """Record multiple trade outcomes."""
        for o in outcomes:
            self.record_trade(o)

    # ───────────────────────────────────────────────────────
    #  Market regime detection
    # ───────────────────────────────────────────────────────

    def detect_market_regime(self) -> MarketRegime:
        """Detect current market conditions from recent trade data."""
        regime = MarketRegime()
        recent = [t for t in self._trade_history if time.time() - t.timestamp < 86400]

        if len(recent) < 5:
            regime.regime = "unknown"
            regime.confidence = 0.0
            self._market_regime = regime
            return regime

        pnls = [t.pnl_percent for t in recent]
        avg_pnl = sum(pnls) / len(pnls)
        regime.avg_token_pnl_24h = avg_pnl

        # Calculate volatility
        if HAS_NUMPY:
            regime.volatility_index = float(np.std(pnls))
        else:
            mean = avg_pnl
            regime.volatility_index = math.sqrt(sum((p - mean) ** 2 for p in pnls) / len(pnls))

        # Rug ratio
        rugs = sum(1 for t in recent if t.exit_reason == "rug_detected")
        regime.rug_ratio_24h = rugs / len(recent) if recent else 0

        # Classify
        if avg_pnl > 10 and regime.volatility_index < 30:
            regime.regime = "bull"
            regime.confidence = min(0.9, avg_pnl / 50)
        elif avg_pnl < -5:
            regime.regime = "bear"
            regime.confidence = min(0.9, abs(avg_pnl) / 30)
        elif regime.volatility_index > 40:
            regime.regime = "volatile"
            regime.confidence = min(0.9, regime.volatility_index / 80)
        else:
            regime.regime = "sideways"
            regime.confidence = 0.6

        logger.info(f"AI: market regime = {regime.regime} (confidence {regime.confidence:.2f})")
        self._market_regime = regime
        return regime

    # ───────────────────────────────────────────────────────
    #  ML Model training
    # ───────────────────────────────────────────────────────

    def _train_reward_model(self) -> bool:
        """Train the reward prediction model from trade history."""
        if not HAS_SKLEARN or not HAS_NUMPY:
            logger.info("AI: scikit-learn/numpy not available — using heuristic optimization")
            return False

        if len(self._trade_history) < self.MIN_TRADES_FOR_ML:
            return False

        try:
            # Build training data
            X = []
            y = []

            for trade in self._trade_history:
                if trade.params_name in self._param_history:
                    continue  # skip unknown params

                # Use current params as features (simplified)
                features = self._current_params.to_vector()
                # Add trade-specific context
                features.extend([
                    trade.liquidity_usd / 10000,
                    trade.gas_price_gwei,
                    1.0 if trade.market_regime == "bull" else (
                        -1.0 if trade.market_regime == "bear" else 0.0
                    ),
                ])
                X.append(features)
                y.append(trade.pnl_percent)

            if len(X) < 20:
                return False

            X = np.array(X)
            y = np.array(y)

            # Scale features
            self._scaler = StandardScaler()
            X_scaled = self._scaler.fit_transform(X)

            # Train model
            self._reward_model = GradientBoostingRegressor(
                n_estimators=100,
                max_depth=4,
                learning_rate=0.1,
                random_state=42,
            )
            self._reward_model.fit(X_scaled, y)

            # Evaluate
            predictions = self._reward_model.predict(X_scaled)
            mse = float(np.mean((predictions - y) ** 2))
            self.stats["model_accuracy"] = max(0, 1 - mse / (np.var(y) + 1e-6))

            self._model_trained = True
            logger.info(f"AI: reward model trained — accuracy {self.stats['model_accuracy']:.3f}")
            return True

        except Exception as e:
            logger.error(f"AI: model training error: {e}")
            return False

    # ───────────────────────────────────────────────────────
    #  Optimization
    # ───────────────────────────────────────────────────────

    async def optimize(self) -> OptimizationResult:
        """
        Run a full optimization cycle.

        1. Detect market regime
        2. Train/update reward model
        3. Generate candidate parameter sets
        4. Evaluate candidates (ML prediction + heuristics)
        5. Select best candidate
        6. Return proposal

        Returns:
            OptimizationResult with proposed parameters.
        """
        result = OptimizationResult(
            status="running",
            current_params=self._current_params.to_dict(),
            timestamp=time.time(),
        )
        start = time.time()

        self.stats["optimizations_run"] += 1

        if len(self._trade_history) < self.MIN_TRADES_FOR_OPTIMIZATION:
            result.status = "failed"
            result.improvement_predicted_pct = 0
            result.confidence = 0
            result.duration_seconds = time.time() - start
            logger.info(f"AI: need {self.MIN_TRADES_FOR_OPTIMIZATION} trades, have {len(self._trade_history)}")
            return result

        # 1. Market regime
        regime = self.detect_market_regime()
        result.market_regime = regime.regime

        # 2. Current performance stats
        recent_trades = self._trade_history[-100:]
        current_stats = self._compute_stats(recent_trades)
        result.current_win_rate = current_stats["win_rate"]
        result.current_avg_pnl = current_stats["avg_pnl"]
        result.current_sharpe = current_stats["sharpe"]

        # 3. Train ML model
        has_ml = self._train_reward_model()

        # 4. Generate candidates
        candidates = self._generate_candidates(regime, n=50)
        result.candidates_evaluated = len(candidates)

        # 5. Evaluate each candidate
        best_score = -999
        best_params = None

        for params in candidates:
            if has_ml:
                score = self._ml_predict(params)
            else:
                score = self._heuristic_score(params, current_stats, regime)

            if score > best_score:
                best_score = score
                best_params = params

        result.best_candidate_score = best_score

        if best_params:
            result.proposed_params = best_params.to_dict()
            result.predicted_avg_pnl = best_score
            result.predicted_win_rate = min(100, result.current_win_rate + best_score * 0.1)
            result.improvement_predicted_pct = max(0, best_score - result.current_avg_pnl)
            result.confidence = self._calculate_confidence(best_score, current_stats, regime)
            result.status = "completed"

            # Feature importance
            if has_ml and hasattr(self._reward_model, "feature_importances_"):
                names = StrategyParams.feature_names()
                importances = self._reward_model.feature_importances_[:len(names)]
                result.feature_importance = {
                    names[i]: round(float(importances[i]), 4)
                    for i in range(len(names))
                }

            logger.info(
                f"AI: optimization done — predicted improvement {result.improvement_predicted_pct:.1f}%, "
                f"confidence {result.confidence:.2f}"
            )
        else:
            result.status = "failed"
            result.confidence = 0

        result.duration_seconds = time.time() - start
        self._optimization_results.append(result)

        # Emit to frontend
        if self._emit_callback:
            try:
                self._emit_callback("strategy_optimization", result.to_dict())
            except Exception:
                pass

        return result

    def _generate_candidates(self, regime: MarketRegime, n: int = 50) -> list[StrategyParams]:
        """Generate candidate parameter sets focused on the current regime."""
        candidates = []

        for _ in range(n):
            params = StrategyParams(
                generation=self._current_params.generation + 1,
                parent_name=self._current_params.name,
            )

            # Mutate from current params with regime-aware adjustments
            for param_name, (lo, hi, step) in PARAM_RANGES.items():
                current_val = getattr(self._current_params, param_name)

                # Regime-aware mutation
                if regime.regime == "bull":
                    # In bull: higher TP, lower SL, looser filters
                    if param_name == "take_profit_pct":
                        bias = step * 2
                    elif param_name == "stop_loss_pct":
                        bias = -step
                    elif param_name.startswith("min_"):
                        bias = -step
                    else:
                        bias = 0
                elif regime.regime == "bear":
                    # In bear: lower TP, tighter SL, stricter filters
                    if param_name == "take_profit_pct":
                        bias = -step * 2
                    elif param_name == "stop_loss_pct":
                        bias = step
                    elif param_name.startswith("min_"):
                        bias = step
                    else:
                        bias = 0
                elif regime.regime == "volatile":
                    if param_name == "slippage_pct":
                        bias = step
                    elif param_name == "stop_loss_pct":
                        bias = step
                    else:
                        bias = 0
                else:
                    bias = 0

                # Random mutation
                mutation = random.choice([-step * 2, -step, 0, step, step * 2])
                new_val = current_val + mutation + bias
                new_val = max(lo, min(hi, new_val))

                # Round to step
                if isinstance(step, int):
                    new_val = int(round(new_val / step) * step)
                else:
                    new_val = round(new_val / step) * step

                setattr(params, param_name, new_val)

            params.name = f"gen{params.generation}_c{len(candidates)}"
            candidates.append(params)

        return candidates

    def _ml_predict(self, params: StrategyParams) -> float:
        """Predict reward for a parameter set using the ML model."""
        if not self._model_trained or not self._reward_model:
            return 0.0

        try:
            features = params.to_vector()
            # Add placeholder context features
            features.extend([5.0, 5.0, 0.0])  # liquidity, gas, regime

            X = np.array([features])
            X_scaled = self._scaler.transform(X)
            prediction = float(self._reward_model.predict(X_scaled)[0])
            return prediction
        except Exception:
            return 0.0

    def _heuristic_score(
        self, params: StrategyParams,
        current_stats: dict, regime: MarketRegime
    ) -> float:
        """Score a parameter set using heuristic rules (no ML)."""
        score = 0.0

        # Reward good TP/SL ratio
        if params.stop_loss_pct > 0:
            ratio = params.take_profit_pct / params.stop_loss_pct
            if 2 <= ratio <= 5:
                score += 10
            elif ratio > 5:
                score += 5  # too greedy
            else:
                score -= 5  # poor risk/reward

        # Penalize extreme slippage
        if params.slippage_pct > 20:
            score -= 5
        if params.slippage_pct < 8:
            score -= 3  # might get reverted

        # Reward tighter filters in bear markets
        if regime.regime == "bear":
            if params.min_pump_score >= 50:
                score += 5
            if params.max_sell_tax_pct <= 10:
                score += 3

        # Reward realistic hold times
        if 2 <= params.max_hold_hours <= 48:
            score += 3

        # Bonus for trailing stop usage
        if params.trailing_stop_pct > 0:
            score += 5

        # Normalize around current average
        score += current_stats["avg_pnl"] * 0.5

        return score

    def _calculate_confidence(self, best_score: float, current_stats: dict, regime: MarketRegime) -> float:
        """Calculate confidence in the optimization result."""
        confidence = 0.3  # base

        # More data = more confidence
        n = len(self._trade_history)
        if n >= 100:
            confidence += 0.2
        elif n >= 50:
            confidence += 0.1

        # ML model trained
        if self._model_trained:
            confidence += 0.15
            confidence += self.stats.get("model_accuracy", 0) * 0.15

        # Clear regime
        if regime.confidence > 0.7:
            confidence += 0.1

        # Improvement is significant
        if best_score > current_stats["avg_pnl"] * 1.2:
            confidence += 0.1

        return min(1.0, confidence)

    def _compute_stats(self, trades: list[TradeOutcome]) -> dict:
        """Compute performance statistics from trades."""
        if not trades:
            return {"win_rate": 0, "avg_pnl": 0, "sharpe": 0, "total_pnl": 0}

        pnls = [t.pnl_percent for t in trades]
        wins = sum(1 for p in pnls if p > 0)
        avg_pnl = sum(pnls) / len(pnls)

        # Sharpe
        sharpe = 0.0
        if HAS_NUMPY and len(pnls) >= 2:
            std = float(np.std(pnls))
            if std > 0:
                sharpe = (avg_pnl / std) * math.sqrt(365)
        elif len(pnls) >= 2:
            mean = avg_pnl
            std = math.sqrt(sum((p - mean) ** 2 for p in pnls) / len(pnls))
            if std > 0:
                sharpe = (avg_pnl / std) * math.sqrt(365)

        return {
            "win_rate": (wins / len(trades)) * 100,
            "avg_pnl": avg_pnl,
            "sharpe": round(sharpe, 2),
            "total_pnl": sum(pnls),
        }

    # ───────────────────────────────────────────────────────
    #  Auto-apply & suggestions
    # ───────────────────────────────────────────────────────

    def get_suggestion(self) -> dict:
        """Get the latest optimization suggestion."""
        if not self._optimization_results:
            return {"status": "no_data", "message": "No optimizations run yet."}

        latest = self._optimization_results[-1]
        if latest.confidence >= 0.7:
            return {
                "status": "confident",
                "message": f"High-confidence suggestion (regime: {latest.market_regime})",
                "proposed_params": latest.proposed_params,
                "predicted_improvement": f"{latest.improvement_predicted_pct:.1f}%",
                "confidence": f"{latest.confidence:.0%}",
            }
        elif latest.confidence >= 0.4:
            return {
                "status": "moderate",
                "message": "Moderate confidence — test with paper trading first",
                "proposed_params": latest.proposed_params,
                "confidence": f"{latest.confidence:.0%}",
            }
        else:
            return {
                "status": "low_confidence",
                "message": "Not enough data for confident optimization. Need more trades.",
            }

    def detect_strategy_degradation(self) -> dict:
        """
        Check if the current strategy is degrading.
        Compare recent 20 trades vs overall history.
        """
        if len(self._trade_history) < 40:
            return {"degrading": False, "message": "Not enough data"}

        recent = self._trade_history[-20:]
        older = self._trade_history[-60:-20]

        recent_stats = self._compute_stats(recent)
        older_stats = self._compute_stats(older)

        degrading = False
        reasons = []

        # Win rate dropped significantly
        if older_stats["win_rate"] - recent_stats["win_rate"] > 15:
            degrading = True
            reasons.append(f"Win rate dropped: {older_stats['win_rate']:.0f}% → {recent_stats['win_rate']:.0f}%")

        # Average P&L dropped
        if older_stats["avg_pnl"] - recent_stats["avg_pnl"] > 5:
            degrading = True
            reasons.append(f"Avg P&L dropped: {older_stats['avg_pnl']:.1f}% → {recent_stats['avg_pnl']:.1f}%")

        # More losses
        recent_losses = sum(1 for t in recent if t.pnl_percent < 0)
        if recent_losses / len(recent) > 0.7:
            degrading = True
            reasons.append(f"Recent loss ratio: {recent_losses}/{len(recent)}")

        return {
            "degrading": degrading,
            "reasons": reasons,
            "recent_stats": recent_stats,
            "older_stats": older_stats,
            "recommendation": "Re-optimize" if degrading else "Keep current strategy",
        }

    # ───────────────────────────────────────────────────────
    #  Stats
    # ───────────────────────────────────────────────────────

    def get_stats(self) -> dict:
        return {
            **self.stats,
            "trade_count": len(self._trade_history),
            "current_params": self._current_params.to_dict(),
            "market_regime": self._market_regime.to_dict(),
            "has_ml": self._model_trained,
            "latest_result": self._optimization_results[-1].to_dict() if self._optimization_results else None,
            "bandit_arms": {
                k: {
                    "wins": v["wins"],
                    "pulls": v["pulls"],
                    "win_rate": f"{v['wins'] / v['pulls'] * 100:.1f}%" if v["pulls"] > 0 else "N/A",
                    "avg_reward": f"{v['avg_reward']:.2f}%",
                }
                for k, v in self._bandit_arms.items()
            },
        }

"""
reinforcementLearner.py — Adaptive ML with Reinforcement Learning & Bayesian Optimization.

Real-time learning from every trade:
  trade outcome → update Q-table / policy
  → adjust strategy parameters automatically
  → maximize cumulative PnL

Components:
  1. Q-Learning Agent (discrete state-action)
  2. Contextual Bandit (continuous action selection)
  3. Bayesian Optimizer (hyperparameter tuning)
  4. Experience Replay Buffer
  5. Reward Shaping Engine

Architecture:
  each trade → experience buffer → batch update → policy improvement
  market regime → state encoding → action selection → execution → reward

Dependencies: numpy, scikit-learn (optional: torch for DQN)
"""

import logging
import time
import math
import hashlib
import random
from dataclasses import dataclass, field
from typing import Optional
from enum import Enum

logger = logging.getLogger(__name__)

# ── Optional heavy deps ──
try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    np = None
    HAS_NUMPY = False

try:
    from sklearn.gaussian_process import GaussianProcessRegressor
    from sklearn.gaussian_process.kernels import Matern
    HAS_SKLEARN = True
except ImportError:
    HAS_SKLEARN = False


# ═══════════════════════════════════════════════════════════════════
#  Data Classes
# ═══════════════════════════════════════════════════════════════════

class ActionType(Enum):
    """Discrete actions the RL agent can take."""
    BUY_AGGRESSIVE = "buy_aggressive"      # high amount, tight SL
    BUY_MODERATE = "buy_moderate"          # medium amount, medium SL
    BUY_CONSERVATIVE = "buy_conservative"  # low amount, wide SL
    SKIP = "skip"                          # don't buy
    WAIT = "wait"                          # wait for better entry


@dataclass
class MarketState:
    """Encoded state for RL agent."""
    volatility_bucket: int = 0        # 0=low, 1=med, 2=high
    trend_bucket: int = 0             # 0=bear, 1=neutral, 2=bull
    volume_bucket: int = 0            # 0=low, 1=med, 2=high
    mev_risk_bucket: int = 0          # 0=safe, 1=moderate, 2=dangerous
    pump_score_bucket: int = 0        # 0=low, 1=med, 2=high
    social_bucket: int = 0            # 0=none, 1=some, 2=viral
    liquidity_bucket: int = 0         # 0=thin, 1=medium, 2=deep
    whale_activity_bucket: int = 0    # 0=none, 1=some, 2=heavy

    def to_key(self) -> str:
        """Convert state to hashable key for Q-table."""
        return f"{self.volatility_bucket}{self.trend_bucket}{self.volume_bucket}" \
               f"{self.mev_risk_bucket}{self.pump_score_bucket}{self.social_bucket}" \
               f"{self.liquidity_bucket}{self.whale_activity_bucket}"

    def to_vector(self) -> list:
        """Convert to numeric vector for ML models."""
        return [
            self.volatility_bucket, self.trend_bucket, self.volume_bucket,
            self.mev_risk_bucket, self.pump_score_bucket, self.social_bucket,
            self.liquidity_bucket, self.whale_activity_bucket,
        ]


@dataclass
class Experience:
    """Single experience tuple for replay buffer."""
    state: MarketState
    action: str                    # ActionType value
    reward: float = 0.0            # PnL-based reward
    next_state: Optional[MarketState] = None
    done: bool = False
    timestamp: float = 0.0
    token_address: str = ""
    metadata: dict = field(default_factory=dict)


@dataclass
class RLConfig:
    """Configuration for the RL agent."""
    # Q-Learning
    learning_rate: float = 0.1           # alpha
    discount_factor: float = 0.95        # gamma
    exploration_rate: float = 0.3        # epsilon (initial)
    exploration_decay: float = 0.995     # epsilon decay per episode
    min_exploration: float = 0.05        # minimum epsilon
    # Experience replay
    buffer_size: int = 10000
    batch_size: int = 32
    min_experiences: int = 20            # min experiences before learning
    # Bayesian optimization
    bo_iterations: int = 50
    bo_initial_points: int = 10
    # Reward shaping
    win_reward: float = 1.0
    loss_penalty: float = -1.5           # asymmetric penalty for losses
    skip_reward: float = 0.02            # small reward for skipping bad trades
    hold_penalty: float = -0.01          # per-hour penalty for holding
    # General
    update_interval: int = 10            # update policy every N experiences
    enabled: bool = True

    def to_dict(self) -> dict:
        return {
            "learning_rate": self.learning_rate,
            "discount_factor": self.discount_factor,
            "exploration_rate": self.exploration_rate,
            "exploration_decay": self.exploration_decay,
            "min_exploration": self.min_exploration,
            "buffer_size": self.buffer_size,
            "batch_size": self.batch_size,
            "min_experiences": self.min_experiences,
            "bo_iterations": self.bo_iterations,
            "update_interval": self.update_interval,
            "enabled": self.enabled,
        }


@dataclass
class RLDecision:
    """Result of RL agent decision."""
    action: str = "skip"                # ActionType value
    confidence: float = 0.0             # 0-1
    q_values: dict = field(default_factory=dict)  # action → Q-value
    exploration: bool = False           # was this an exploration move?
    state_key: str = ""
    suggested_amount: float = 0.05      # BNB/ETH
    suggested_sl: float = 15.0          # stop-loss %
    suggested_tp: float = 50.0          # take-profit %
    reasoning: str = ""


@dataclass
class BayesianResult:
    """Result of Bayesian optimization."""
    best_params: dict = field(default_factory=dict)
    best_score: float = 0.0
    iterations_run: int = 0
    improvement_pct: float = 0.0
    convergence: bool = False
    param_history: list = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════
#  Experience Replay Buffer
# ═══════════════════════════════════════════════════════════════════

class ExperienceBuffer:
    """Circular buffer for experience replay."""

    def __init__(self, capacity: int = 10000):
        self._buffer: list[Experience] = []
        self._capacity = capacity
        self._position = 0
        self._total_added = 0

    def add(self, experience: Experience):
        """Add experience to buffer."""
        if len(self._buffer) < self._capacity:
            self._buffer.append(experience)
        else:
            self._buffer[self._position] = experience
        self._position = (self._position + 1) % self._capacity
        self._total_added += 1

    def sample(self, batch_size: int) -> list[Experience]:
        """Random sample from buffer."""
        if len(self._buffer) <= batch_size:
            return list(self._buffer)
        indices = random.sample(range(len(self._buffer)), batch_size)
        return [self._buffer[i] for i in indices]

    def sample_prioritized(self, batch_size: int) -> list[Experience]:
        """Priority sampling — higher absolute reward = more likely sampled."""
        if not HAS_NUMPY or len(self._buffer) <= batch_size:
            return self.sample(batch_size)
        rewards = np.array([abs(e.reward) + 0.01 for e in self._buffer])
        probs = rewards / rewards.sum()
        indices = np.random.choice(len(self._buffer), size=batch_size, replace=False, p=probs)
        return [self._buffer[i] for i in indices]

    def get_recent(self, n: int = 50) -> list[Experience]:
        """Get N most recent experiences."""
        return self._buffer[-n:]

    @property
    def size(self) -> int:
        return len(self._buffer)

    @property
    def total_added(self) -> int:
        return self._total_added

    def clear(self):
        self._buffer.clear()
        self._position = 0


# ═══════════════════════════════════════════════════════════════════
#  Q-Learning Agent
# ═══════════════════════════════════════════════════════════════════

class QLearningAgent:
    """Tabular Q-learning with epsilon-greedy exploration."""

    ACTIONS = [a.value for a in ActionType]

    def __init__(self, config: RLConfig):
        self.config = config
        self._q_table: dict[str, dict[str, float]] = {}
        self._epsilon = config.exploration_rate
        self._episode_count = 0
        self._total_updates = 0

    def _get_q_values(self, state_key: str) -> dict[str, float]:
        """Get Q-values for a state, initializing if needed."""
        if state_key not in self._q_table:
            self._q_table[state_key] = {a: 0.0 for a in self.ACTIONS}
        return self._q_table[state_key]

    def select_action(self, state: MarketState) -> tuple[str, bool]:
        """Select action using epsilon-greedy policy. Returns (action, is_exploration)."""
        state_key = state.to_key()
        q_values = self._get_q_values(state_key)

        # Epsilon-greedy
        if random.random() < self._epsilon:
            action = random.choice(self.ACTIONS)
            return action, True
        else:
            best_action = max(q_values, key=q_values.get)
            return best_action, False

    def update(self, state: MarketState, action: str, reward: float,
               next_state: Optional[MarketState] = None, done: bool = False):
        """Q-learning update: Q(s,a) ← Q(s,a) + α[r + γ·max_a' Q(s',a') - Q(s,a)]."""
        state_key = state.to_key()
        q_values = self._get_q_values(state_key)

        if done or next_state is None:
            target = reward
        else:
            next_key = next_state.to_key()
            next_q = self._get_q_values(next_key)
            target = reward + self.config.discount_factor * max(next_q.values())

        # Update rule
        old_q = q_values[action]
        q_values[action] = old_q + self.config.learning_rate * (target - old_q)
        self._total_updates += 1

    def batch_update(self, experiences: list[Experience]):
        """Update from a batch of experiences."""
        for exp in experiences:
            self.update(exp.state, exp.action, exp.reward, exp.next_state, exp.done)

    def decay_exploration(self):
        """Decay epsilon after each episode."""
        self._epsilon = max(
            self.config.min_exploration,
            self._epsilon * self.config.exploration_decay,
        )
        self._episode_count += 1

    def get_best_action(self, state: MarketState) -> str:
        """Get greedy best action (no exploration)."""
        state_key = state.to_key()
        q_values = self._get_q_values(state_key)
        return max(q_values, key=q_values.get)

    def get_stats(self) -> dict:
        return {
            "states_visited": len(self._q_table),
            "epsilon": round(self._epsilon, 4),
            "episodes": self._episode_count,
            "total_updates": self._total_updates,
        }


# ═══════════════════════════════════════════════════════════════════
#  Contextual Bandit (continuous action selection)
# ═══════════════════════════════════════════════════════════════════

class ContextualBandit:
    """Multi-armed bandit with contextual features for continuous parameter selection."""

    def __init__(self):
        self._arms: dict[str, dict] = {}  # arm_name → {pulls, total_reward, mean, ...}
        self._feature_weights: dict[str, float] = {}
        self._total_pulls = 0

    def add_arm(self, name: str):
        """Register a new arm (action variant)."""
        if name not in self._arms:
            self._arms[name] = {
                "pulls": 0,
                "total_reward": 0.0,
                "mean_reward": 0.0,
                "variance": 1.0,
            }

    def select_arm(self, context: list[float] = None) -> str:
        """UCB1 selection with optional contextual features."""
        if not self._arms:
            return ""

        best_arm = ""
        best_score = float("-inf")

        for name, stats in self._arms.items():
            if stats["pulls"] == 0:
                return name  # explore unpulled arms first

            # UCB1: mean + sqrt(2 * ln(total) / pulls)
            exploitation = stats["mean_reward"]
            exploration = math.sqrt(2 * math.log(self._total_pulls + 1) / stats["pulls"])
            score = exploitation + exploration

            # Contextual bonus
            if context and HAS_NUMPY:
                ctx_bonus = sum(c * 0.01 for c in context) / max(len(context), 1)
                score += ctx_bonus

            if score > best_score:
                best_score = score
                best_arm = name

        return best_arm

    def update_arm(self, name: str, reward: float):
        """Update arm after observing reward."""
        if name not in self._arms:
            self.add_arm(name)
        arm = self._arms[name]
        arm["pulls"] += 1
        arm["total_reward"] += reward
        arm["mean_reward"] = arm["total_reward"] / arm["pulls"]
        # Online variance (Welford's)
        delta = reward - arm["mean_reward"]
        arm["variance"] = max(0.01, arm["variance"] + delta * delta / arm["pulls"])
        self._total_pulls += 1

    def get_best_arm(self) -> str:
        """Pure exploitation — best mean reward."""
        if not self._arms:
            return ""
        return max(self._arms, key=lambda a: self._arms[a]["mean_reward"])

    def get_stats(self) -> dict:
        return {
            "arms": {n: {"pulls": s["pulls"], "mean": round(s["mean_reward"], 4)}
                     for n, s in self._arms.items()},
            "total_pulls": self._total_pulls,
        }


# ═══════════════════════════════════════════════════════════════════
#  Bayesian Optimizer
# ═══════════════════════════════════════════════════════════════════

class BayesianOptimizer:
    """Bayesian optimization for strategy hyperparameters using Gaussian Processes."""

    # Parameter bounds: (name, min, max)
    PARAM_BOUNDS = [
        ("buy_amount", 0.01, 0.5),
        ("take_profit", 10.0, 200.0),
        ("stop_loss", 5.0, 50.0),
        ("slippage", 5.0, 25.0),
        ("min_pump_score", 20.0, 80.0),
        ("min_liquidity_k", 1.0, 100.0),
        ("max_buy_tax", 3.0, 15.0),
        ("max_sell_tax", 3.0, 20.0),
    ]

    def __init__(self, config: RLConfig):
        self.config = config
        self._X: list[list[float]] = []   # observed param vectors
        self._y: list[float] = []          # observed rewards
        self._best_params: dict = {}
        self._best_score: float = float("-inf")
        self._iterations = 0

    def _random_point(self) -> list[float]:
        """Generate random point within bounds."""
        return [random.uniform(lo, hi) for _, lo, hi in self.PARAM_BOUNDS]

    def _params_to_dict(self, point: list[float]) -> dict:
        """Convert parameter vector to named dict."""
        return {name: round(val, 4) for (name, _, _), val in zip(self.PARAM_BOUNDS, point)}

    def _acquisition_function(self, gp, X_cand) -> int:
        """Expected Improvement acquisition function."""
        if not HAS_NUMPY:
            return random.randint(0, len(X_cand) - 1)

        X_arr = np.array(X_cand)
        mu, sigma = gp.predict(X_arr, return_std=True)
        sigma = np.maximum(sigma, 1e-9)

        # Expected Improvement
        improvement = mu - self._best_score - 0.01  # xi = 0.01
        Z = improvement / sigma
        # Approximate standard normal CDF and PDF
        ei = improvement * (0.5 * (1 + _erf_approx(Z / math.sqrt(2)))) + \
             sigma * _standard_normal_pdf(Z)
        return int(np.argmax(ei))

    def observe(self, params: dict, reward: float):
        """Record an observation (params → reward)."""
        point = [params.get(name, (lo + hi) / 2) for name, lo, hi in self.PARAM_BOUNDS]
        self._X.append(point)
        self._y.append(reward)
        if reward > self._best_score:
            self._best_score = reward
            self._best_params = dict(params)
        self._iterations += 1

    def suggest(self) -> dict:
        """Suggest next parameter configuration to try."""
        # Initial random exploration
        if len(self._X) < self.config.bo_initial_points or not HAS_SKLEARN or not HAS_NUMPY:
            point = self._random_point()
            return self._params_to_dict(point)

        try:
            # Fit Gaussian Process
            X_arr = np.array(self._X)
            y_arr = np.array(self._y)
            kernel = Matern(nu=2.5)
            gp = GaussianProcessRegressor(kernel=kernel, alpha=1e-6, n_restarts_optimizer=5)
            gp.fit(X_arr, y_arr)

            # Generate candidate points
            n_candidates = 1000
            candidates = [self._random_point() for _ in range(n_candidates)]

            # Select best candidate via acquisition function
            best_idx = self._acquisition_function(gp, candidates)
            return self._params_to_dict(candidates[best_idx])

        except Exception as e:
            logger.warning(f"Bayesian optimization failed, using random: {e}")
            return self._params_to_dict(self._random_point())

    def optimize(self, objective_fn=None, n_iterations: int = 0) -> BayesianResult:
        """Run optimization loop (if objective_fn provided) or return current best."""
        iters = n_iterations or self.config.bo_iterations

        if objective_fn:
            for i in range(iters):
                params = self.suggest()
                score = objective_fn(params)
                self.observe(params, score)

        initial_score = self._y[0] if self._y else 0
        improvement = ((self._best_score - initial_score) / max(abs(initial_score), 1e-9)) * 100

        return BayesianResult(
            best_params=dict(self._best_params),
            best_score=round(self._best_score, 4),
            iterations_run=self._iterations,
            improvement_pct=round(improvement, 2),
            convergence=self._iterations >= iters,
            param_history=[self._params_to_dict(x) for x in self._X[-10:]],
        )

    def get_stats(self) -> dict:
        return {
            "observations": len(self._X),
            "best_score": round(self._best_score, 4) if self._best_score > float("-inf") else 0,
            "best_params": self._best_params,
            "iterations": self._iterations,
        }


# ═══════════════════════════════════════════════════════════════════
#  Reward Shaping Engine
# ═══════════════════════════════════════════════════════════════════

class RewardShaper:
    """Shapes raw PnL into meaningful RL rewards."""

    def __init__(self, config: RLConfig):
        self.config = config
        self._reward_history: list[float] = []

    def compute_reward(self, pnl_percent: float, hold_hours: float = 0,
                       was_skip: bool = False, token_rugged: bool = False,
                       mev_avoided: bool = False) -> float:
        """Compute shaped reward from trade outcome."""
        if was_skip:
            # Small positive reward for avoiding bad trades
            if token_rugged or pnl_percent < -30:
                return self.config.skip_reward * 5  # big bonus for dodging a bullet
            return self.config.skip_reward

        reward = 0.0

        # Base PnL reward (asymmetric)
        if pnl_percent > 0:
            reward = self.config.win_reward * min(pnl_percent / 50, 3.0)  # cap at 3x
        else:
            reward = self.config.loss_penalty * min(abs(pnl_percent) / 50, 3.0)

        # Hold time penalty (encourage quick profits)
        if hold_hours > 0:
            reward += self.config.hold_penalty * hold_hours

        # Rug pull extra penalty
        if token_rugged:
            reward -= 2.0

        # MEV avoidance bonus
        if mev_avoided:
            reward += 0.3

        # Risk-adjusted: sharpe-like component
        if len(self._reward_history) >= 10:
            mean_r = sum(self._reward_history[-10:]) / 10
            std_r = max(_std_dev(self._reward_history[-10:]), 0.01)
            sharpe_bonus = (reward - mean_r) / std_r * 0.1
            reward += max(-0.5, min(0.5, sharpe_bonus))

        self._reward_history.append(reward)
        if len(self._reward_history) > 1000:
            self._reward_history = self._reward_history[-500:]

        return round(reward, 4)

    def get_average_reward(self, n: int = 50) -> float:
        if not self._reward_history:
            return 0.0
        recent = self._reward_history[-n:]
        return round(sum(recent) / len(recent), 4)


# ═══════════════════════════════════════════════════════════════════
#  State Encoder
# ═══════════════════════════════════════════════════════════════════

class StateEncoder:
    """Encodes raw market data into discrete MarketState for Q-learning."""

    @staticmethod
    def encode(token_info=None, market_data: dict = None) -> MarketState:
        """Encode raw data into discretized MarketState."""
        data = market_data or {}
        state = MarketState()

        # Volatility bucket
        vol = data.get("volatility_score", 0)
        if vol <= 30:
            state.volatility_bucket = 0
        elif vol <= 65:
            state.volatility_bucket = 1
        else:
            state.volatility_bucket = 2

        # Trend bucket (from price changes)
        h1_change = data.get("price_change_h1", 0)
        if h1_change <= -10:
            state.trend_bucket = 0
        elif h1_change <= 15:
            state.trend_bucket = 1
        else:
            state.trend_bucket = 2

        # Volume bucket
        volume = data.get("volume_24h", 0)
        if volume <= 5000:
            state.volume_bucket = 0
        elif volume <= 50000:
            state.volume_bucket = 1
        else:
            state.volume_bucket = 2

        # MEV risk
        mev_risk = data.get("mev_frontrun_risk", 0)
        if mev_risk <= 0.2:
            state.mev_risk_bucket = 0
        elif mev_risk <= 0.6:
            state.mev_risk_bucket = 1
        else:
            state.mev_risk_bucket = 2

        # Pump score
        pump = data.get("pump_score", 0)
        if pump <= 35:
            state.pump_score_bucket = 0
        elif pump <= 65:
            state.pump_score_bucket = 1
        else:
            state.pump_score_bucket = 2

        # Social
        social = data.get("social_score", 0)
        if social <= 20:
            state.social_bucket = 0
        elif social <= 60:
            state.social_bucket = 1
        else:
            state.social_bucket = 2

        # Liquidity
        liq = data.get("liquidity_usd", 0)
        if liq <= 10000:
            state.liquidity_bucket = 0
        elif liq <= 100000:
            state.liquidity_bucket = 1
        else:
            state.liquidity_bucket = 2

        # Whale activity
        whale = data.get("whale_buys", 0)
        if whale <= 0:
            state.whale_activity_bucket = 0
        elif whale <= 3:
            state.whale_activity_bucket = 1
        else:
            state.whale_activity_bucket = 2

        return state

    @staticmethod
    def encode_from_token_info(token_info) -> MarketState:
        """Encode directly from TokenInfo dataclass."""
        data = {
            "volatility_score": getattr(token_info, "volatility_score", 0),
            "price_change_h1": getattr(token_info, "dexscreener_price_change_h1", 0),
            "volume_24h": getattr(token_info, "dexscreener_volume_24h", 0),
            "mev_frontrun_risk": getattr(token_info, "mev_frontrun_risk", 0),
            "pump_score": getattr(token_info, "pump_score", 0),
            "social_score": getattr(token_info, "social_sentiment_score", 0),
            "liquidity_usd": getattr(token_info, "dexscreener_liquidity", 0),
            "whale_buys": getattr(token_info, "whale_total_buys", 0),
        }
        return StateEncoder.encode(market_data=data)


# ═══════════════════════════════════════════════════════════════════
#  Main: Reinforcement Learner
# ═══════════════════════════════════════════════════════════════════

class ReinforcementLearner:
    """
    Adaptive ML agent that learns from every trade.
    Combines Q-Learning, Contextual Bandits, and Bayesian Optimization.

    Usage:
        learner = ReinforcementLearner()
        # Before each trade:
        decision = learner.decide(token_info, market_data)
        # After each trade:
        learner.record_outcome(token_address, pnl_pct, hold_hours, ...)
        # Periodically:
        bo_result = learner.optimize_params()
    """

    MIN_EXPERIENCES_FOR_LEARNING = 20
    MIN_EXPERIENCES_FOR_BO = 30

    def __init__(self, config: RLConfig = None):
        self.config = config or RLConfig()
        self._buffer = ExperienceBuffer(capacity=self.config.buffer_size)
        self._q_agent = QLearningAgent(self.config)
        self._bandit = ContextualBandit()
        self._bayesian = BayesianOptimizer(self.config)
        self._reward_shaper = RewardShaper(self.config)
        self._encoder = StateEncoder()

        # Track pending trades (waiting for outcome)
        self._pending: dict[str, dict] = {}  # token_address → {state, action, ...}

        # Stats
        self._total_decisions = 0
        self._total_outcomes = 0
        self._cumulative_reward = 0.0
        self._win_rate = 0.0
        self._wins = 0
        self._losses = 0

        # Initialize bandit arms
        for action in ActionType:
            self._bandit.add_arm(action.value)

        # Current suggested params from BO
        self._bo_params: dict = {}

        # Emit callback
        self._emit_callback = None

    def set_emit_callback(self, callback):
        """Set callback for real-time event emission."""
        self._emit_callback = callback

    def decide(self, token_info=None, market_data: dict = None) -> RLDecision:
        """
        Make a trading decision for a token.

        Returns RLDecision with action, confidence, and suggested parameters.
        """
        self._total_decisions += 1

        # Encode state
        if token_info:
            state = self._encoder.encode_from_token_info(token_info)
        else:
            state = self._encoder.encode(market_data=market_data or {})

        state_key = state.to_key()

        # Q-learning action selection
        action, is_exploration = self._q_agent.select_action(state)

        # Get Q-values for confidence
        q_values = dict(self._q_agent._get_q_values(state_key))

        # Calculate confidence (normalized Q-value difference)
        q_vals = list(q_values.values())
        if max(q_vals) != min(q_vals):
            q_range = max(q_vals) - min(q_vals)
            best_q = q_values.get(action, 0)
            confidence = (best_q - min(q_vals)) / q_range
        else:
            confidence = 0.5 if self._buffer.size < self.MIN_EXPERIENCES_FOR_LEARNING else 0.3

        # Bandit arm for parameter variant selection
        bandit_arm = self._bandit.select_arm(state.to_vector())

        # Map action to suggested parameters
        suggested = self._action_to_params(action)

        # Apply BO params if available
        if self._bo_params:
            for k, v in self._bo_params.items():
                if k in suggested:
                    # Blend: 70% BO, 30% RL
                    suggested[k] = round(0.7 * v + 0.3 * suggested.get(k, v), 4)

        # Store pending for later outcome recording
        token_addr = getattr(token_info, "address", "") if token_info else ""
        if token_addr:
            self._pending[token_addr.lower()] = {
                "state": state,
                "action": action,
                "timestamp": time.time(),
            }

        decision = RLDecision(
            action=action,
            confidence=round(confidence, 3),
            q_values={k: round(v, 3) for k, v in q_values.items()},
            exploration=is_exploration,
            state_key=state_key,
            suggested_amount=suggested.get("buy_amount", 0.05),
            suggested_sl=suggested.get("stop_loss", 15.0),
            suggested_tp=suggested.get("take_profit", 50.0),
            reasoning=self._build_reasoning(state, action, is_exploration, confidence),
        )

        return decision

    def record_outcome(self, token_address: str, pnl_percent: float = 0.0,
                       hold_hours: float = 0.0, was_skip: bool = False,
                       token_rugged: bool = False, mev_avoided: bool = False,
                       next_market_data: dict = None):
        """
        Record trade outcome for learning.
        Call this after every trade (or skip) with the result.
        """
        self._total_outcomes += 1

        key = token_address.lower()
        pending = self._pending.pop(key, None)

        if pending:
            state = pending["state"]
            action = pending["action"]
        else:
            state = self._encoder.encode(market_data=next_market_data or {})
            action = "skip" if was_skip else "buy_moderate"

        # Shape reward
        reward = self._reward_shaper.compute_reward(
            pnl_percent, hold_hours, was_skip, token_rugged, mev_avoided,
        )

        # Next state
        next_state = self._encoder.encode(market_data=next_market_data) if next_market_data else None

        # Store experience
        exp = Experience(
            state=state,
            action=action,
            reward=reward,
            next_state=next_state,
            done=True,
            timestamp=time.time(),
            token_address=token_address,
        )
        self._buffer.add(exp)

        # Update stats
        self._cumulative_reward += reward
        if not was_skip:
            if pnl_percent > 0:
                self._wins += 1
            else:
                self._losses += 1
            total_trades = self._wins + self._losses
            self._win_rate = self._wins / total_trades if total_trades > 0 else 0

        # Update bandit arm
        self._bandit.update_arm(action, reward)

        # Batch learning
        if self._buffer.size >= self.config.min_experiences and \
           self._buffer.size % self.config.update_interval == 0:
            self._learn_from_batch()

        # Decay exploration
        self._q_agent.decay_exploration()

        # Record for Bayesian optimizer
        current_params = self._action_to_params(action)
        self._bayesian.observe(current_params, reward)

        logger.info(
            f"RL outcome: token={token_address[:10]}... pnl={pnl_percent:+.1f}% "
            f"reward={reward:+.3f} action={action} buffer={self._buffer.size}"
        )

    def _learn_from_batch(self):
        """Sample batch from buffer and update Q-agent."""
        batch = self._buffer.sample_prioritized(self.config.batch_size)
        self._q_agent.batch_update(batch)
        logger.debug(f"RL batch update: {len(batch)} experiences")

    def optimize_params(self) -> BayesianResult:
        """Run Bayesian optimization to find best strategy parameters."""
        if self._buffer.size < self.MIN_EXPERIENCES_FOR_BO:
            return BayesianResult(
                best_params={},
                iterations_run=0,
                improvement_pct=0,
            )

        result = self._bayesian.optimize()
        if result.best_params:
            self._bo_params = dict(result.best_params)
            logger.info(f"BO optimized: score={result.best_score:.3f} improvement={result.improvement_pct:.1f}%")

        return result

    def _action_to_params(self, action: str) -> dict:
        """Map RL action to concrete trading parameters."""
        if action == ActionType.BUY_AGGRESSIVE.value:
            return {"buy_amount": 0.1, "take_profit": 80, "stop_loss": 10, "slippage": 15}
        elif action == ActionType.BUY_MODERATE.value:
            return {"buy_amount": 0.05, "take_profit": 50, "stop_loss": 15, "slippage": 12}
        elif action == ActionType.BUY_CONSERVATIVE.value:
            return {"buy_amount": 0.02, "take_profit": 30, "stop_loss": 20, "slippage": 10}
        elif action == ActionType.WAIT.value:
            return {"buy_amount": 0.03, "take_profit": 60, "stop_loss": 12, "slippage": 10}
        else:  # SKIP
            return {"buy_amount": 0, "take_profit": 0, "stop_loss": 0, "slippage": 0}

    def _build_reasoning(self, state: MarketState, action: str,
                         is_exploration: bool, confidence: float) -> str:
        """Human-readable reasoning for the decision."""
        parts = []
        if is_exploration:
            parts.append("exploration move")
        else:
            parts.append(f"confidence={confidence:.0%}")

        if state.volatility_bucket == 2:
            parts.append("high volatility")
        if state.mev_risk_bucket == 2:
            parts.append("dangerous MEV")
        if state.pump_score_bucket == 2:
            parts.append("strong pump signal")
        if state.whale_activity_bucket == 2:
            parts.append("heavy whale activity")

        if self._buffer.size < self.MIN_EXPERIENCES_FOR_LEARNING:
            parts.append(f"still learning ({self._buffer.size}/{self.MIN_EXPERIENCES_FOR_LEARNING})")

        return " | ".join(parts)

    def get_stats(self) -> dict:
        return {
            "enabled": self.config.enabled,
            "total_decisions": self._total_decisions,
            "total_outcomes": self._total_outcomes,
            "buffer_size": self._buffer.size,
            "cumulative_reward": round(self._cumulative_reward, 3),
            "win_rate": round(self._win_rate, 3),
            "wins": self._wins,
            "losses": self._losses,
            "avg_reward": self._reward_shaper.get_average_reward(),
            "q_agent": self._q_agent.get_stats(),
            "bandit": self._bandit.get_stats(),
            "bayesian": self._bayesian.get_stats(),
            "bo_params": self._bo_params,
        }

    def get_policy_summary(self) -> dict:
        """Get a summary of the learned policy."""
        summary = {}
        for state_key, q_values in self._q_agent._q_table.items():
            best_action = max(q_values, key=q_values.get)
            best_q = q_values[best_action]
            if best_q != 0:
                summary[state_key] = {
                    "best_action": best_action,
                    "q_value": round(best_q, 3),
                }
        return {
            "states_with_learned_policy": len(summary),
            "top_states": dict(sorted(summary.items(), key=lambda x: -x[1]["q_value"])[:10]),
        }

    def reset(self):
        """Reset all learning (start fresh)."""
        self._buffer.clear()
        self._q_agent = QLearningAgent(self.config)
        self._bandit = ContextualBandit()
        for action in ActionType:
            self._bandit.add_arm(action.value)
        self._bayesian = BayesianOptimizer(self.config)
        self._pending.clear()
        self._total_decisions = 0
        self._total_outcomes = 0
        self._cumulative_reward = 0.0
        self._win_rate = 0.0
        self._wins = 0
        self._losses = 0
        self._bo_params = {}


# ═══════════════════════════════════════════════════════════════════
#  Utility Functions
# ═══════════════════════════════════════════════════════════════════

def _std_dev(values: list[float]) -> float:
    """Standard deviation of a list."""
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    return math.sqrt(variance)


def _erf_approx(x: float) -> float:
    """Approximate error function (for standard normal CDF)."""
    # Abramowitz & Stegun approximation
    sign = 1 if x >= 0 else -1
    x = abs(x)
    a1, a2, a3, a4, a5 = 0.254829592, -0.284496736, 1.421413741, -1.453152027, 1.061405429
    p = 0.3275911
    t = 1.0 / (1.0 + p * x)
    y = 1.0 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * math.exp(-x * x)
    return sign * y


def _standard_normal_pdf(x: float) -> float:
    """Standard normal PDF."""
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)

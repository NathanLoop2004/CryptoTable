"""
mlPredictor.py — ML-Based Prediction Models (v5).

Provides statistical/ML scoring without heavy ML library dependencies.
Uses feature engineering + weighted logistic-like scoring trained from
observed token patterns.

Models:
  1. PumpDumpPredictor:  Predicts probability of pump vs dump based
     on multi-dimensional token features. Outputs 0-100 score.
  2. DevReputationML:    ML-enhanced developer reputation scoring
     that considers cross-wallet patterns, funding sources, and
     historical behavior clusters.
  3. AnomalyDetector:    Detects unusual activity patterns that
     deviate from normal token behavior.

All models use memory-resident feature stores (no disk persistence).
Weights are pre-tuned from empirical BSC/ETH token data and adapt
via online learning as the bot observes outcomes.

Integration:
  Called by SniperBot._process_pair() after basic analysis.
  Results fed into RiskEngine as additional scoring components.
"""

import logging
import math
import time
from dataclasses import dataclass, field, asdict
from typing import Optional
from collections import deque

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
#  Data structures
# ═══════════════════════════════════════════════════════════════════

@dataclass
class PumpDumpPrediction:
    """Prediction result for pump/dump probability."""
    token_address: str
    pump_probability: float = 0.5      # 0.0–1.0
    dump_probability: float = 0.5      # 0.0–1.0
    ml_score: int = 50                 # 0–100 (100 = very likely pump)
    confidence: float = 0.0            # 0.0–1.0
    feature_importances: dict = field(default_factory=dict)
    signals: list = field(default_factory=list)
    model_version: str = "v5.0"
    timestamp: float = 0.0

    def to_dict(self):
        return asdict(self)


@dataclass
class DevReputationPrediction:
    """ML-enhanced developer reputation prediction."""
    wallet_address: str
    ml_reputation_score: int = 50      # 0–100
    cluster: str = "unknown"           # "legit_dev" | "neutral" | "suspicious" | "scammer"
    funding_risk: str = "unknown"      # "clean" | "mixed" | "suspicious" | "unknown"
    cross_wallet_risk: float = 0.0     # 0.0–1.0 linked to known scammer wallets
    pattern_score: int = 50            # 0–100 behavioral pattern score
    signals: list = field(default_factory=list)
    timestamp: float = 0.0

    def to_dict(self):
        return asdict(self)


@dataclass
class AnomalyResult:
    """Anomaly detection result for a token."""
    token_address: str
    is_anomalous: bool = False
    anomaly_score: float = 0.0         # 0.0–1.0 (1.0 = extreme anomaly)
    anomaly_type: str = ""             # "volume_spike" | "holder_explosion" | "price_pattern" | ""
    signals: list = field(default_factory=list)
    timestamp: float = 0.0

    def to_dict(self):
        return asdict(self)


# ═══════════════════════════════════════════════════════════════════
#  Feature Engineering
# ═══════════════════════════════════════════════════════════════════

# Feature weights learned from empirical BSC/ETH token data.
# Positive = bullish signal, Negative = bearish signal.
# Magnitude = importance.
PUMP_FEATURES = {
    # Security features (strong weight)
    "is_honeypot":           -50.0,
    "has_blacklist":         -8.0,
    "is_mintable":           -6.0,
    "can_pause_trading":     -5.0,
    "is_proxy":              -10.0,
    "has_hidden_owner":      -8.0,
    "is_open_source":        +10.0,
    "is_true_token":         +8.0,
    "can_self_destruct":     -15.0,
    "owner_can_change_bal":  -12.0,
    # Liquidity features
    "lp_locked":             +12.0,
    "lp_lock_pct_high":      +8.0,    # lp_lock_percent >= 90
    "lp_lock_long":          +6.0,    # lp_lock_hours > 168
    "liquidity_good":        +5.0,    # > $10k
    "liquidity_great":       +8.0,    # > $50k
    # Market features
    "has_coingecko":         +15.0,
    "has_website":           +4.0,
    "has_social":            +3.0,
    "dex_volume_good":       +5.0,    # >$5k volume 24h
    "dex_age_young":         +3.0,    # < 2h age (early entry)
    "dex_age_very_young":    +5.0,    # < 30min
    # Tax features
    "low_tax":               +8.0,    # buy+sell < 10%
    "high_tax":              -10.0,   # buy+sell > 20%
    # Holder features
    "holders_growing":       +6.0,
    "whale_concentration":   -7.0,    # top holder > 20%
    "creator_low":           +4.0,    # creator < 5%
    "creator_high":          -8.0,    # creator > 15%
    # Smart money
    "smart_money_buying":    +12.0,
    # Dev track record
    "dev_good":              +10.0,   # dev_score >= 70
    "dev_bad":               -15.0,   # dev_score < 30
    "dev_scammer":           -40.0,   # serial scammer
    # Simulation
    "sim_ok":                +6.0,
    "sim_honeypot":          -50.0,
    # Bytecode
    "bytecode_clean":        +5.0,
    "bytecode_dangerous":    -12.0,
    # Pump momentum
    "pump_grade_high":       +10.0,
    "pump_grade_avoid":      -10.0,
    # Price momentum
    "price_up_m5":           +3.0,    # 5-15% up in 5min (healthy)
    "price_rocket_m5":       -5.0,    # >30% in 5min (already pumped)
    "price_dump_h1":         -8.0,    # >25% down in 1h
}

# Bias term (baseline probability)
PUMP_BIAS = 0.0

# DEV REPUTATION feature weights
DEV_FEATURES = {
    "success_rate_high":     +20.0,   # success_rate >= 0.7
    "success_rate_mid":      +10.0,   # 0.3–0.7
    "rug_rate_high":         -30.0,   # rug_rate >= 0.3
    "has_rugs":              -15.0,   # any rug pulls
    "verified_contracts":    +10.0,   # >80% verified
    "lp_locked_pattern":     +12.0,   # consistently locks LP
    "high_roi_history":      +8.0,    # best multiplier >= 5x
    "many_launches":         +5.0,    # 5+ launches
    "recent_activity":       +4.0,    # active within 72h
    "known_good":            +20.0,   # manually labeled good
    "known_scam":            -40.0,   # manually labeled scam
}

DEV_BIAS = 0.0


def _sigmoid(x: float) -> float:
    """Numerically stable sigmoid function."""
    if x >= 0:
        z = math.exp(-x)
        return 1 / (1 + z)
    else:
        z = math.exp(x)
        return z / (1 + z)


# ═══════════════════════════════════════════════════════════════════
#  Pump/Dump Predictor
# ═══════════════════════════════════════════════════════════════════

class PumpDumpPredictor:
    """
    Predicts pump vs dump probability using feature engineering.

    Process:
      1. Extract binary/numeric features from token_info
      2. Compute weighted sum → logistic score
      3. Apply sigmoid for probability
      4. Adapt weights via online learning from outcomes

    Not a real neural network — uses a pre-tuned feature-weight model
    that's fast, interpretable, and doesn't require sklearn/torch.
    """

    def __init__(self):
        self.weights = PUMP_FEATURES.copy()
        self.bias = PUMP_BIAS
        self._learning_rate = 0.01
        self._outcomes: deque = deque(maxlen=1000)  # (features_dict, actual_outcome)
        self.stats = {
            "predictions": 0,
            "outcomes_recorded": 0,
            "accuracy": 0.0,
        }

    def predict(self, token_info, liquidity_usd: float = 0,
                smart_signals: list = None) -> PumpDumpPrediction:
        """
        Predict pump/dump probability from token features.

        Args:
            token_info: TokenInfo dataclass from sniperService
            liquidity_usd: Liquidity in USD
            smart_signals: Smart money signals if any

        Returns:
            PumpDumpPrediction with score 0-100
        """
        result = PumpDumpPrediction(
            token_address=token_info.address,
            timestamp=time.time(),
        )

        # Extract features
        features = self._extract_features(token_info, liquidity_usd, smart_signals)

        # Compute weighted sum
        z = self.bias
        importances = {}
        for feat_name, feat_val in features.items():
            if feat_name in self.weights and feat_val:
                contribution = self.weights[feat_name]
                z += contribution
                importances[feat_name] = round(contribution, 2)

        # Sigmoid → probability
        pump_prob = _sigmoid(z / 20.0)  # scale factor to keep probabilities reasonable

        result.pump_probability = round(pump_prob, 4)
        result.dump_probability = round(1 - pump_prob, 4)
        result.ml_score = max(0, min(100, round(pump_prob * 100)))
        result.feature_importances = importances

        # Confidence based on number of available features
        available = sum(1 for v in features.values() if v)
        total = len(PUMP_FEATURES)
        result.confidence = round(min(1.0, available / max(1, total * 0.5)), 2)

        # Signals
        top_positive = sorted(
            [(k, v) for k, v in importances.items() if v > 0],
            key=lambda x: x[1], reverse=True
        )[:3]
        top_negative = sorted(
            [(k, v) for k, v in importances.items() if v < 0],
            key=lambda x: x[1]
        )[:3]

        for name, val in top_positive:
            result.signals.append(f"✅ {name}: +{val}")
        for name, val in top_negative:
            result.signals.append(f"⚠️ {name}: {val}")

        if result.ml_score >= 70:
            result.signals.append(f"🚀 ML predice PUMP ({result.ml_score}/100)")
        elif result.ml_score <= 30:
            result.signals.append(f"📉 ML predice DUMP ({result.ml_score}/100)")

        self.stats["predictions"] += 1

        return result

    def record_outcome(self, token_address: str, features: dict,
                       was_pump: bool):
        """
        Record actual outcome for online learning.
        Call when a token's pump/dump result is known.
        """
        self._outcomes.append((features, 1.0 if was_pump else 0.0))
        self.stats["outcomes_recorded"] += 1

        # Mini-batch online update every 10 outcomes
        if len(self._outcomes) >= 10 and self.stats["outcomes_recorded"] % 10 == 0:
            self._update_weights()

    def _extract_features(self, info, liquidity_usd: float,
                          smart_signals: list = None) -> dict:
        """Extract binary features from TokenInfo."""
        feats = {
            # Security
            "is_honeypot": info.is_honeypot,
            "has_blacklist": info.has_blacklist,
            "is_mintable": info.is_mintable,
            "can_pause_trading": info.can_pause_trading,
            "is_proxy": info.is_proxy,
            "has_hidden_owner": info.has_hidden_owner,
            "is_open_source": info.is_open_source,
            "is_true_token": info.is_true_token,
            "can_self_destruct": info.can_self_destruct,
            "owner_can_change_bal": info.owner_can_change_balance,
            # LP
            "lp_locked": info.lp_locked,
            "lp_lock_pct_high": info.lp_lock_percent >= 90,
            "lp_lock_long": info.lp_lock_hours_remaining > 168,
            "liquidity_good": liquidity_usd > 10000,
            "liquidity_great": liquidity_usd > 50000,
            # Market
            "has_coingecko": info.listed_coingecko,
            "has_website": info.has_website,
            "has_social": info.has_social_links,
            "dex_volume_good": info.dexscreener_volume_24h > 5000 if info._dexscreener_ok else False,
            "dex_age_young": 0 < info.dexscreener_age_hours < 2 if info._dexscreener_ok else False,
            "dex_age_very_young": 0 < info.dexscreener_age_hours < 0.5 if info._dexscreener_ok else False,
            # Tax
            "low_tax": (info.buy_tax + info.sell_tax) < 10,
            "high_tax": (info.buy_tax + info.sell_tax) > 20,
            # Holders
            "holders_growing": info.pump_holder_growth_rate > 0 if hasattr(info, 'pump_holder_growth_rate') else False,
            "whale_concentration": info.top_holder_percent > 20,
            "creator_low": 0 < info.creator_percent < 5,
            "creator_high": info.creator_percent > 15,
            # Smart money
            "smart_money_buying": bool(smart_signals),
            # Dev
            "dev_good": info.dev_score >= 70 if info._dev_ok else False,
            "dev_bad": info.dev_score < 30 if info._dev_ok else False,
            "dev_scammer": info.dev_is_serial_scammer if info._dev_ok else False,
            # Simulation
            "sim_ok": info.sim_can_buy and info.sim_can_sell if info._simulation_ok else False,
            "sim_honeypot": info.sim_is_honeypot if info._simulation_ok else False,
            # Bytecode
            "bytecode_clean": info._bytecode_ok and not info.bytecode_flags,
            "bytecode_dangerous": info.bytecode_has_selfdestruct or info.bytecode_has_delegatecall,
            # Pump
            "pump_grade_high": info.pump_grade == "HIGH",
            "pump_grade_avoid": info.pump_grade == "AVOID",
            # Price
            "price_up_m5": 5 < info.dexscreener_price_change_m5 < 15 if info._dexscreener_ok else False,
            "price_rocket_m5": info.dexscreener_price_change_m5 > 30 if info._dexscreener_ok else False,
            "price_dump_h1": info.dexscreener_price_change_h1 < -25 if info._dexscreener_ok else False,
        }
        return feats

    def _update_weights(self):
        """Online gradient descent update from recorded outcomes."""
        if len(self._outcomes) < 5:
            return

        correct = 0
        total = 0

        for features, actual in list(self._outcomes)[-50:]:
            z = self.bias
            for feat, val in features.items():
                if feat in self.weights and val:
                    z += self.weights[feat]

            predicted = _sigmoid(z / 20.0)
            error = actual - predicted

            # Update weights (gradient step)
            for feat, val in features.items():
                if feat in self.weights and val:
                    self.weights[feat] += self._learning_rate * error * 1.0

            self.bias += self._learning_rate * error

            # Track accuracy
            pred_class = 1 if predicted >= 0.5 else 0
            if pred_class == int(actual):
                correct += 1
            total += 1

        if total > 0:
            self.stats["accuracy"] = round(correct / total, 3)

    def get_stats(self) -> dict:
        return self.stats.copy()


# ═══════════════════════════════════════════════════════════════════
#  Dev Reputation ML
# ═══════════════════════════════════════════════════════════════════

class DevReputationML:
    """
    ML-enhanced developer reputation scorer.

    Analyzes:
      - Historical success/rug patterns (feature-weighted)
      - Cross-wallet linkage risk (shared funding sources)
      - Behavioral clustering (legit_dev / neutral / suspicious / scammer)
    """

    def __init__(self):
        self.weights = DEV_FEATURES.copy()
        self.bias = DEV_BIAS
        # Known wallet clusters (populated from observed patterns)
        self._wallet_clusters: dict[str, str] = {}   # addr → cluster
        self._funding_graph: dict[str, set] = {}      # addr → set of connected addrs
        self.stats = {
            "predictions": 0,
            "wallets_clustered": 0,
        }

    def predict(self, dev_profile, dev_check_result=None) -> DevReputationPrediction:
        """
        Score a developer's reputation with ML features.

        Args:
            dev_profile: DevProfile from devTracker
            dev_check_result: DevCheckResult with basic check data

        Returns:
            DevReputationPrediction with score and cluster
        """
        wallet = dev_profile.wallet if hasattr(dev_profile, 'wallet') else ""
        result = DevReputationPrediction(
            wallet_address=wallet,
            timestamp=time.time(),
        )

        features = self._extract_dev_features(dev_profile)
        z = self.bias

        for feat, val in features.items():
            if feat in self.weights and val:
                z += self.weights[feat]

        reputation_prob = _sigmoid(z / 15.0)

        result.ml_reputation_score = max(0, min(100, round(reputation_prob * 100)))
        result.pattern_score = result.ml_reputation_score

        # Cluster assignment
        if result.ml_reputation_score >= 75:
            result.cluster = "legit_dev"
            result.signals.append("✅ Clasificado como developer legítimo")
        elif result.ml_reputation_score >= 50:
            result.cluster = "neutral"
            result.signals.append("ℹ️ Clasificado como neutral")
        elif result.ml_reputation_score >= 30:
            result.cluster = "suspicious"
            result.signals.append("⚠️ Clasificado como sospechoso")
        else:
            result.cluster = "scammer"
            result.signals.append("🚨 Clasificado como probable scammer")

        # Cross-wallet risk
        wallet_lower = wallet.lower()
        if wallet_lower in self._funding_graph:
            connected = self._funding_graph[wallet_lower]
            scammer_links = sum(
                1 for c in connected
                if self._wallet_clusters.get(c, "") == "scammer"
            )
            if scammer_links > 0:
                risk = min(1.0, scammer_links * 0.3)
                result.cross_wallet_risk = round(risk, 2)
                result.funding_risk = "suspicious"
                result.signals.append(
                    f"⚠️ Conectado a {scammer_links} wallet(s) scammer"
                )
                result.ml_reputation_score = max(0, result.ml_reputation_score - int(risk * 30))
            else:
                result.funding_risk = "clean"

        # Save cluster for cross-referencing
        self._wallet_clusters[wallet_lower] = result.cluster
        self.stats["predictions"] += 1
        self.stats["wallets_clustered"] = len(self._wallet_clusters)

        return result

    def link_wallets(self, wallet_a: str, wallet_b: str, evidence: str = ""):
        """Record a funding link between two wallets."""
        a, b = wallet_a.lower(), wallet_b.lower()
        self._funding_graph.setdefault(a, set()).add(b)
        self._funding_graph.setdefault(b, set()).add(a)

    def _extract_dev_features(self, profile) -> dict:
        """Extract features from DevProfile."""
        launches = getattr(profile, 'tokens_launched', 0)
        success = getattr(profile, 'successful_launches', 0)
        rugs = getattr(profile, 'rug_pulls', 0)
        best_mult = getattr(profile, 'best_multiplier', 0.0)
        source = getattr(profile, 'source', 'auto')
        last_launch = getattr(profile, 'last_launch_time', 0)
        launch_list = getattr(profile, 'launches', [])

        success_rate = success / launches if launches > 0 else 0
        rug_rate = rugs / launches if launches > 0 else 0

        verified_count = sum(1 for l in launch_list if getattr(l, 'is_verified', False))
        locked_count = sum(1 for l in launch_list if getattr(l, 'lp_locked', False))

        verify_rate = verified_count / len(launch_list) if launch_list else 0
        lock_rate = locked_count / len(launch_list) if launch_list else 0

        hours_since = (time.time() - last_launch) / 3600 if last_launch > 0 else 9999

        return {
            "success_rate_high": success_rate >= 0.7,
            "success_rate_mid": 0.3 <= success_rate < 0.7,
            "rug_rate_high": rug_rate >= 0.3,
            "has_rugs": rugs > 0,
            "verified_contracts": verify_rate >= 0.8,
            "lp_locked_pattern": lock_rate >= 0.7,
            "high_roi_history": best_mult >= 5.0,
            "many_launches": launches >= 5,
            "recent_activity": hours_since < 72,
            "known_good": source == "known" and success > 0,
            "known_scam": source == "known" and rugs >= 3,
        }

    def get_stats(self) -> dict:
        return self.stats.copy()


# ═══════════════════════════════════════════════════════════════════
#  Anomaly Detector
# ═══════════════════════════════════════════════════════════════════

class AnomalyDetector:
    """
    Detects unusual activity patterns for a token.

    Monitors:
      - Volume spikes relative to market cap
      - Holder explosion (too many holders too fast → airdrop scam)
      - Unusual DEX buy/sell ratio
    """

    def __init__(self):
        self._baselines: dict[str, dict] = {}  # token → baseline metrics

    def detect(self, token_info, liquidity_usd: float = 0) -> AnomalyResult:
        """Detect anomalies in token metrics."""
        result = AnomalyResult(
            token_address=token_info.address,
            timestamp=time.time(),
        )

        anomalies = 0
        total_score = 0.0

        # ── Volume spike detection ──
        if token_info._dexscreener_ok and liquidity_usd > 0:
            vol = token_info.dexscreener_volume_24h
            vol_to_liq_ratio = vol / liquidity_usd if liquidity_usd > 0 else 0

            if vol_to_liq_ratio > 10:
                anomalies += 1
                total_score += 0.4
                result.signals.append(
                    f"🔥 Volumen anómalo: ${vol:,.0f} ({vol_to_liq_ratio:.1f}x liquidez)"
                )
                result.anomaly_type = "volume_spike"
            elif vol_to_liq_ratio > 5:
                total_score += 0.2
                result.signals.append(
                    f"⚠️ Volumen alto: ${vol:,.0f} ({vol_to_liq_ratio:.1f}x liquidez)"
                )

        # ── Holder explosion detection ──
        if token_info._dexscreener_ok:
            age_hours = token_info.dexscreener_age_hours
            holders = token_info.holder_count

            if age_hours > 0 and holders > 0:
                holders_per_hour = holders / max(0.1, age_hours)
                if holders_per_hour > 100 and age_hours < 2:
                    anomalies += 1
                    total_score += 0.3
                    result.signals.append(
                        f"🚨 Holder explosion: {holders} holders en {age_hours:.1f}h "
                        f"({holders_per_hour:.0f}/h) — posible airdrop"
                    )
                    result.anomaly_type = result.anomaly_type or "holder_explosion"

        # ── Buy/sell ratio anomaly ──
        if token_info._dexscreener_ok:
            buys = token_info.dexscreener_buys_24h
            sells = token_info.dexscreener_sells_24h
            if buys + sells > 10:
                ratio = buys / max(1, sells)
                if ratio > 10:
                    anomalies += 1
                    total_score += 0.2
                    result.signals.append(
                        f"⚠️ Ratio buy/sell anómalo: {ratio:.1f} ({buys} buys vs {sells} sells)"
                    )
                    result.anomaly_type = result.anomaly_type or "buy_sell_ratio_anomaly"
                elif ratio < 0.1 and buys + sells > 20:
                    anomalies += 1
                    total_score += 0.3
                    result.signals.append(
                        f"🚨 Sell masivo: ratio {ratio:.2f} ({sells} sells vs {buys} buys)"
                    )
                    result.anomaly_type = result.anomaly_type or "sell_pressure"

        # ── Price pattern anomaly ──
        if token_info._dexscreener_ok:
            m5 = abs(token_info.dexscreener_price_change_m5)
            h1 = abs(token_info.dexscreener_price_change_h1)
            if m5 > 50:
                anomalies += 1
                total_score += 0.3
                result.signals.append(f"🚨 Movimiento de precio extremo 5m: {m5:.0f}%")
                result.anomaly_type = result.anomaly_type or "price_pattern"

        result.anomaly_score = min(1.0, round(total_score, 3))
        result.is_anomalous = anomalies >= 2 or total_score > 0.5

        return result

    def get_stats(self) -> dict:
        return {"baselines_tracked": len(self._baselines)}

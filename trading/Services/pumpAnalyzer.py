"""
pumpAnalyzer.py — Pump Prediction & Scoring Engine.

Calculates a 0–100 "pump score" for newly detected tokens based on
on-chain metrics, liquidity profile, holder distribution, trading activity,
and whale behavior patterns.

Scoring zones:
  80–100  HIGH POTENTIAL — strong buy signal
  60–79   MEDIUM — decent opportunity with some risk
  40–59   LOW — weak signals, caution advised
  0–39    AVOID — likely scam or dead token

Integration:
  Called by SniperBot after security analysis passes.
  The pump_score is included in token events and used in buy gating.
"""

import logging
import time
from dataclasses import dataclass, field, asdict
from typing import Optional

from web3 import Web3
import aiohttp
import asyncio

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
#  Data structures
# ═══════════════════════════════════════════════════════════════════

@dataclass
class PumpScore:
    """Result of pump analysis for a single token."""
    token_address: str
    total_score: int = 0               # 0–100
    grade: str = "AVOID"               # HIGH / MEDIUM / LOW / AVOID
    # Component scores (each 0–100, weighted)
    liquidity_score: int = 0           # is liquidity in the sweet spot?
    holder_score: int = 0              # healthy distribution?
    activity_score: int = 0            # buy/sell ratio, txn count
    whale_score: int = 0               # whale accumulation signals
    momentum_score: int = 0            # price momentum pattern
    age_score: int = 0                 # fresh but not too fresh
    social_score: int = 0              # social presence bonus
    # Signals (human-readable reasons)
    signals: list = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


# ═══════════════════════════════════════════════════════════════════
#  Pump Analyzer
# ═══════════════════════════════════════════════════════════════════

# Weights — must sum to 100
WEIGHTS = {
    "liquidity":  20,
    "holder":     15,
    "activity":   20,
    "whale":      15,
    "momentum":   15,
    "age":        10,
    "social":      5,
}


class PumpAnalyzer:
    """
    Evaluates a token's pump potential using on-chain + API data already
    collected during the security analysis phase.

    Does NOT make additional API calls — works entirely off TokenInfo fields
    plus optional on-chain reads for whale detection.
    """

    def __init__(self, w3: Web3, chain_id: int):
        self.w3 = w3
        self.chain_id = chain_id

    async def analyze(self, token_info, pair_liquidity_usd: float = 0,
                      pair_address: str = "") -> PumpScore:
        """
        Calculate pump score from already-collected token data.

        Args:
            token_info: TokenInfo dataclass from ContractAnalyzer
            pair_liquidity_usd: Current liquidity in USD
            pair_address: DEX pair address for on-chain reads
        """
        ps = PumpScore(token_address=token_info.address)

        # 1. Liquidity Score — sweet spot is $8k–$120k
        ps.liquidity_score = self._score_liquidity(pair_liquidity_usd, ps)

        # 2. Holder Distribution Score
        ps.holder_score = self._score_holders(token_info, ps)

        # 3. Trading Activity Score (buy/sell ratio, volume)
        ps.activity_score = self._score_activity(token_info, ps)

        # 4. Whale Score (large holder accumulation patterns)
        ps.whale_score = self._score_whale(token_info, ps)

        # 5. Momentum Score (price changes)
        ps.momentum_score = self._score_momentum(token_info, ps)

        # 6. Age Score (fresh tokens = more potential)
        ps.age_score = self._score_age(token_info, ps)

        # 7. Social Score (website, socials, CoinGecko)
        ps.social_score = self._score_social(token_info, ps)

        # Weighted total
        ps.total_score = round(
            ps.liquidity_score * WEIGHTS["liquidity"] / 100 +
            ps.holder_score    * WEIGHTS["holder"]    / 100 +
            ps.activity_score  * WEIGHTS["activity"]  / 100 +
            ps.whale_score     * WEIGHTS["whale"]     / 100 +
            ps.momentum_score  * WEIGHTS["momentum"]  / 100 +
            ps.age_score       * WEIGHTS["age"]       / 100 +
            ps.social_score    * WEIGHTS["social"]    / 100
        )

        # Grade
        if ps.total_score >= 80:
            ps.grade = "HIGH"
        elif ps.total_score >= 60:
            ps.grade = "MEDIUM"
        elif ps.total_score >= 40:
            ps.grade = "LOW"
        else:
            ps.grade = "AVOID"

        logger.info(
            f"PumpScore {token_info.symbol}: {ps.total_score}/100 ({ps.grade}) "
            f"[L={ps.liquidity_score} H={ps.holder_score} A={ps.activity_score} "
            f"W={ps.whale_score} M={ps.momentum_score} G={ps.age_score} S={ps.social_score}]"
        )

        return ps

    # ─── Component scorers ────────────────────────────────

    def _score_liquidity(self, liquidity_usd: float, ps: PumpScore) -> int:
        """
        Ideal liquidity range: $8k–$120k.
        Too low = no real backing, too high = already discovered.
        """
        if liquidity_usd <= 0:
            ps.signals.append("❌ Sin liquidez detectada")
            return 0

        if liquidity_usd < 2000:
            ps.signals.append(f"⚠️ Liquidez muy baja: ${liquidity_usd:,.0f}")
            return 10
        elif liquidity_usd < 5000:
            ps.signals.append(f"⚠️ Liquidez baja: ${liquidity_usd:,.0f}")
            return 30
        elif liquidity_usd < 8000:
            ps.signals.append(f"📊 Liquidez aceptable: ${liquidity_usd:,.0f}")
            return 55
        elif liquidity_usd <= 50000:
            # Sweet spot — highest scores
            ps.signals.append(f"✅ Liquidez ideal: ${liquidity_usd:,.0f}")
            return 100
        elif liquidity_usd <= 120000:
            ps.signals.append(f"✅ Liquidez buena: ${liquidity_usd:,.0f}")
            return 85
        elif liquidity_usd <= 300000:
            ps.signals.append(f"📊 Liquidez alta: ${liquidity_usd:,.0f} — ya descubierto")
            return 60
        elif liquidity_usd <= 1000000:
            ps.signals.append(f"📊 Liquidez muy alta: ${liquidity_usd:,.0f} — poco espacio de pump")
            return 40
        else:
            ps.signals.append(f"📊 Mega liquidez: ${liquidity_usd:,.0f} — estabilizado")
            return 20

    def _score_holders(self, info, ps: PumpScore) -> int:
        """
        Healthy holder distribution = no massive concentration.
        More holders = more organic interest.
        """
        score = 50  # baseline

        holder_count = info.holder_count
        if holder_count > 0:
            if holder_count >= 500:
                score += 30
                ps.signals.append(f"✅ {holder_count} holders — buena distribución")
            elif holder_count >= 100:
                score += 20
                ps.signals.append(f"📊 {holder_count} holders — distribución aceptable")
            elif holder_count >= 30:
                score += 5
                ps.signals.append(f"⚠️ {holder_count} holders — distribución limitada")
            elif holder_count >= 10:
                score -= 10
                ps.signals.append(f"⚠️ Solo {holder_count} holders")
            else:
                score -= 30
                ps.signals.append(f"❌ Muy pocos holders: {holder_count}")

        # Top holder concentration penalty
        if info.top_holder_percent >= 20:
            score -= 25
            ps.signals.append(f"🐋 Top holder: {info.top_holder_percent}% — concentrado")
        elif info.top_holder_percent >= 10:
            score -= 10
            ps.signals.append(f"🐋 Top holder: {info.top_holder_percent}%")
        elif 0 < info.top_holder_percent < 5:
            score += 15
            ps.signals.append(f"✅ Top holder solo {info.top_holder_percent}% — descentralizado")

        # Creator holding penalty
        if info.creator_percent >= 15:
            score -= 20
            ps.signals.append(f"👨‍💻 Creator retiene {info.creator_percent}%")
        elif info.creator_percent >= 8:
            score -= 10
        elif 0 < info.creator_percent < 3:
            score += 10
            ps.signals.append(f"✅ Creator solo tiene {info.creator_percent}%")

        return max(0, min(100, score))

    def _score_activity(self, info, ps: PumpScore) -> int:
        """
        Score based on trading activity and buy/sell ratio.
        High buy ratio + decent volume = accumulation signal.
        """
        score = 30  # baseline

        buys = info.dexscreener_buys_24h
        sells = info.dexscreener_sells_24h
        total_txns = buys + sells
        volume = info.dexscreener_volume_24h

        # Transaction count
        if total_txns >= 200:
            score += 25
            ps.signals.append(f"✅ Alta actividad: {total_txns} txns/24h")
        elif total_txns >= 50:
            score += 15
            ps.signals.append(f"📊 Actividad moderada: {total_txns} txns/24h")
        elif total_txns >= 10:
            score += 5
        elif total_txns < 5:
            score -= 20
            ps.signals.append(f"❌ Muy baja actividad: {total_txns} txns/24h")

        # Buy/sell ratio — more buys = accumulation
        if total_txns > 5:
            buy_ratio = buys / total_txns if total_txns > 0 else 0.5
            if buy_ratio >= 0.70:
                score += 25
                ps.signals.append(f"🚀 Fuerte presión compradora: {buy_ratio:.0%} buys")
            elif buy_ratio >= 0.55:
                score += 15
                ps.signals.append(f"📈 Presión compradora: {buy_ratio:.0%} buys")
            elif buy_ratio <= 0.30:
                score -= 20
                ps.signals.append(f"📉 Dominan las ventas: {buy_ratio:.0%} buys")

        # Volume — higher = more interest
        if volume >= 50000:
            score += 15
            ps.signals.append(f"✅ Volumen alto: ${volume:,.0f}/24h")
        elif volume >= 10000:
            score += 10
        elif volume >= 1000:
            score += 5
        elif volume < 100:
            score -= 15
            ps.signals.append(f"⚠️ Volumen muy bajo: ${volume:,.0f}/24h")

        return max(0, min(100, score))

    def _score_whale(self, info, ps: PumpScore) -> int:
        """
        Whale accumulation patterns.
        Uses holder data and transaction patterns to infer smart money.
        """
        score = 50  # baseline

        # If top holder is moderate (<10%) and many holders exist → healthy
        if 0 < info.top_holder_percent < 10 and info.holder_count > 50:
            score += 20
            ps.signals.append("✅ Distribución whale saludable")

        # Whale dumping risk
        if info.top_holder_percent >= 25:
            score -= 30
            ps.signals.append(f"🐋 Riesgo de whale dump: {info.top_holder_percent}%")
        elif info.top_holder_percent >= 15:
            score -= 15

        # Buy/sell ratio can indicate whale accumulation
        buys = info.dexscreener_buys_24h
        sells = info.dexscreener_sells_24h
        total = buys + sells
        if total > 20:
            if buys > sells * 2.5:
                score += 20
                ps.signals.append("🐋 Posible acumulación whale (buys >> sells)")
            elif sells > buys * 2:
                score -= 20
                ps.signals.append("⚠️ Posible distribución whale (sells >> buys)")

        # Volume vs liquidity ratio — high volume relative to liquidity = big players
        if info.dexscreener_liquidity > 0 and info.dexscreener_volume_24h > 0:
            vol_liq_ratio = info.dexscreener_volume_24h / info.dexscreener_liquidity
            if 1.0 <= vol_liq_ratio <= 5.0:
                score += 15
                ps.signals.append(f"🐋 Vol/Liq ratio: {vol_liq_ratio:.1f}x — interés institucional")
            elif vol_liq_ratio > 10:
                score -= 10
                ps.signals.append(f"⚠️ Vol/Liq ratio: {vol_liq_ratio:.1f}x — posible wash trading")

        return max(0, min(100, score))

    def _score_momentum(self, info, ps: PumpScore) -> int:
        """
        Price momentum analysis.
        Best signal: gradual uptrend (not a spike).
        """
        score = 50  # baseline

        if not info._dexscreener_ok:
            return score

        m5 = info.dexscreener_price_change_m5
        h1 = info.dexscreener_price_change_h1
        h6 = info.dexscreener_price_change_h6
        h24 = info.dexscreener_price_change_h24

        # Ideal: steady uptrend (not a pump-and-dump spike)
        # Positive 1h + positive 6h = organic growth
        if 5 <= h1 <= 30 and h6 >= 0:
            score += 25
            ps.signals.append(f"📈 Tendencia alcista estable: +{h1:.0f}% 1h, +{h6:.0f}% 6h")
        elif h1 > 50:
            score -= 15
            ps.signals.append(f"⚠️ Pump agresivo: +{h1:.0f}% en 1h — riesgo de dump")
        elif h1 < -20:
            score -= 20
            ps.signals.append(f"📉 Caída fuerte: {h1:.0f}% en 1h")

        # 5-minute momentum — good for sniper entry
        if 2 <= m5 <= 15:
            score += 15
            ps.signals.append(f"🚀 Momentum 5m positivo: +{m5:.0f}%")
        elif m5 > 30:
            score -= 15
            ps.signals.append(f"⚠️ Spike 5m: +{m5:.0f}% — posible bot pump")
        elif m5 < -10:
            score -= 10

        # 24h context
        if 10 <= h24 <= 100:
            score += 10
            ps.signals.append(f"📈 Tendencia 24h: +{h24:.0f}%")
        elif h24 < -40:
            score -= 20
            ps.signals.append(f"📉 Dump 24h: {h24:.0f}%")

        return max(0, min(100, score))

    def _score_age(self, info, ps: PumpScore) -> int:
        """
        Token age scoring.
        Sweet spot: 1–24 hours old (fresh but verified).
        """
        age_h = info.dexscreener_age_hours

        if age_h <= 0:
            ps.signals.append("🔜 Edad desconocida")
            return 40  # unknown

        if age_h < 0.5:
            ps.signals.append(f"⚡ Recién creado: {age_h*60:.0f}min — muy temprano")
            return 70   # very fresh, high risk but high reward
        elif age_h < 2:
            ps.signals.append(f"🔥 Token fresco: {age_h:.1f}h")
            return 90   # ideal sweet spot
        elif age_h < 6:
            ps.signals.append(f"✅ Token joven: {age_h:.1f}h")
            return 85
        elif age_h < 24:
            ps.signals.append(f"📊 Token de hoy: {age_h:.0f}h")
            return 70
        elif age_h < 72:
            ps.signals.append(f"📊 Token reciente: {age_h:.0f}h")
            return 50
        elif age_h < 168:  # 1 week
            ps.signals.append(f"📊 Token de ~{age_h/24:.0f} días")
            return 35
        else:
            ps.signals.append(f"📊 Token antiguo: {age_h/24:.0f} días")
            return 20

    def _score_social(self, info, ps: PumpScore) -> int:
        """
        Social presence scoring.
        Real projects have websites, socials, and exchange listings.
        """
        score = 30  # baseline

        if info.has_website:
            score += 20
            ps.signals.append("🌐 Tiene website")

        if info.has_social_links:
            score += 20
            ps.signals.append("📱 Tiene redes sociales")

        if info.listed_coingecko:
            score += 25
            ps.signals.append("✅ Listado en CoinGecko")

        if info.dexscreener_pairs >= 3:
            score += 10
            ps.signals.append(f"📊 {info.dexscreener_pairs} pares en DEX")

        return max(0, min(100, score))

"""
pumpAnalyzer.py — Advanced Pump Prediction & Scoring Engine v3.

Calculates a 0–100 "pump score" for newly detected tokens based on
on-chain metrics, liquidity profile, holder distribution, trading activity,
whale behavior patterns, market cap analysis, holder growth rate, and
liquidity growth monitoring.

Scoring zones:
  80–100  HIGH POTENTIAL — strong buy signal
  60–79   MEDIUM — decent opportunity with some risk
  40–59   LOW — weak signals, caution advised
  0–39    AVOID — likely scam or dead token

v3 Enhancements:
  - Market cap calculation and ideal range scoring ($20k–$400k sweet spot)
  - Holder growth rate tracking over time (snapshots every analysis)
  - Liquidity growth monitoring (LP increasing = confidence boost)
  - 10 scoring components (was 7)

Integration:
  Called by SniperBot after security analysis passes.
  The pump_score is included in token events and used in buy gating.
"""

import logging
import time
from dataclasses import dataclass, field, asdict
from typing import Optional
from collections import defaultdict

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
    # v3 new component scores
    mcap_score: int = 0                # market cap ideal range
    holder_growth_score: int = 0       # holder growth rate over time
    lp_growth_score: int = 0           # liquidity growth trend
    # v3 extra data
    market_cap_usd: float = 0.0        # calculated market cap
    holder_growth_rate: float = 0.0    # holders gained per minute
    lp_growth_percent: float = 0.0     # LP change since first seen
    # Signals (human-readable reasons)
    signals: list = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


@dataclass
class _TokenSnapshot:
    """Internal: time-series snapshot for growth tracking."""
    timestamp: float
    holder_count: int
    liquidity_usd: float


# ═══════════════════════════════════════════════════════════════════
#  Pump Analyzer v3
# ═══════════════════════════════════════════════════════════════════

# Weights — must sum to 100 (rebalanced for 10 components)
WEIGHTS = {
    "liquidity":      14,
    "holder":         10,
    "activity":       15,
    "whale":          10,
    "momentum":       12,
    "age":             7,
    "social":          4,
    "mcap":           12,   # NEW: market cap scoring
    "holder_growth":  10,   # NEW: holder growth rate
    "lp_growth":       6,   # NEW: liquidity growth
}


class PumpAnalyzer:
    """
    Evaluates a token's pump potential using on-chain + API data already
    collected during the security analysis phase.

    v3: Added market cap scoring, holder growth tracking (time-series),
    and liquidity growth monitoring.

    Does NOT make additional API calls — works entirely off TokenInfo fields
    plus optional on-chain reads for whale detection.
    """

    # Max snapshots per token (avoid memory leak)
    MAX_SNAPSHOTS = 50

    def __init__(self, w3: Web3, chain_id: int):
        self.w3 = w3
        self.chain_id = chain_id
        # Time-series tracking: token_address → list[_TokenSnapshot]
        self._snapshots: dict[str, list[_TokenSnapshot]] = defaultdict(list)
        # First-seen liquidity: token_address → float (USD)
        self._initial_liquidity: dict[str, float] = {}

    async def analyze(self, token_info, pair_liquidity_usd: float = 0,
                      pair_address: str = "") -> PumpScore:
        """
        Calculate pump score from already-collected token data.

        Args:
            token_info: TokenInfo dataclass from ContractAnalyzer
            pair_liquidity_usd: Current liquidity in USD
            pair_address: DEX pair address for on-chain reads
        """
        ps = PumpScore(token_address=getattr(token_info, 'address', ''))

        try:
            return await self._analyze_inner(ps, token_info, pair_liquidity_usd, pair_address)
        except Exception as e:
            # Absolute safety net — NEVER propagate exception
            logger.error(f"PumpAnalyzer.analyze() crashed: {e}", exc_info=True)
            ps.signals.append(f"⚠️ Error general: {str(e)[:80]}")
            ps.grade = "AVOID"
            return ps

    async def _analyze_inner(self, ps: PumpScore, token_info,
                             pair_liquidity_usd: float,
                             pair_address: str) -> PumpScore:
        """Internal analysis — wrapped by analyze() for safety."""
        addr = token_info.address.lower()

        # ── Record snapshot for growth tracking ──
        now = time.time()
        snap = _TokenSnapshot(
            timestamp=now,
            holder_count=token_info.holder_count,
            liquidity_usd=pair_liquidity_usd,
        )
        self._snapshots[addr].append(snap)
        if len(self._snapshots[addr]) > self.MAX_SNAPSHOTS:
            self._snapshots[addr] = self._snapshots[addr][-self.MAX_SNAPSHOTS:]

        # Track initial liquidity
        if addr not in self._initial_liquidity and pair_liquidity_usd > 0:
            self._initial_liquidity[addr] = pair_liquidity_usd

        # Run each scoring component safely — one failure must not kill the rest
        components = [
            ("liquidity",     lambda: self._score_liquidity(pair_liquidity_usd, ps)),
            ("holder",        lambda: self._score_holders(token_info, ps)),
            ("activity",      lambda: self._score_activity(token_info, ps)),
            ("whale",         lambda: self._score_whale(token_info, ps)),
            ("momentum",      lambda: self._score_momentum(token_info, ps)),
            ("age",           lambda: self._score_age(token_info, ps)),
            ("social",        lambda: self._score_social(token_info, ps)),
            ("mcap",          lambda: self._score_market_cap(token_info, pair_liquidity_usd, ps)),
            ("holder_growth", lambda: self._score_holder_growth(token_info, ps)),
            ("lp_growth",     lambda: self._score_lp_growth(pair_liquidity_usd, token_info, ps)),
        ]

        attr_map = {
            "liquidity": "liquidity_score", "holder": "holder_score",
            "activity": "activity_score",   "whale": "whale_score",
            "momentum": "momentum_score",   "age": "age_score",
            "social": "social_score",        "mcap": "mcap_score",
            "holder_growth": "holder_growth_score", "lp_growth": "lp_growth_score",
        }

        for name, scorer in components:
            try:
                value = scorer()
                setattr(ps, attr_map[name], value)
            except Exception as e:
                logger.warning(f"PumpScore component '{name}' failed for {token_info.address}: {e}")
                setattr(ps, attr_map[name], 40)  # neutral fallback
                ps.signals.append(f"⚠️ {name}: error de cálculo")

        # Weighted total
        ps.total_score = round(
            ps.liquidity_score       * WEIGHTS["liquidity"]     / 100 +
            ps.holder_score          * WEIGHTS["holder"]        / 100 +
            ps.activity_score        * WEIGHTS["activity"]      / 100 +
            ps.whale_score           * WEIGHTS["whale"]         / 100 +
            ps.momentum_score        * WEIGHTS["momentum"]      / 100 +
            ps.age_score             * WEIGHTS["age"]           / 100 +
            ps.social_score          * WEIGHTS["social"]        / 100 +
            ps.mcap_score            * WEIGHTS["mcap"]          / 100 +
            ps.holder_growth_score   * WEIGHTS["holder_growth"] / 100 +
            ps.lp_growth_score       * WEIGHTS["lp_growth"]     / 100
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
            f"W={ps.whale_score} M={ps.momentum_score} G={ps.age_score} S={ps.social_score} "
            f"MC={ps.mcap_score} HG={ps.holder_growth_score} LG={ps.lp_growth_score}]"
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

    # ─── v3 New scoring components ────────────────────────

    def _score_market_cap(self, info, pair_liquidity_usd: float, ps: PumpScore) -> int:
        """
        Market cap scoring.
        Calculate mcap from (total_supply × price_usd).
        Sweet spot: $20k–$400k (low mcap = room for 100x).
        Above $3M = already discovered, limited upside.
        """
        # Try to calculate market cap
        mcap = 0.0

        # Method 1: Use DexScreener price if available
        if info._dexscreener_ok and info.dexscreener_liquidity > 0:
            # Estimate price from liquidity and supply
            total_supply = float(info.total_supply) if info.total_supply else 0
            if total_supply > 0 and pair_liquidity_usd > 0:
                # Rough mcap estimate: liquidity * 2 is a common heuristic for
                # AMM with 50/50 pools, then scale by circulating proportion
                # Better: if we have price from DexScreener, use that directly
                pass

        # Method 2: Direct calculation from router price
        # price_per_token ≈ liquidity_usd / (reserve_token × 2)
        # Simplified: mcap ≈ pair_liquidity_usd (for very new tokens, ~80% supply in LP)
        total_supply = float(info.total_supply) if info.total_supply else 0
        if total_supply > 0 and pair_liquidity_usd > 0:
            # For AMM: token_price ≈ (liquidity_usd / 2) / token_reserve
            # Since most new tokens have ~90%+ supply in LP:
            # mcap ≈ liquidity_usd × supply_multiplier
            # Conservative: mcap ≈ liquidity_usd × 1.2 (assuming 80% in LP)
            mcap = pair_liquidity_usd * 1.2
        elif pair_liquidity_usd > 0:
            mcap = pair_liquidity_usd * 1.2

        ps.market_cap_usd = mcap

        if mcap <= 0:
            ps.signals.append("⚠️ Market cap desconocido")
            return 40

        if mcap < 10000:
            ps.signals.append(f"⚠️ Micro cap: ${mcap:,.0f} — muy arriesgado")
            return 25
        elif mcap < 20000:
            ps.signals.append(f"📊 Cap muy bajo: ${mcap:,.0f}")
            return 50
        elif mcap <= 100000:
            # Sweet spot — maximum 100x potential
            ps.signals.append(f"🚀 Low cap ideal: ${mcap:,.0f} — potencial 100x")
            return 100
        elif mcap <= 250000:
            ps.signals.append(f"✅ Low cap: ${mcap:,.0f} — potencial 10-50x")
            return 90
        elif mcap <= 400000:
            ps.signals.append(f"✅ Mid-low cap: ${mcap:,.0f} — potencial 10-20x")
            return 80
        elif mcap <= 1000000:
            ps.signals.append(f"📊 Mid cap: ${mcap:,.0f} — potencial 5-10x")
            return 60
        elif mcap <= 3000000:
            ps.signals.append(f"📊 Cap alto: ${mcap:,.0f} — potencial limitado 2-5x")
            return 40
        elif mcap <= 10000000:
            ps.signals.append(f"📊 Cap muy alto: ${mcap:,.0f} — poco espacio")
            return 25
        else:
            ps.signals.append(f"📊 Mega cap: ${mcap:,.0f} — ya establecido")
            return 10

    def _score_holder_growth(self, info, ps: PumpScore) -> int:
        """
        Holder growth rate scoring.
        Track holder count over time snapshots and measure growth velocity.
        Strong signal: +50-200 holders in 10 minutes = organic growth.
        """
        addr = info.address.lower()
        snapshots = self._snapshots.get(addr, [])

        if len(snapshots) < 2:
            # First time seeing this token — neutral score
            ps.signals.append("🔄 Holder growth: primera lectura")
            return 50

        first = snapshots[0]
        last = snapshots[-1]
        time_diff_min = (last.timestamp - first.timestamp) / 60.0

        if time_diff_min < 0.5:
            # Not enough time elapsed
            return 50

        holder_diff = last.holder_count - first.holder_count
        growth_rate = holder_diff / time_diff_min  # holders per minute
        ps.holder_growth_rate = growth_rate

        if growth_rate <= 0:
            ps.signals.append(f"📉 Holders decreciendo: {growth_rate:.1f}/min")
            if growth_rate < -5:
                return 10
            return 25
        elif growth_rate < 1:
            ps.signals.append(f"📊 Crecimiento lento: +{growth_rate:.1f} holders/min")
            return 40
        elif growth_rate < 5:
            ps.signals.append(f"📈 Crecimiento moderado: +{growth_rate:.1f} holders/min")
            return 65
        elif growth_rate < 15:
            ps.signals.append(f"🚀 Crecimiento fuerte: +{growth_rate:.1f} holders/min")
            return 85
        elif growth_rate < 50:
            ps.signals.append(f"🔥 Crecimiento explosivo: +{growth_rate:.1f} holders/min")
            return 95
        else:
            # Too fast could be bots/fake
            ps.signals.append(f"⚠️ Crecimiento sospechoso: +{growth_rate:.1f} holders/min — posibles bots")
            return 50

    def _score_lp_growth(self, current_liq: float, info, ps: PumpScore) -> int:
        """
        Liquidity growth monitoring.
        Compare current LP vs initial LP observed.
        LP increasing = dev adding liquidity = confidence.
        LP decreasing = potential slow rug.
        """
        addr = info.address.lower()
        initial = self._initial_liquidity.get(addr, 0)

        if initial <= 0 or current_liq <= 0:
            return 50  # neutral

        growth_pct = ((current_liq - initial) / initial) * 100
        ps.lp_growth_percent = growth_pct

        if growth_pct < -50:
            ps.signals.append(f"🚨 LP cayendo: {growth_pct:.0f}% — posible slow rug")
            return 5
        elif growth_pct < -20:
            ps.signals.append(f"⚠️ LP decreciendo: {growth_pct:.0f}%")
            return 20
        elif growth_pct < -5:
            ps.signals.append(f"📉 LP ligera caída: {growth_pct:.1f}%")
            return 35
        elif growth_pct < 5:
            ps.signals.append(f"📊 LP estable: {growth_pct:+.1f}%")
            return 55
        elif growth_pct < 20:
            ps.signals.append(f"📈 LP creciendo: +{growth_pct:.1f}%")
            return 70
        elif growth_pct < 50:
            ps.signals.append(f"✅ LP crecimiento fuerte: +{growth_pct:.0f}%")
            return 85
        elif growth_pct < 200:
            ps.signals.append(f"🚀 LP expansión: +{growth_pct:.0f}% — confianza alta")
            return 95
        else:
            ps.signals.append(f"🔥 LP mega expansión: +{growth_pct:.0f}%")
            return 100

    def get_stats(self) -> dict:
        """Return analyzer statistics."""
        return {
            "tracked_tokens": len(self._snapshots),
            "total_snapshots": sum(len(v) for v in self._snapshots.values()),
            "tracked_initial_lp": len(self._initial_liquidity),
        }

    def cleanup_old(self, max_age_seconds: int = 7200):
        """Remove snapshots older than max_age for memory management."""
        cutoff = time.time() - max_age_seconds
        for addr in list(self._snapshots.keys()):
            self._snapshots[addr] = [s for s in self._snapshots[addr] if s.timestamp > cutoff]
            if not self._snapshots[addr]:
                del self._snapshots[addr]
                self._initial_liquidity.pop(addr, None)

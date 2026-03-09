"""
riskEngine.py — Unified Risk Scoring Engine v1.

Combines ALL analysis modules into a single final score and decision.
This is the "brain" that decides whether to BUY, WATCH, or IGNORE a token
by weighting inputs from security analysis, pump prediction, dev reputation,
smart money signals, swap simulation, and bytecode verification.

Decision Thresholds:
  ≥ 80  → STRONG_BUY    (auto-buy if enabled)
  65–79 → BUY           (recommended buy)
  50–64 → WATCH         (monitor, don't buy yet)
  35–49 → WEAK          (probably avoid)
  < 35  → IGNORE        (definitely skip)

Component Weights (configurable):
  Security analysis:     25 pts   (GoPlus + Honeypot + TokenSniffer)
  Pump score:            20 pts   (pumpAnalyzer v3 output)
  Dev reputation:        15 pts   (devTracker output)
  Smart money signals:   15 pts   (smartMoneyTracker output)
  Swap simulation:       12 pts   (swapSimulator output)
  Bytecode analysis:      8 pts   (bytecodeAnalyzer output)
  Market conditions:      5 pts   (LP lock + price trend context)

Integration:
  Called by SniperBot._process_pair() AFTER all individual analyses complete.
  The risk_engine_score replaces/supplements the simple passes_safety logic.
"""

import logging
import time
from dataclasses import dataclass, field, asdict
from typing import Optional

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
#  Data structures
# ═══════════════════════════════════════════════════════════════════

@dataclass
class RiskDecision:
    """Final risk engine output for a token."""
    token_address: str
    # Final decision
    final_score: int = 0                # 0–100
    action: str = "IGNORE"              # STRONG_BUY / BUY / WATCH / WEAK / IGNORE
    confidence: float = 0.0             # 0.0–1.0 how confident we are
    # Component breakdown
    security_score: int = 0             # from GoPlus + Honeypot + TokenSniffer
    pump_score: int = 0                 # from pumpAnalyzer
    dev_score: int = 0                  # from devTracker
    smart_money_score: int = 0          # from smartMoneyTracker
    simulation_score: int = 0           # from swapSimulator
    bytecode_score: int = 0             # from bytecodeAnalyzer
    market_score: int = 0               # from LP lock + price context
    # Breakdown explanation
    signals: list = field(default_factory=list)
    # Hard stop flags (override everything)
    hard_stop: bool = False             # if True, IGNORE regardless of score
    hard_stop_reason: str = ""
    # Timestamps
    evaluated_at: float = 0.0

    def to_dict(self):
        return asdict(self)


# ═══════════════════════════════════════════════════════════════════
#  Risk Engine Weights
# ═══════════════════════════════════════════════════════════════════

DEFAULT_WEIGHTS = {
    "security":     25,
    "pump":         20,
    "dev":          15,
    "smart_money":  15,
    "simulation":   12,
    "bytecode":      8,
    "market":        5,
}


# ═══════════════════════════════════════════════════════════════════
#  Risk Engine
# ═══════════════════════════════════════════════════════════════════

class RiskEngine:
    """
    Unified scoring engine combining all analysis modules.

    Usage:
        engine = RiskEngine()
        decision = engine.evaluate(
            token_info=info,
            pump_result=pump_score,
            dev_result=dev_check,
            smart_signals=smart_list,
            sim_result=sim_result,
            bytecode_result=bytecode_dict,
            liquidity_usd=usd_liq,
        )
        if decision.action in ("STRONG_BUY", "BUY"):
            # proceed with purchase
    """

    def __init__(self, weights: dict = None):
        self.weights = weights or DEFAULT_WEIGHTS.copy()
        # Ensure weights sum to 100
        total = sum(self.weights.values())
        if total != 100:
            logger.warning(f"RiskEngine weights sum to {total}, normalizing to 100")
            for k in self.weights:
                self.weights[k] = round(self.weights[k] * 100 / total)

    def evaluate(
        self,
        token_info,
        pump_result=None,          # PumpScore or None
        dev_result=None,           # DevCheckResult or None
        smart_signals: list = None,  # list[SmartMoneySignal] or None
        sim_result=None,           # SimulationResult or None
        bytecode_result: dict = None,
        liquidity_usd: float = 0,
    ) -> RiskDecision:
        """
        Evaluate all inputs and produce a final risk decision.

        Each component scores 0–100 internally, then weighted.
        Hard stops (honeypot, selfdestruct, serial scammer) override everything.
        """
        decision = RiskDecision(
            token_address=token_info.address,
            evaluated_at=time.time(),
        )

        # ── 1. Security Score (GoPlus + Honeypot.is + TokenSniffer) ──
        decision.security_score = self._score_security(token_info, decision)

        # ── 2. Pump Score (from pumpAnalyzer) ──
        decision.pump_score = self._score_pump(pump_result, decision)

        # ── 3. Dev Reputation Score (from devTracker) ──
        decision.dev_score = self._score_dev(dev_result, decision)

        # ── 4. Smart Money Score ──
        decision.smart_money_score = self._score_smart_money(smart_signals, decision)

        # ── 5. Simulation Score ──
        decision.simulation_score = self._score_simulation(sim_result, token_info, decision)

        # ── 6. Bytecode Score ──
        decision.bytecode_score = self._score_bytecode(bytecode_result, token_info, decision)

        # ── 7. Market Score (LP lock + price context) ──
        decision.market_score = self._score_market(token_info, liquidity_usd, decision)

        # ── Check hard stops FIRST ──
        if decision.hard_stop:
            decision.final_score = 0
            decision.action = "IGNORE"
            decision.confidence = 1.0
            return decision

        # ── Weighted total ──
        decision.final_score = round(
            decision.security_score    * self.weights["security"]    / 100 +
            decision.pump_score        * self.weights["pump"]        / 100 +
            decision.dev_score         * self.weights["dev"]         / 100 +
            decision.smart_money_score * self.weights["smart_money"] / 100 +
            decision.simulation_score  * self.weights["simulation"]  / 100 +
            decision.bytecode_score    * self.weights["bytecode"]    / 100 +
            decision.market_score      * self.weights["market"]      / 100
        )

        # ── Action decision ──
        if decision.final_score >= 80:
            decision.action = "STRONG_BUY"
            decision.confidence = min(1.0, decision.final_score / 100)
        elif decision.final_score >= 65:
            decision.action = "BUY"
            decision.confidence = 0.7
        elif decision.final_score >= 50:
            decision.action = "WATCH"
            decision.confidence = 0.5
        elif decision.final_score >= 35:
            decision.action = "WEAK"
            decision.confidence = 0.3
        else:
            decision.action = "IGNORE"
            decision.confidence = 0.2

        # Boost confidence if multiple strong signals align
        strong_components = sum(1 for s in [
            decision.security_score,
            decision.pump_score,
            decision.dev_score,
            decision.smart_money_score,
            decision.simulation_score,
        ] if s >= 75)

        if strong_components >= 4:
            decision.confidence = min(1.0, decision.confidence + 0.15)
            decision.signals.append(f"💪 {strong_components}/5 módulos con señal fuerte")
        elif strong_components <= 1 and decision.action in ("BUY", "STRONG_BUY"):
            decision.confidence = max(0.3, decision.confidence - 0.15)
            decision.signals.append("⚠️ Pocos módulos confirman — confianza reducida")

        logger.info(
            f"RiskEngine {token_info.symbol}: {decision.final_score}/100 → {decision.action} "
            f"(conf={decision.confidence:.0%}) "
            f"[sec={decision.security_score} pump={decision.pump_score} "
            f"dev={decision.dev_score} sm={decision.smart_money_score} "
            f"sim={decision.simulation_score} bc={decision.bytecode_score} "
            f"mkt={decision.market_score}]"
        )

        return decision

    # ─── Component scorers ────────────────────────────────

    def _score_security(self, info, dec: RiskDecision) -> int:
        """Score from security APIs (GoPlus, Honeypot.is, TokenSniffer)."""
        score = 50  # baseline

        # Hard stops
        if info.is_honeypot:
            dec.hard_stop = True
            dec.hard_stop_reason = "Honeypot confirmado"
            dec.signals.append("🚨 HARD STOP: Honeypot")
            return 0

        if info.is_airdrop_scam:
            dec.hard_stop = True
            dec.hard_stop_reason = "Airdrop scam"
            dec.signals.append("🚨 HARD STOP: Airdrop scam")
            return 0

        # GoPlus flags
        if info._goplus_ok:
            if info.is_open_source:
                score += 10
            else:
                score -= 25
                dec.signals.append("⚠️ Código no verificado")

            if info.is_proxy:
                score -= 20
                dec.signals.append("⚠️ Contrato proxy")

            if info.has_hidden_owner:
                score -= 15

            if info.can_take_back_ownership:
                score -= 15

            if info.is_mintable:
                score -= 10

            if info.has_blacklist:
                score -= 10

            if info.can_pause_trading:
                score -= 10

            if info.owner_can_change_balance:
                score -= 20
                dec.signals.append("🚨 Owner puede cambiar balances")

            if info.can_self_destruct:
                score -= 20

            if info.cannot_sell_all:
                score -= 15

            if not info.is_true_token:
                score -= 20

            # Top holder concentration
            if info.top_holder_percent >= 30:
                score -= 20
            elif info.top_holder_percent >= 15:
                score -= 10

            # Creator holding
            if info.creator_percent >= 20:
                score -= 15
            elif info.creator_percent >= 10:
                score -= 5

        # Tax analysis
        if info.buy_tax <= 5 and info.sell_tax <= 5:
            score += 15
            dec.signals.append("✅ Impuestos bajos")
        elif info.buy_tax > 10 or info.sell_tax > 15:
            score -= 15
            dec.signals.append(f"⚠️ Impuestos altos: buy {info.buy_tax}% sell {info.sell_tax}%")

        # TokenSniffer
        if info._tokensniffer_ok:
            if info.tokensniffer_is_scam:
                dec.hard_stop = True
                dec.hard_stop_reason = "TokenSniffer: SCAM"
                dec.signals.append("🚨 HARD STOP: TokenSniffer marcó como SCAM")
                return 0
            if info.tokensniffer_score >= 80:
                score += 15
            elif info.tokensniffer_score >= 50:
                score += 5
            elif info.tokensniffer_score < 30:
                score -= 15

        # API coverage bonus (more APIs confirmed = more confidence)
        api_count = sum([
            info._goplus_ok,
            info._honeypot_ok,
            info._dexscreener_ok,
            info._coingecko_ok,
            info._tokensniffer_ok,
        ])
        if api_count >= 4:
            score += 5
        elif api_count <= 1:
            score -= 10

        return max(0, min(100, score))

    def _score_pump(self, pump_result, dec: RiskDecision) -> int:
        """Score from pump analyzer."""
        if not pump_result:
            return 50  # neutral if no data

        score = pump_result.total_score
        grade = pump_result.grade

        if grade == "HIGH":
            dec.signals.append(f"🚀 Pump grade HIGH: {score}/100")
        elif grade == "MEDIUM":
            dec.signals.append(f"📊 Pump grade MEDIUM: {score}/100")
        elif grade == "LOW":
            dec.signals.append(f"⚠️ Pump grade LOW: {score}/100")
        elif grade == "AVOID":
            dec.signals.append(f"❌ Pump grade AVOID: {score}/100")

        return score

    def _score_dev(self, dev_result, dec: RiskDecision) -> int:
        """Score from dev tracker."""
        if not dev_result:
            return 50  # neutral if no data

        # Serial scammer hard stop
        if dev_result.is_serial_scammer:
            dec.hard_stop = True
            dec.hard_stop_reason = f"Serial scammer: {dev_result.rug_pulls} rug pulls"
            dec.signals.append(f"🚨 HARD STOP: Dev es scammer serial ({dev_result.rug_pulls} rugs)")
            return 0

        score = dev_result.dev_score

        if dev_result.is_tracked and dev_result.successful_launches > 0:
            dec.signals.append(
                f"✅ Dev conocido: {dev_result.successful_launches} éxitos, "
                f"mejor {dev_result.best_multiplier:.1f}x"
            )
        elif dev_result.rug_pulls > 0:
            dec.signals.append(f"⚠️ Dev con {dev_result.rug_pulls} rug pull(s)")
            score = max(0, score - 20)

        return max(0, min(100, score))

    def _score_smart_money(self, signals: list, dec: RiskDecision) -> int:
        """Score from smart money tracker."""
        if not signals:
            return 50  # neutral

        score = 50
        if len(signals) >= 3:
            score = 95
            dec.signals.append(f"🐋 {len(signals)} whales comprando — señal muy fuerte")
        elif len(signals) >= 2:
            score = 80
            dec.signals.append(f"🐋 {len(signals)} whales comprando")
        elif len(signals) == 1:
            best = signals[0]
            if best.confidence >= 80:
                score = 75
            elif best.confidence >= 50:
                score = 65
            else:
                score = 55
            dec.signals.append(f"🐋 1 whale comprando (conf: {best.confidence}%)")

        return max(0, min(100, score))

    def _score_simulation(self, sim_result, info, dec: RiskDecision) -> int:
        """Score from swap simulation."""
        if not sim_result and not info._simulation_ok:
            return 50  # neutral

        score = 70  # baseline for successful simulation

        # Use token_info simulation fields (already populated by sniperService)
        if info._simulation_ok:
            if info.sim_is_honeypot:
                dec.hard_stop = True
                dec.hard_stop_reason = f"Simulation honeypot: {info.sim_honeypot_reason}"
                dec.signals.append(f"🚨 HARD STOP: Simulation confirmó honeypot")
                return 0

            if info.sim_can_buy and info.sim_can_sell:
                score += 20
                dec.signals.append("✅ Simulación: compra y venta exitosas")
            elif info.sim_can_buy and not info.sim_can_sell:
                score -= 30
                dec.signals.append("⚠️ Simulación: se puede comprar pero NO vender")

            # Tax from simulation (more accurate than API)
            if info.sim_buy_tax <= 5 and info.sim_sell_tax <= 5:
                score += 10
            elif info.sim_buy_tax > 10 or info.sim_sell_tax > 15:
                score -= 15

        elif sim_result:
            if sim_result.is_honeypot:
                dec.hard_stop = True
                dec.hard_stop_reason = f"Simulation honeypot: {sim_result.honeypot_reason}"
                return 0
            if sim_result.can_buy and sim_result.can_sell:
                score += 20
            if sim_result.buy_tax_percent <= 5 and sim_result.sell_tax_percent <= 5:
                score += 10

        return max(0, min(100, score))

    def _score_bytecode(self, bytecode_result: dict, info, dec: RiskDecision) -> int:
        """Score from bytecode analysis."""
        if not bytecode_result and not info._bytecode_ok:
            return 50  # neutral

        score = 70  # baseline

        if info._bytecode_ok:
            if info.bytecode_has_selfdestruct:
                dec.hard_stop = True
                dec.hard_stop_reason = "SELFDESTRUCT opcode detectado"
                dec.signals.append("🚨 HARD STOP: SELFDESTRUCT en bytecode")
                return 0

            if info.bytecode_has_delegatecall:
                score -= 25
                dec.signals.append("⚠️ DELEGATECALL en bytecode — puede ser hijacked")

            if info.bytecode_is_proxy:
                score -= 15

            flags = info.bytecode_flags
            if flags:
                score -= len(flags) * 5
                dec.signals.append(f"⚠️ {len(flags)} flag(s) en bytecode")
            else:
                score += 20
                dec.signals.append("✅ Bytecode limpio — sin flags")

            # Reasonable bytecode size
            if 0 < info.bytecode_size < 200:
                score -= 10  # suspiciously small
            elif info.bytecode_size > 24000:
                score += 5  # complex contract, likely real project

        elif bytecode_result:
            if bytecode_result.get("has_selfdestruct"):
                dec.hard_stop = True
                dec.hard_stop_reason = "SELFDESTRUCT opcode"
                return 0
            if not bytecode_result.get("risk_flags"):
                score += 20

        return max(0, min(100, score))

    def _score_market(self, info, liquidity_usd: float, dec: RiskDecision) -> int:
        """Score from market conditions (LP lock, price trends)."""
        score = 50  # baseline

        # LP lock
        if info.lp_locked:
            score += 15
            if info.lp_lock_percent >= 90:
                score += 10
            if info.lp_lock_hours_remaining >= 168:  # 1 week+
                score += 10
                dec.signals.append(f"🔒 LP locked {info.lp_lock_percent:.0f}% por {info.lp_lock_hours_remaining:.0f}h")
            elif info.lp_lock_hours_remaining < 24:
                score -= 10
                dec.signals.append(f"⚠️ LP lock expira pronto: {info.lp_lock_hours_remaining:.1f}h")
        else:
            score -= 20
            dec.signals.append("⚠️ LP no bloqueado")

        # Price trend context
        if info._dexscreener_ok:
            h1 = info.dexscreener_price_change_h1
            h24 = info.dexscreener_price_change_h24

            if h24 <= -50:
                score -= 15
            elif h24 <= -30:
                score -= 10
            elif 10 <= h24 <= 100:
                score += 5

            if h1 > 50:
                score -= 5  # already pumped

        # Liquidity adequacy
        if liquidity_usd >= 10000:
            score += 5
        elif liquidity_usd < 3000:
            score -= 10

        return max(0, min(100, score))

    def get_stats(self) -> dict:
        """Return engine statistics."""
        return {
            "weights": self.weights.copy(),
            "thresholds": {
                "STRONG_BUY": 80,
                "BUY": 65,
                "WATCH": 50,
                "WEAK": 35,
                "IGNORE": 0,
            },
        }

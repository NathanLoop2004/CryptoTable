"""
devTracker.py — Developer Reputation Tracker v1.

Maintains a database of token deployers (developers) and tracks their
historical success rate. When a new token is detected, checks if its
creator is a tracked dev with a proven track record — boosting confidence.

Key Features:
  - Track developers who launched successful tokens before
  - Score dev reputation 0–100 based on launch history
  - Auto-detect when tracked devs deploy new contracts
  - Distinguish serial scammers from legitimate devs
  - Provide dev_score to RiskEngine for final decision

Dev Score Components:
  - Launch history:      40 pts  (successful launches, avg ROI)
  - Liquidity behavior:  20 pts  (LP lock pattern, no rugs)
  - Community track:     15 pts  (holder growth across launches)
  - Contract quality:    15 pts  (verified source, no red flags)
  - Recency:             10 pts  (recent activity bonus)

Integration:
  Called by SniperBot._process_pair() to check if token creator
  is a tracked dev. Result fed into RiskEngine.
"""

import logging
import time
import asyncio
from dataclasses import dataclass, field, asdict
from typing import Optional
from collections import defaultdict

from web3 import Web3

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
#  Data structures
# ═══════════════════════════════════════════════════════════════════

@dataclass
class DevLaunch:
    """Record of a single token launch by a developer."""
    token_address: str
    symbol: str = ""
    launch_time: float = 0.0
    initial_liquidity_usd: float = 0.0
    peak_liquidity_usd: float = 0.0
    peak_mcap_usd: float = 0.0
    was_rug_pull: bool = False
    lp_locked: bool = False
    lp_lock_percent: float = 0.0
    holder_count_peak: int = 0
    is_honeypot: bool = False
    is_verified: bool = False           # code verified on explorer
    max_roi_x: float = 0.0             # peak ROI multiplier (e.g. 5.0 = 5x)
    outcome: str = "unknown"            # "success" | "neutral" | "rug" | "unknown"

    def to_dict(self):
        return asdict(self)


@dataclass
class DevProfile:
    """Tracked developer profile with reputation data."""
    wallet: str                         # deployer address (checksum)
    label: str = ""                     # optional human-readable label
    # Launch history
    tokens_launched: int = 0
    successful_launches: int = 0        # tokens that did 3x+ without rugging
    rug_pulls: int = 0
    best_multiplier: float = 0.0        # best ROI across all launches
    avg_roi: float = 0.0                # average peak ROI
    # Scores
    reputation_score: int = 0           # 0–100
    launch_history_score: int = 0       # component
    liquidity_score: int = 0            # component
    community_score: int = 0            # component
    quality_score: int = 0              # component
    recency_score: int = 0              # component
    # Metadata
    first_seen: float = 0.0
    last_launch_time: float = 0.0
    source: str = "auto"                # "auto" | "manual" | "known"
    # Internal launch records
    launches: list = field(default_factory=list)

    def to_dict(self):
        d = asdict(self)
        # Limit launches in serialization to save bandwidth
        d["launches"] = d["launches"][-10:]
        return d


@dataclass
class DevCheckResult:
    """Result of checking a token's creator against dev tracker."""
    creator_address: str
    is_tracked: bool = False
    is_known_dev: bool = False
    dev_score: int = 0                  # 0–100 reputation
    dev_label: str = ""
    total_launches: int = 0
    successful_launches: int = 0
    rug_pulls: int = 0
    best_multiplier: float = 0.0
    is_serial_scammer: bool = False     # 3+ rug pulls
    # v5: ML reputation fields
    ml_reputation_score: int = 50       # 0–100 ML-predicted reputation
    ml_cluster: str = "neutral"         # legit_dev / neutral / suspicious / scammer
    ml_confidence: float = 0.0          # 0–1 confidence
    ml_linked_wallets: int = 0          # wallets linked via funding graph
    signals: list = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


# ═══════════════════════════════════════════════════════════════════
#  Known good/bad developer addresses (community curated)
# ═══════════════════════════════════════════════════════════════════

# Format: { "address_lower": ("label", "type") }
# type: "good" = known legit, "scam" = known scammer
KNOWN_DEVS: dict[str, tuple[str, str]] = {
    # Add known addresses here as they are discovered
    # "0x1234...": ("SafeDev Alpha", "good"),
    # "0xdead...": ("Serial Rugger", "scam"),
}


# ═══════════════════════════════════════════════════════════════════
#  Dev Tracker
# ═══════════════════════════════════════════════════════════════════

class DevTracker:
    """
    Tracks token deployers and maintains reputation scores.

    Usage:
        tracker = DevTracker(w3, chain_id)
        result = await tracker.check_creator(token_address, token_info)
        tracker.record_launch_outcome(token_address, creator, outcome_data)
    """

    def __init__(self, w3: Web3, chain_id: int, enable_ml: bool = True):
        self.w3 = w3
        self.chain_id = chain_id
        self.running = False
        self.enable_ml = enable_ml

        # Dev profiles indexed by address (lowercase)
        self._devs: dict[str, DevProfile] = {}

        # Token → creator mapping cache
        self._token_creators: dict[str, str] = {}

        # v5: ML Reputation predictor
        self._ml_predictor = None
        if self.enable_ml:
            try:
                from trading.Services.mlPredictor import DevReputationML
                self._ml_predictor = DevReputationML()
                logger.info("DevTracker: ML reputation predictor enabled")
            except Exception as e:
                logger.warning(f"DevTracker: ML predictor unavailable: {e}")

        # Load known devs
        for addr, (label, dtype) in KNOWN_DEVS.items():
            profile = DevProfile(
                wallet=Web3.to_checksum_address(addr),
                label=label,
                source="known",
                first_seen=time.time(),
            )
            if dtype == "scam":
                profile.rug_pulls = 99
                profile.reputation_score = 0
            elif dtype == "good":
                profile.reputation_score = 80
                profile.successful_launches = 5
            self._devs[addr.lower()] = profile

    async def check_creator(self, token_address: str, token_info=None) -> DevCheckResult:
        """
        Check if a token's creator is a tracked developer.

        Attempts to find the contract creator via:
        1. token_info.owner_address if available
        2. On-chain lookup of contract creation transaction

        Returns DevCheckResult with reputation data.
        """
        result = DevCheckResult(creator_address="")

        # Try to find creator address
        creator = ""
        if token_info and hasattr(token_info, 'owner_address') and token_info.owner_address:
            creator = token_info.owner_address
        elif token_address.lower() in self._token_creators:
            creator = self._token_creators[token_address.lower()]
        else:
            # Try on-chain lookup (get deployer from contract creation tx)
            creator = await self._find_contract_creator(token_address)

        if not creator:
            result.signals.append("❓ Creator desconocido")
            return result

        result.creator_address = creator
        self._token_creators[token_address.lower()] = creator
        creator_lower = creator.lower()

        # Check if tracked
        if creator_lower in self._devs:
            profile = self._devs[creator_lower]
            result.is_tracked = True
            result.is_known_dev = profile.source == "known"
            result.dev_score = profile.reputation_score
            result.dev_label = profile.label
            result.total_launches = profile.tokens_launched
            result.successful_launches = profile.successful_launches
            result.rug_pulls = profile.rug_pulls
            result.best_multiplier = profile.best_multiplier

            # Serial scammer detection
            if profile.rug_pulls >= 3:
                result.is_serial_scammer = True
                result.dev_score = max(0, result.dev_score - 50)
                result.signals.append(f"🚨 SCAMMER SERIAL: {profile.rug_pulls} rug pulls")
            elif profile.rug_pulls >= 1:
                result.signals.append(f"⚠️ Dev tiene {profile.rug_pulls} rug pull(s) previo(s)")

            if profile.successful_launches > 0:
                result.signals.append(
                    f"✅ Dev exitoso: {profile.successful_launches} lanzamientos buenos "
                    f"(mejor: {profile.best_multiplier:.1f}x)"
                )

            if profile.label:
                result.signals.append(f"🏷️ Dev conocido: {profile.label}")
        else:
            # New developer — auto-track
            profile = DevProfile(
                wallet=Web3.to_checksum_address(creator),
                first_seen=time.time(),
                source="auto",
            )
            self._devs[creator_lower] = profile
            result.dev_score = 50  # neutral for unknown devs
            result.signals.append("🆕 Developer nuevo — sin historial")

        # v5: ML reputation prediction
        if self._ml_predictor and profile:
            try:
                ml_result = self._ml_predictor.predict(profile)
                result.ml_reputation_score = ml_result.ml_reputation_score
                result.ml_cluster = ml_result.cluster
                result.ml_confidence = ml_result.cross_wallet_risk
                # Count linked wallets from funding graph
                wallet_lower = profile.wallet.lower() if hasattr(profile, 'wallet') else ""
                linked = self._ml_predictor._funding_graph.get(wallet_lower, set())
                result.ml_linked_wallets = len(linked)

                if ml_result.cluster == "scammer":
                    result.signals.append(
                        f"🤖 ML: SCAMMER detectado (score {ml_result.ml_reputation_score}, "
                        f"pattern {ml_result.pattern_score})"
                    )
                elif ml_result.cluster == "suspicious":
                    result.signals.append(
                        f"🤖 ML: Dev sospechoso (score {ml_result.ml_reputation_score}, "
                        f"pattern {ml_result.pattern_score})"
                    )
                elif ml_result.cluster == "legit_dev":
                    result.signals.append(
                        f"🤖 ML: Dev legítimo (score {ml_result.ml_reputation_score}, "
                        f"pattern {ml_result.pattern_score})"
                    )

                if result.ml_linked_wallets > 0:
                    result.signals.append(
                        f"🔗 ML: {result.ml_linked_wallets} wallet(s) vinculadas"
                    )

                # Blend ML score with traditional score
                # 70% traditional, 30% ML
                result.dev_score = round(
                    result.dev_score * 0.70 + ml_result.ml_reputation_score * 0.30
                )
            except Exception as e:
                logger.debug(f"DevTracker ML prediction error: {e}")

        return result

    async def record_launch(self, token_address: str, creator: str,
                            token_info=None, liquidity_usd: float = 0):
        """Record a new token launch for a developer."""
        creator_lower = creator.lower()
        self._token_creators[token_address.lower()] = creator

        if creator_lower not in self._devs:
            self._devs[creator_lower] = DevProfile(
                wallet=Web3.to_checksum_address(creator),
                first_seen=time.time(),
                source="auto",
            )

        profile = self._devs[creator_lower]
        profile.tokens_launched += 1
        profile.last_launch_time = time.time()

        launch = DevLaunch(
            token_address=token_address,
            symbol=token_info.symbol if token_info else "",
            launch_time=time.time(),
            initial_liquidity_usd=liquidity_usd,
            lp_locked=token_info.lp_locked if token_info else False,
            lp_lock_percent=token_info.lp_lock_percent if token_info else 0,
            is_verified=token_info.is_open_source if token_info else False,
            is_honeypot=token_info.is_honeypot if token_info else False,
        )
        profile.launches.append(launch)

        # Keep launches manageable
        if len(profile.launches) > 50:
            profile.launches = profile.launches[-50:]

        # Recalculate reputation
        self._recalculate_reputation(profile)

        logger.info(
            f"DevTracker: Recorded launch #{profile.tokens_launched} for "
            f"{creator[:10]}… — token {token_address[:10]}…"
        )

    async def record_outcome(self, token_address: str, outcome: str,
                             peak_roi: float = 0, was_rug: bool = False):
        """
        Record the outcome of a token launch after monitoring.

        Args:
            token_address: Token contract address
            outcome: "success" | "neutral" | "rug"
            peak_roi: Peak ROI multiplier (e.g. 5.0 = 5x)
            was_rug: Whether the token was a rug pull
        """
        token_lower = token_address.lower()
        creator = self._token_creators.get(token_lower)
        if not creator:
            return

        creator_lower = creator.lower()
        profile = self._devs.get(creator_lower)
        if not profile:
            return

        # Update the specific launch record
        for launch in reversed(profile.launches):
            if launch.token_address.lower() == token_lower:
                launch.outcome = outcome
                launch.max_roi_x = peak_roi
                launch.was_rug_pull = was_rug
                break

        # Update profile stats
        if was_rug or outcome == "rug":
            profile.rug_pulls += 1
        elif outcome == "success" or peak_roi >= 3.0:
            profile.successful_launches += 1

        if peak_roi > profile.best_multiplier:
            profile.best_multiplier = peak_roi

        # Recalculate reputation
        self._recalculate_reputation(profile)

        logger.info(
            f"DevTracker: Outcome for {creator[:10]}… — "
            f"{outcome} (ROI: {peak_roi:.1f}x, rug: {was_rug})"
        )

    def _recalculate_reputation(self, profile: DevProfile):
        """Recalculate a developer's reputation score from all available data."""
        # 1. Launch History Score (40 pts max)
        history_score = 50  # baseline
        if profile.tokens_launched > 0:
            success_rate = profile.successful_launches / profile.tokens_launched
            rug_rate = profile.rug_pulls / profile.tokens_launched

            if rug_rate >= 0.5:
                history_score = 5
            elif rug_rate >= 0.3:
                history_score = 20
            elif success_rate >= 0.7:
                history_score = 95
            elif success_rate >= 0.5:
                history_score = 80
            elif success_rate >= 0.3:
                history_score = 65
            elif success_rate > 0:
                history_score = 55
            else:
                history_score = 40

            # Bonus for high ROI history
            if profile.best_multiplier >= 10:
                history_score = min(100, history_score + 15)
            elif profile.best_multiplier >= 5:
                history_score = min(100, history_score + 10)

        profile.launch_history_score = max(0, min(100, history_score))

        # 2. Liquidity Behavior Score (20 pts max)
        liq_score = 50
        locked_count = sum(1 for l in profile.launches if l.lp_locked)
        if profile.launches:
            lock_rate = locked_count / len(profile.launches)
            if lock_rate >= 0.8:
                liq_score = 90
            elif lock_rate >= 0.5:
                liq_score = 70
            elif lock_rate < 0.2:
                liq_score = 20
        profile.liquidity_score = max(0, min(100, liq_score))

        # 3. Community Score (15 pts max)
        comm_score = 50
        avg_holders = 0
        holder_launches = [l for l in profile.launches if l.holder_count_peak > 0]
        if holder_launches:
            avg_holders = sum(l.holder_count_peak for l in holder_launches) / len(holder_launches)
            if avg_holders >= 500:
                comm_score = 90
            elif avg_holders >= 200:
                comm_score = 75
            elif avg_holders >= 50:
                comm_score = 60
            else:
                comm_score = 35
        profile.community_score = max(0, min(100, comm_score))

        # 4. Contract Quality Score (15 pts max)
        qual_score = 50
        verified_count = sum(1 for l in profile.launches if l.is_verified)
        honeypot_count = sum(1 for l in profile.launches if l.is_honeypot)
        if profile.launches:
            verify_rate = verified_count / len(profile.launches)
            hp_rate = honeypot_count / len(profile.launches)
            if hp_rate >= 0.3:
                qual_score = 10
            elif verify_rate >= 0.8:
                qual_score = 90
            elif verify_rate >= 0.5:
                qual_score = 70
            elif verify_rate < 0.2:
                qual_score = 30
        profile.quality_score = max(0, min(100, qual_score))

        # 5. Recency Score (10 pts max)
        rec_score = 50
        if profile.last_launch_time > 0:
            hours_since = (time.time() - profile.last_launch_time) / 3600
            if hours_since < 24:
                rec_score = 90
            elif hours_since < 72:
                rec_score = 75
            elif hours_since < 168:  # 1 week
                rec_score = 60
            elif hours_since < 720:  # 1 month
                rec_score = 40
            else:
                rec_score = 25
        profile.recency_score = max(0, min(100, rec_score))

        # Weighted total
        profile.reputation_score = round(
            profile.launch_history_score * 0.40 +
            profile.liquidity_score      * 0.20 +
            profile.community_score      * 0.15 +
            profile.quality_score        * 0.15 +
            profile.recency_score        * 0.10
        )

        # Calculate average ROI
        roi_launches = [l for l in profile.launches if l.max_roi_x > 0]
        if roi_launches:
            profile.avg_roi = sum(l.max_roi_x for l in roi_launches) / len(roi_launches)

    async def _find_contract_creator(self, token_address: str) -> str:
        """
        Try to find the contract creator address on-chain.
        Uses BSCScan/Etherscan API if available, otherwise falls back
        to checking the first transaction to the contract.
        """
        loop = asyncio.get_event_loop()
        try:
            # Method: Check the contract creation by looking at the first
            # internal transaction. For most tokens, the deployer is the
            # account that sent the creation tx.
            checksum = Web3.to_checksum_address(token_address)

            # Try to get the contract code — if it exists, try nonce-based lookup
            code = await loop.run_in_executor(
                None, self.w3.eth.get_code, checksum
            )
            if len(code) < 4:
                return ""

            # Unfortunately, web3.py doesn't have a direct "getContractCreator" method.
            # We'll return empty and let the caller provide it from GoPlus/token_info.
            return ""

        except Exception as e:
            logger.debug(f"Creator lookup failed for {token_address}: {e}")
            return ""

    def add_dev(self, address: str, label: str = "", source: str = "manual"):
        """Manually add a developer to track."""
        addr_lower = address.lower()
        if addr_lower not in self._devs:
            self._devs[addr_lower] = DevProfile(
                wallet=Web3.to_checksum_address(address),
                label=label,
                source=source,
                first_seen=time.time(),
            )
        else:
            if label:
                self._devs[addr_lower].label = label
            if source:
                self._devs[addr_lower].source = source
        return self._devs[addr_lower].to_dict()

    def get_tracked_devs(self, top_n: int = 50) -> list[dict]:
        """Get top tracked developers sorted by reputation."""
        sorted_devs = sorted(
            self._devs.values(),
            key=lambda d: d.reputation_score,
            reverse=True,
        )
        return [d.to_dict() for d in sorted_devs[:top_n]]

    def get_dev_profile(self, address: str) -> Optional[dict]:
        """Get a specific developer's profile."""
        profile = self._devs.get(address.lower())
        return profile.to_dict() if profile else None

    def link_wallets(self, from_wallet: str, to_wallet: str, evidence: str = ""):
        """v5: Record a funding link between two wallets for ML clustering."""
        if self._ml_predictor:
            try:
                self._ml_predictor.link_wallets(from_wallet, to_wallet, evidence)
                logger.debug(
                    f"DevTracker: Linked {from_wallet[:10]}… → {to_wallet[:10]}… "
                    f"({evidence})"
                )
            except Exception as e:
                logger.debug(f"DevTracker: link_wallets error: {e}")

    def get_stats(self) -> dict:
        """Return tracker statistics."""
        total = len(self._devs)
        good = sum(1 for d in self._devs.values() if d.reputation_score >= 70)
        bad = sum(1 for d in self._devs.values() if d.rug_pulls >= 2)
        stats = {
            "total_tracked": total,
            "good_devs": good,
            "known_scammers": bad,
            "tokens_mapped": len(self._token_creators),
            "ml_enabled": self._ml_predictor is not None,
        }
        if self._ml_predictor:
            stats["ml_clusters"] = len(self._ml_predictor._wallet_clusters)
            stats["ml_funding_links"] = sum(
                len(v) for v in self._ml_predictor._funding_graph.values()
            )
        return stats

    def stop(self):
        """Stop tracker."""
        self.running = False

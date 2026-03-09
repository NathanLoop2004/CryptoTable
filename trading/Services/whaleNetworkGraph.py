"""
whaleNetworkGraph.py — Whale & Smart Money Network Graph Engine.

Maps relationships between wallets to detect:
  1. Coordinated whale movements (same funding source)
  2. Smart money wallet clusters
  3. Historical wallet profitability (past token performance)
  4. Fund flow patterns (where did the money come from / go to)
  5. Sybil attack detection (many wallets, one entity)

Architecture:
  wallet tracker → graph builder → cluster detection
  → profitability scorer → influence mapper

Uses graph theory (adjacency, connected components, centrality)
to uncover hidden wallet relationships.
"""

import asyncio
import logging
import time
import math
from dataclasses import dataclass, field
from typing import Optional
from enum import Enum
from collections import defaultdict, deque

logger = logging.getLogger(__name__)

try:
    from web3 import Web3
    HAS_WEB3 = True
except ImportError:
    Web3 = None
    HAS_WEB3 = False


# ═══════════════════════════════════════════════════════════════════
#  Data Classes
# ═══════════════════════════════════════════════════════════════════

class WalletType(Enum):
    """Wallet classification types."""
    UNKNOWN = "unknown"
    WHALE = "whale"
    SMART_MONEY = "smart_money"
    BOT = "bot"
    DEPLOYER = "deployer"
    INSIDER = "insider"
    EXCHANGE = "exchange"
    RETAIL = "retail"
    SYBIL = "sybil"


class RelationType(Enum):
    """Types of relationships between wallets."""
    FUNDING = "funding"                # A funded B
    TOKEN_TRANSFER = "token_transfer"  # A sent tokens to B
    SAME_TOKEN_BUY = "same_token_buy"  # Both bought same token within window
    CO_DEPLOYMENT = "co_deployment"    # Both deployed by same funder
    SEQUENTIAL_BUY = "sequential_buy"  # A bought, then B bought same token


@dataclass
class WalletNode:
    """A wallet in the network graph."""
    address: str = ""
    wallet_type: WalletType = WalletType.UNKNOWN
    label: str = ""
    # Activity metrics
    total_buys: int = 0
    total_sells: int = 0
    total_volume_native: float = 0.0
    active_since: float = 0.0
    last_active: float = 0.0
    # Profitability
    total_pnl_pct: float = 0.0
    win_rate: float = 0.0
    avg_hold_time_hours: float = 0.0
    best_trade_pnl: float = 0.0
    worst_trade_pnl: float = 0.0
    tokens_traded: int = 0
    # Network metrics
    connections: int = 0               # number of edges
    cluster_id: int = -1               # which cluster this wallet belongs to
    centrality_score: float = 0.0      # importance in the network
    influence_score: float = 0.0       # how much this wallet influences prices
    # Flags
    is_known_rugger: bool = False
    is_known_whale: bool = False
    is_exchange_wallet: bool = False
    trust_score: float = 50.0          # 0-100

    def to_dict(self) -> dict:
        return {
            "address": self.address,
            "type": self.wallet_type.value,
            "label": self.label,
            "total_buys": self.total_buys,
            "total_sells": self.total_sells,
            "volume_native": round(self.total_volume_native, 4),
            "total_pnl_pct": round(self.total_pnl_pct, 2),
            "win_rate": round(self.win_rate, 3),
            "tokens_traded": self.tokens_traded,
            "connections": self.connections,
            "cluster_id": self.cluster_id,
            "centrality_score": round(self.centrality_score, 4),
            "influence_score": round(self.influence_score, 4),
            "trust_score": round(self.trust_score, 1),
            "is_known_rugger": self.is_known_rugger,
        }


@dataclass
class WalletEdge:
    """A relationship between two wallets."""
    from_address: str = ""
    to_address: str = ""
    relation_type: RelationType = RelationType.FUNDING
    weight: float = 1.0
    count: int = 1                     # how many times this relationship occurred
    total_value_native: float = 0.0
    first_seen: float = 0.0
    last_seen: float = 0.0
    tokens_in_common: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "from": self.from_address,
            "to": self.to_address,
            "type": self.relation_type.value,
            "weight": round(self.weight, 3),
            "count": self.count,
            "value_native": round(self.total_value_native, 4),
            "tokens_in_common": self.tokens_in_common[:10],
        }


@dataclass
class WalletCluster:
    """A group of related wallets."""
    cluster_id: int = 0
    wallets: list = field(default_factory=list)  # addresses
    cluster_type: str = "unknown"       # whale_group / bot_network / sybil / organic
    total_volume: float = 0.0
    avg_pnl: float = 0.0
    coordinated_buys: int = 0
    risk_score: float = 0.0
    description: str = ""

    def to_dict(self) -> dict:
        return {
            "cluster_id": self.cluster_id,
            "wallets": self.wallets[:20],  # limit display
            "wallet_count": len(self.wallets),
            "type": self.cluster_type,
            "total_volume": round(self.total_volume, 4),
            "avg_pnl": round(self.avg_pnl, 2),
            "coordinated_buys": self.coordinated_buys,
            "risk_score": round(self.risk_score, 1),
            "description": self.description,
        }


@dataclass
class NetworkAnalysis:
    """Complete network analysis result for a token."""
    token_address: str = ""
    total_wallets: int = 0
    total_edges: int = 0
    clusters: list = field(default_factory=list)
    top_whales: list = field(default_factory=list)
    smart_money_wallets: list = field(default_factory=list)
    suspicious_wallets: list = field(default_factory=list)
    # Metrics
    network_density: float = 0.0      # edges / possible_edges
    coordination_score: float = 0.0    # 0-100 (higher = more coordinated)
    sybil_risk: float = 0.0           # 0-100
    whale_concentration: float = 0.0   # % volume from top wallets
    smart_money_sentiment: str = ""    # bullish / bearish / neutral

    def to_dict(self) -> dict:
        return {
            "token_address": self.token_address,
            "total_wallets": self.total_wallets,
            "total_edges": self.total_edges,
            "clusters": [c.to_dict() if hasattr(c, "to_dict") else c for c in self.clusters],
            "top_whales": self.top_whales[:10],
            "smart_money_wallets": self.smart_money_wallets[:10],
            "suspicious_wallets": self.suspicious_wallets[:10],
            "network_density": round(self.network_density, 4),
            "coordination_score": round(self.coordination_score, 1),
            "sybil_risk": round(self.sybil_risk, 1),
            "whale_concentration": round(self.whale_concentration, 2),
            "smart_money_sentiment": self.smart_money_sentiment,
        }


@dataclass
class GraphConfig:
    """Configuration for the whale network graph."""
    enabled: bool = True
    max_wallets_tracked: int = 5000
    max_edges: int = 20000
    # Relationship detection
    funding_lookback_blocks: int = 1000
    same_token_window_seconds: float = 300  # 5 min
    sequential_buy_window_seconds: float = 60
    min_edge_weight: float = 0.1
    # Whale thresholds
    whale_min_volume_native: float = 5.0
    smart_money_min_win_rate: float = 0.65
    smart_money_min_trades: int = 10
    # Cluster detection
    min_cluster_size: int = 2
    sybil_min_cluster_size: int = 5
    sybil_low_balance_threshold: float = 0.01  # BNB
    # Known addresses (can be extended)
    known_exchange_wallets: list = field(default_factory=lambda: [
        "0x8894e0a0c962cb723c1ef8a1b6ba5104d8eaadd7",  # Binance Hot
        "0xe2fc31F816A9b94326492132018C3aEcC4a93aE1",  # Binance
    ])

    def to_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "max_wallets": self.max_wallets_tracked,
            "whale_min_volume": self.whale_min_volume_native,
            "smart_money_min_wr": self.smart_money_min_win_rate,
        }


# ═══════════════════════════════════════════════════════════════════
#  Graph Engine
# ═══════════════════════════════════════════════════════════════════

class GraphEngine:
    """Core graph operations: adjacency, components, centrality."""

    def __init__(self):
        self._adjacency: dict[str, dict[str, float]] = defaultdict(dict)
        self._edge_count = 0

    def add_edge(self, from_addr: str, to_addr: str, weight: float = 1.0):
        """Add or update an edge."""
        a = from_addr.lower()
        b = to_addr.lower()
        if b not in self._adjacency[a]:
            self._edge_count += 1
        self._adjacency[a][b] = self._adjacency[a].get(b, 0) + weight
        # Bidirectional for clustering
        if a not in self._adjacency[b]:
            pass  # directed graph, but we use both directions for components
        self._adjacency[b][a] = self._adjacency[b].get(a, 0) + weight

    def get_neighbors(self, address: str) -> dict[str, float]:
        return dict(self._adjacency.get(address.lower(), {}))

    def get_all_nodes(self) -> set[str]:
        return set(self._adjacency.keys())

    def find_connected_components(self) -> list[set[str]]:
        """Find connected components using BFS."""
        visited = set()
        components = []

        for node in self._adjacency:
            if node in visited:
                continue
            component = set()
            queue = deque([node])
            while queue:
                current = queue.popleft()
                if current in visited:
                    continue
                visited.add(current)
                component.add(current)
                for neighbor in self._adjacency.get(current, {}):
                    if neighbor not in visited:
                        queue.append(neighbor)
            if component:
                components.append(component)

        return components

    def compute_degree_centrality(self) -> dict[str, float]:
        """Compute degree centrality for all nodes."""
        n = len(self._adjacency)
        if n <= 1:
            return {addr: 0 for addr in self._adjacency}
        centrality = {}
        for addr, neighbors in self._adjacency.items():
            centrality[addr] = len(neighbors) / (n - 1)
        return centrality

    def compute_weighted_centrality(self) -> dict[str, float]:
        """Compute weighted degree centrality."""
        centrality = {}
        max_weight = 1
        for addr, neighbors in self._adjacency.items():
            total = sum(neighbors.values())
            centrality[addr] = total
            max_weight = max(max_weight, total)
        # Normalize
        for addr in centrality:
            centrality[addr] /= max_weight
        return centrality

    @property
    def edge_count(self) -> int:
        return self._edge_count

    def clear(self):
        self._adjacency.clear()
        self._edge_count = 0


# ═══════════════════════════════════════════════════════════════════
#  Cluster Analyzer
# ═══════════════════════════════════════════════════════════════════

class ClusterAnalyzer:
    """Analyzes wallet clusters for patterns."""

    def __init__(self, config: GraphConfig = None):
        self.config = config or GraphConfig()

    def classify_cluster(self, wallets: list[WalletNode]) -> WalletCluster:
        """Classify a cluster of wallets."""
        cluster = WalletCluster(
            wallets=[w.address for w in wallets],
        )

        if not wallets:
            return cluster

        cluster.total_volume = sum(w.total_volume_native for w in wallets)
        pnls = [w.total_pnl_pct for w in wallets if w.tokens_traded > 0]
        cluster.avg_pnl = sum(pnls) / len(pnls) if pnls else 0

        # Classify cluster type
        whale_count = sum(1 for w in wallets if w.wallet_type == WalletType.WHALE)
        bot_count = sum(1 for w in wallets if w.wallet_type == WalletType.BOT)
        low_balance = sum(
            1 for w in wallets
            if w.total_volume_native < self.config.sybil_low_balance_threshold
        )

        n = len(wallets)
        if n >= self.config.sybil_min_cluster_size and low_balance > n * 0.7:
            cluster.cluster_type = "sybil"
            cluster.risk_score = 80
            cluster.description = f"Sybil cluster: {n} wallets with low balance, likely one entity"
        elif bot_count > n * 0.5:
            cluster.cluster_type = "bot_network"
            cluster.risk_score = 60
            cluster.description = f"Bot network: {bot_count}/{n} wallets are bots"
        elif whale_count > 0:
            cluster.cluster_type = "whale_group"
            cluster.risk_score = 40
            cluster.description = f"Whale group: {whale_count} whales with combined {cluster.total_volume:.2f} BNB"
        else:
            cluster.cluster_type = "organic"
            cluster.risk_score = 10
            cluster.description = f"Organic cluster: {n} wallets"

        return cluster

    def detect_sybil(self, wallets: list[WalletNode], edges: list[WalletEdge]) -> float:
        """
        Compute sybil risk score (0-100).
        High when many wallets are connected, funded similarly, low balance.
        """
        if len(wallets) < 3:
            return 0

        # Check funding patterns: many wallets funded by same source
        funding_sources = defaultdict(int)
        for edge in edges:
            if edge.relation_type == RelationType.FUNDING:
                funding_sources[edge.from_address] += 1

        max_funded = max(funding_sources.values()) if funding_sources else 0
        funding_concentration = max_funded / len(wallets) if wallets else 0

        # Check balance distribution
        low_balance_pct = sum(
            1 for w in wallets
            if w.total_volume_native < self.config.sybil_low_balance_threshold
        ) / len(wallets) if wallets else 0

        # Check timing similarity (concurrent buys)
        concurrent_factor = 0
        buy_times = [w.last_active for w in wallets if w.last_active > 0]
        if len(buy_times) >= 3:
            sorted_times = sorted(buy_times)
            diffs = [sorted_times[i+1] - sorted_times[i] for i in range(len(sorted_times) - 1)]
            avg_diff = sum(diffs) / len(diffs) if diffs else 999
            if avg_diff < 10:  # within 10 seconds
                concurrent_factor = 0.4
            elif avg_diff < 60:
                concurrent_factor = 0.2

        sybil_score = (
            funding_concentration * 40 +
            low_balance_pct * 30 +
            concurrent_factor * 100 +
            min(20, len(wallets) * 2)
        )

        return min(100, sybil_score)


# ═══════════════════════════════════════════════════════════════════
#  Smart Money Tracker
# ═══════════════════════════════════════════════════════════════════

class SmartMoneyTracker:
    """Tracks and identifies smart money wallets."""

    def __init__(self, config: GraphConfig = None):
        self.config = config or GraphConfig()

    def is_smart_money(self, wallet: WalletNode) -> bool:
        """Check if a wallet qualifies as smart money."""
        if wallet.tokens_traded < self.config.smart_money_min_trades:
            return False
        if wallet.win_rate < self.config.smart_money_min_win_rate:
            return False
        if wallet.total_pnl_pct < 0:
            return False
        return True

    def get_sentiment(self, smart_wallets: list[WalletNode],
                      token_address: str, recent_trades: list[dict]) -> str:
        """
        Determine smart money sentiment for a token.
        Returns: bullish / bearish / neutral
        """
        buys = 0
        sells = 0
        smart_addrs = {w.address.lower() for w in smart_wallets}

        for trade in recent_trades:
            addr = trade.get("wallet", "").lower()
            if addr in smart_addrs:
                if trade.get("direction") == "buy":
                    buys += 1
                else:
                    sells += 1

        if buys > sells * 2:
            return "bullish"
        elif sells > buys * 2:
            return "bearish"
        return "neutral"

    def compute_influence_scores(self, wallets: dict[str, WalletNode]) -> dict[str, float]:
        """
        Compute influence score for each wallet.
        Based on: volume, profitability, and network position.
        """
        scores = {}
        max_volume = max((w.total_volume_native for w in wallets.values()), default=1)
        max_pnl = max((abs(w.total_pnl_pct) for w in wallets.values()), default=1)

        for addr, wallet in wallets.items():
            vol_norm = wallet.total_volume_native / max_volume if max_volume > 0 else 0
            pnl_norm = max(0, wallet.total_pnl_pct) / max_pnl if max_pnl > 0 else 0
            wr_norm = wallet.win_rate

            influence = vol_norm * 0.4 + pnl_norm * 0.35 + wr_norm * 0.25
            scores[addr] = min(1.0, influence)

        return scores


# ═══════════════════════════════════════════════════════════════════
#  Main: Whale Network Graph
# ═══════════════════════════════════════════════════════════════════

class WhaleNetworkGraph:
    """
    Builds and analyzes the whale & smart money network graph.

    Usage:
        graph = WhaleNetworkGraph(w3)
        analysis = await graph.analyze_token("0x...")
        whales = graph.get_top_whales(10)
    """

    def __init__(self, w3=None, chain_id: int = 56, config: GraphConfig = None):
        self.w3 = w3
        self.chain_id = chain_id
        self.config = config or GraphConfig()
        self._graph = GraphEngine()
        self._cluster_analyzer = ClusterAnalyzer(self.config)
        self._smart_tracker = SmartMoneyTracker(self.config)
        self._wallets: dict[str, WalletNode] = {}
        self._edges: list[WalletEdge] = []
        self._analysis_cache: dict[str, dict] = {}
        self._total_analyses: int = 0
        self._emit_callback = None

    def set_emit_callback(self, callback):
        self._emit_callback = callback

    def add_wallet(self, address: str, **kwargs) -> WalletNode:
        """Add or update a wallet in the graph."""
        addr = address.lower()
        if addr not in self._wallets:
            self._wallets[addr] = WalletNode(address=addr)

        wallet = self._wallets[addr]
        for k, v in kwargs.items():
            if hasattr(wallet, k):
                setattr(wallet, k, v)

        # Auto-classify
        self._classify_wallet(wallet)
        return wallet

    def add_relationship(self, from_addr: str, to_addr: str,
                         relation_type: RelationType = RelationType.FUNDING,
                         value_native: float = 0, token_address: str = ""):
        """Add a relationship between two wallets."""
        fa = from_addr.lower()
        ta = to_addr.lower()

        # Find existing edge or create new
        existing = None
        for edge in self._edges:
            if (edge.from_address == fa and edge.to_address == ta and
                edge.relation_type == relation_type):
                existing = edge
                break

        if existing:
            existing.count += 1
            existing.total_value_native += value_native
            existing.last_seen = time.time()
            existing.weight += 0.5
            if token_address and token_address not in existing.tokens_in_common:
                existing.tokens_in_common.append(token_address)
        else:
            edge = WalletEdge(
                from_address=fa,
                to_address=ta,
                relation_type=relation_type,
                weight=1.0,
                count=1,
                total_value_native=value_native,
                first_seen=time.time(),
                last_seen=time.time(),
                tokens_in_common=[token_address] if token_address else [],
            )
            self._edges.append(edge)

        # Update graph engine
        self._graph.add_edge(fa, ta, weight=1.0)

        # Ensure both wallets exist
        if fa not in self._wallets:
            self._wallets[fa] = WalletNode(address=fa)
        if ta not in self._wallets:
            self._wallets[ta] = WalletNode(address=ta)

        self._wallets[fa].connections = len(self._graph.get_neighbors(fa))
        self._wallets[ta].connections = len(self._graph.get_neighbors(ta))

    def record_trade(self, wallet_address: str, token_address: str,
                     direction: str, amount_native: float, pnl_pct: float = 0):
        """Record a trade for a wallet."""
        addr = wallet_address.lower()
        wallet = self.add_wallet(addr)

        if direction == "buy":
            wallet.total_buys += 1
        else:
            wallet.total_sells += 1

        wallet.total_volume_native += amount_native
        wallet.last_active = time.time()
        if wallet.active_since == 0:
            wallet.active_since = time.time()

        if pnl_pct != 0:
            # Update PnL tracking
            total_trades = wallet.total_buys + wallet.total_sells
            if total_trades > 0:
                wallet.total_pnl_pct = (
                    (wallet.total_pnl_pct * (total_trades - 1) + pnl_pct)
                    / total_trades
                )
            if pnl_pct > 0:
                wallet.win_rate = (
                    (wallet.win_rate * (total_trades - 1) + 1) / total_trades
                )
            else:
                wallet.win_rate = (
                    wallet.win_rate * (total_trades - 1) / total_trades
                )

            wallet.best_trade_pnl = max(wallet.best_trade_pnl, pnl_pct)
            wallet.worst_trade_pnl = min(wallet.worst_trade_pnl, pnl_pct)

        wallet.tokens_traded = max(wallet.tokens_traded, 1)
        self._classify_wallet(wallet)

    async def analyze_token(self, token_address: str,
                            trades: list[dict] = None) -> NetworkAnalysis:
        """
        Perform full network analysis for a token.

        trades: list of {wallet, direction, amount_native, pnl_pct, timestamp}
        """
        self._total_analyses += 1
        addr = token_address.lower()

        # Cache check
        if addr in self._analysis_cache:
            cached = self._analysis_cache[addr]
            if time.time() - cached.get("_timestamp", 0) < 300:
                return self._build_analysis_from_cache(cached)

        # Process trades if provided
        if trades:
            await self._process_token_trades(addr, trades)

        # Build analysis
        analysis = NetworkAnalysis(token_address=token_address)

        # Get token-related wallets
        token_wallets = self._get_wallets_for_token(addr, trades or [])
        analysis.total_wallets = len(token_wallets)
        analysis.total_edges = self._graph.edge_count

        # Compute centrality
        centrality = self._graph.compute_weighted_centrality()
        for wallet_addr, score in centrality.items():
            if wallet_addr in self._wallets:
                self._wallets[wallet_addr].centrality_score = score

        # Find clusters
        components = self._graph.find_connected_components()
        clusters = []
        for component in components:
            if len(component) >= self.config.min_cluster_size:
                cluster_wallets = [
                    self._wallets[a] for a in component if a in self._wallets
                ]
                cluster = self._cluster_analyzer.classify_cluster(cluster_wallets)
                cluster.cluster_id = len(clusters)
                for w in cluster_wallets:
                    w.cluster_id = cluster.cluster_id
                clusters.append(cluster)

        analysis.clusters = [c.to_dict() for c in clusters]

        # Compute influence scores
        influence = self._smart_tracker.compute_influence_scores(self._wallets)
        for wa, score in influence.items():
            if wa in self._wallets:
                self._wallets[wa].influence_score = score

        # Top whales
        whales = sorted(
            [w for w in token_wallets if w.wallet_type == WalletType.WHALE],
            key=lambda w: -w.total_volume_native
        )
        analysis.top_whales = [w.to_dict() for w in whales[:10]]

        # Smart money
        smart = [w for w in token_wallets if self._smart_tracker.is_smart_money(w)]
        analysis.smart_money_wallets = [w.to_dict() for w in smart[:10]]

        # Suspicious wallets
        suspicious = [
            w for w in token_wallets
            if w.wallet_type in (WalletType.SYBIL, WalletType.INSIDER)
               or w.is_known_rugger
        ]
        analysis.suspicious_wallets = [w.to_dict() for w in suspicious[:10]]

        # Network metrics
        n = len(token_wallets)
        max_edges = n * (n - 1) / 2 if n > 1 else 1
        analysis.network_density = min(1, analysis.total_edges / max_edges)

        # Coordination score
        sybil_clusters = [c for c in clusters if c.cluster_type == "sybil"]
        bot_clusters = [c for c in clusters if c.cluster_type == "bot_network"]
        coordination = 0
        if sybil_clusters:
            coordination += 40
        if bot_clusters:
            coordination += 30
        if len(clusters) > 0:
            largest = max(clusters, key=lambda c: len(c.wallets))
            coordination += min(30, len(largest.wallets) * 3)
        analysis.coordination_score = min(100, coordination)

        # Sybil risk
        token_edges = [e for e in self._edges if addr in str(e.tokens_in_common).lower()]
        analysis.sybil_risk = self._cluster_analyzer.detect_sybil(
            token_wallets, token_edges
        )

        # Whale concentration
        if token_wallets:
            total_vol = sum(w.total_volume_native for w in token_wallets)
            whale_vol = sum(w.total_volume_native for w in whales[:3])
            analysis.whale_concentration = whale_vol / total_vol if total_vol > 0 else 0

        # Smart money sentiment
        analysis.smart_money_sentiment = self._smart_tracker.get_sentiment(
            smart, token_address, trades or []
        )

        # Cache
        result_dict = analysis.to_dict()
        result_dict["_timestamp"] = time.time()
        self._analysis_cache[addr] = result_dict

        return analysis

    async def _process_token_trades(self, token_address: str, trades: list[dict]):
        """Process a list of trades to build graph relationships."""
        # Sort by timestamp
        sorted_trades = sorted(trades, key=lambda t: t.get("timestamp", 0))

        prev_buyers = []
        for trade in sorted_trades:
            wallet = trade.get("wallet", "").lower()
            direction = trade.get("direction", "buy")
            amount = trade.get("amount_native", 0)
            pnl = trade.get("pnl_pct", 0)
            ts = trade.get("timestamp", time.time())

            self.record_trade(wallet, token_address, direction, amount, pnl)

            if direction == "buy":
                # Check for sequential buys within window
                for prev_wallet, prev_ts in prev_buyers:
                    if ts - prev_ts <= self.config.sequential_buy_window_seconds:
                        self.add_relationship(
                            prev_wallet, wallet,
                            RelationType.SEQUENTIAL_BUY,
                            token_address=token_address,
                        )
                    if ts - prev_ts <= self.config.same_token_window_seconds:
                        self.add_relationship(
                            prev_wallet, wallet,
                            RelationType.SAME_TOKEN_BUY,
                            token_address=token_address,
                        )

                prev_buyers.append((wallet, ts))
                # Keep only recent buyers
                cutoff = ts - self.config.same_token_window_seconds
                prev_buyers = [(w, t) for w, t in prev_buyers if t >= cutoff]

    def _get_wallets_for_token(self, token_address: str,
                               trades: list[dict]) -> list[WalletNode]:
        """Get all wallets that traded a specific token."""
        addrs = set()
        for trade in trades:
            w = trade.get("wallet", "").lower()
            if w:
                addrs.add(w)

        # Also check edges for this token
        for edge in self._edges:
            if token_address in [t.lower() for t in edge.tokens_in_common]:
                addrs.add(edge.from_address)
                addrs.add(edge.to_address)

        return [self._wallets[a] for a in addrs if a in self._wallets]

    def _classify_wallet(self, wallet: WalletNode):
        """Auto-classify wallet type based on metrics."""
        addr = wallet.address.lower()

        # Check known addresses
        if addr in [e.lower() for e in self.config.known_exchange_wallets]:
            wallet.wallet_type = WalletType.EXCHANGE
            wallet.is_exchange_wallet = True
            wallet.trust_score = 90
            return

        if wallet.is_known_rugger:
            wallet.wallet_type = WalletType.DEPLOYER
            wallet.trust_score = 5
            return

        # Volume-based classification
        if wallet.total_volume_native >= self.config.whale_min_volume_native:
            wallet.wallet_type = WalletType.WHALE
            wallet.is_known_whale = True
        elif self._smart_tracker.is_smart_money(wallet):
            wallet.wallet_type = WalletType.SMART_MONEY
        else:
            wallet.wallet_type = WalletType.RETAIL

        # Trust score
        trust = 50
        if wallet.win_rate > 0.6 and wallet.tokens_traded >= 5:
            trust += 20
        if wallet.total_pnl_pct > 0:
            trust += 10
        if wallet.is_known_rugger:
            trust -= 40
        wallet.trust_score = max(0, min(100, trust))

    def _build_analysis_from_cache(self, cached: dict) -> NetworkAnalysis:
        """Reconstruct NetworkAnalysis from cached dict."""
        analysis = NetworkAnalysis()
        for k, v in cached.items():
            if k.startswith("_"):
                continue
            if hasattr(analysis, k):
                setattr(analysis, k, v)
        return analysis

    # ─── Public API ───────────────────────────────────────────────

    def get_wallet(self, address: str) -> Optional[dict]:
        """Get wallet info."""
        wallet = self._wallets.get(address.lower())
        return wallet.to_dict() if wallet else None

    def get_top_whales(self, n: int = 10) -> list[dict]:
        """Get top N whales by volume."""
        whales = sorted(
            [w for w in self._wallets.values() if w.wallet_type == WalletType.WHALE],
            key=lambda w: -w.total_volume_native
        )
        return [w.to_dict() for w in whales[:n]]

    def get_smart_money(self, n: int = 10) -> list[dict]:
        """Get top N smart money wallets."""
        smart = [w for w in self._wallets.values()
                 if self._smart_tracker.is_smart_money(w)]
        smart.sort(key=lambda w: -w.total_pnl_pct)
        return [w.to_dict() for w in smart[:n]]

    def get_wallet_connections(self, address: str) -> dict:
        """Get all connections for a wallet."""
        addr = address.lower()
        neighbors = self._graph.get_neighbors(addr)
        connections = []
        for neighbor_addr, weight in neighbors.items():
            wallet = self._wallets.get(neighbor_addr)
            if wallet:
                connections.append({
                    "address": neighbor_addr,
                    "type": wallet.wallet_type.value,
                    "weight": round(weight, 3),
                    "trust_score": wallet.trust_score,
                })
        return {
            "wallet": addr,
            "connections": connections,
            "total": len(connections),
        }

    def get_clusters(self) -> list[dict]:
        """Get all detected clusters."""
        components = self._graph.find_connected_components()
        clusters = []
        for component in components:
            if len(component) >= self.config.min_cluster_size:
                wallets = [self._wallets[a] for a in component if a in self._wallets]
                cluster = self._cluster_analyzer.classify_cluster(wallets)
                cluster.cluster_id = len(clusters)
                clusters.append(cluster.to_dict())
        return clusters

    def configure(self, **kwargs):
        """Update configuration."""
        for k, v in kwargs.items():
            if hasattr(self.config, k):
                setattr(self.config, k, v)

    def get_stats(self) -> dict:
        return {
            "total_wallets": len(self._wallets),
            "total_edges": len(self._edges),
            "total_analyses": self._total_analyses,
            "whale_count": sum(1 for w in self._wallets.values()
                             if w.wallet_type == WalletType.WHALE),
            "smart_money_count": sum(1 for w in self._wallets.values()
                                    if self._smart_tracker.is_smart_money(w)),
            "clusters": len(self._graph.find_connected_components()),
            "config": self.config.to_dict(),
        }

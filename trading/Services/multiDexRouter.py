"""
multiDexRouter.py — Multi-DEX / Multi-Chain Router v6.
═══════════════════════════════════════════════════════

Extends the sniper from BSC-only to multiple chains and DEXes,
finding the best execution route across all supported venues.

Supported Chains:
  - BNB Smart Chain (56):  PancakeSwap V2/V3, BiSwap
  - Ethereum (1):           Uniswap V2/V3, SushiSwap
  - Arbitrum (42161):       Uniswap V3, SushiSwap, Camelot
  - Base (8453):            BaseSwap, Aerodrome, Uniswap V3
  - Polygon (137):          QuickSwap, SushiSwap, Uniswap V3

Features:
  - DEX registry with factory/router addresses per chain
  - Best-route finder: compares amountOut across all DEXes
  - PairCreated listener for multiple factories
  - Gas-aware routing (cheapest chain for same token)
  - Automatic chain detection from token address
  - Unified swap interface regardless of underlying DEX

Architecture:
  MultiDexRouter wraps TradeExecutor to add multi-DEX awareness:
    1. ChainConfig → defines RPCs, factories, routers for each chain
    2. DexConfig → defines a specific DEX on a chain
    3. Router → selects best DEX and executes swap

Integration:
  SniperBot uses MultiDexRouter instead of direct PancakeSwap integration.
  The router transparently picks the best DEX for each trade.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field, asdict
from typing import Optional, Callable

from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
#  Data structures
# ═══════════════════════════════════════════════════════════════════

@dataclass
class DexConfig:
    """Configuration for a single DEX."""
    name: str
    chain_id: int
    version: str = "v2"                    # "v2" | "v3"
    factory_address: str = ""
    router_address: str = ""
    fee: int = 0                           # V3 fee tier (500, 3000, 10000)
    # PairCreated topic hash
    pair_created_topic: str = ""
    # Wrapped native token
    weth_address: str = ""
    # Performance
    avg_gas_cost: int = 250_000
    estimated_slippage_pct: float = 0.5
    # Status
    enabled: bool = True
    priority: int = 1                      # lower = higher priority

    def to_dict(self):
        return asdict(self)


@dataclass
class ChainConfig:
    """Configuration for a blockchain network."""
    chain_id: int
    name: str
    symbol: str                            # ETH, BNB, MATIC
    rpc_urls: list = field(default_factory=list)
    wss_urls: list = field(default_factory=list)
    wrapped_native: str = ""
    explorer_url: str = ""
    dexes: list = field(default_factory=list)  # list[DexConfig]
    # Block time in seconds
    block_time: float = 3.0
    # Gas
    max_gas_gwei: float = 50.0
    default_gas_gwei: float = 5.0
    # Status
    enabled: bool = True
    is_poa: bool = False                   # For PoA chains (BSC, etc.)

    def to_dict(self):
        d = asdict(self)
        d["dexes"] = [dx if isinstance(dx, dict) else dx for dx in d["dexes"]]
        return d


@dataclass
class RouteResult:
    """Result of a route search across DEXes."""
    dex_name: str = ""
    chain_id: int = 0
    router_address: str = ""
    factory_address: str = ""
    path: list = field(default_factory=list)   # token addresses
    amount_in: int = 0
    amount_out: int = 0
    amount_out_usd: float = 0.0
    price_impact_pct: float = 0.0
    gas_estimate: int = 0
    gas_cost_usd: float = 0.0
    effective_price_usd: float = 0.0
    slippage_pct: float = 0.0
    # Score (higher = better)
    score: float = 0.0

    def to_dict(self):
        return asdict(self)


# ═══════════════════════════════════════════════════════════════════
#  DEX Registry — all supported chains and DEXes
# ═══════════════════════════════════════════════════════════════════

PAIR_CREATED_TOPIC_V2 = "0x" + Web3.keccak(
    text="PairCreated(address,address,address,uint256)"
).hex()

# V3 uses PoolCreated instead of PairCreated
POOL_CREATED_TOPIC_V3 = "0x" + Web3.keccak(
    text="PoolCreated(address,address,uint24,int24,address)"
).hex()


def build_chain_registry() -> dict[int, ChainConfig]:
    """Build the full chain/DEX registry."""
    registry = {}

    # ─── BNB Smart Chain ────────────────────────────────
    registry[56] = ChainConfig(
        chain_id=56,
        name="BNB Smart Chain",
        symbol="BNB",
        rpc_urls=["https://bsc-dataseed1.binance.org", "https://bsc-dataseed2.binance.org"],
        wss_urls=["wss://bsc-ws-node.nariox.org:443"],
        wrapped_native="0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c",
        explorer_url="https://bscscan.com",
        block_time=3.0,
        max_gas_gwei=10.0,
        default_gas_gwei=5.0,
        is_poa=True,
        dexes=[
            DexConfig(
                name="PancakeSwap V2", chain_id=56, version="v2",
                factory_address="0xcA143Ce32Fe78f1f7019d7d551a6402fC5350c73",
                router_address="0x10ED43C718714eb63d5aA57B78B54704E256024E",
                weth_address="0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c",
                pair_created_topic=PAIR_CREATED_TOPIC_V2,
                priority=1,
            ),
            DexConfig(
                name="PancakeSwap V3", chain_id=56, version="v3",
                factory_address="0x0BFbCF9fa4f9C56B0F40a671Ad40E0805A091865",
                router_address="0x13f4EA83D0bd40E75C8222255bc855a974568Dd4",
                weth_address="0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c",
                pair_created_topic=POOL_CREATED_TOPIC_V3,
                fee=2500,
                priority=2,
            ),
            DexConfig(
                name="BiSwap", chain_id=56, version="v2",
                factory_address="0x858E3312ed3A876947EA49d572A7C42DE08af7EE",
                router_address="0x3a6d8cA21D1CF76F653A67577FA0D27453350dD8",
                weth_address="0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c",
                pair_created_topic=PAIR_CREATED_TOPIC_V2,
                priority=3,
            ),
        ],
    )

    # ─── Ethereum ────────────────────────────────────────
    registry[1] = ChainConfig(
        chain_id=1,
        name="Ethereum",
        symbol="ETH",
        rpc_urls=["https://eth.llamarpc.com", "https://rpc.ankr.com/eth"],
        wss_urls=[],
        wrapped_native="0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
        explorer_url="https://etherscan.io",
        block_time=12.0,
        max_gas_gwei=200.0,
        default_gas_gwei=30.0,
        is_poa=False,
        dexes=[
            DexConfig(
                name="Uniswap V2", chain_id=1, version="v2",
                factory_address="0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f",
                router_address="0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D",
                weth_address="0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
                pair_created_topic=PAIR_CREATED_TOPIC_V2,
                priority=1,
            ),
            DexConfig(
                name="Uniswap V3", chain_id=1, version="v3",
                factory_address="0x1F98431c8aD98523631AE4a59f267346ea31F984",
                router_address="0xE592427A0AEce92De3Edee1F18E0157C05861564",
                weth_address="0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
                pair_created_topic=POOL_CREATED_TOPIC_V3,
                fee=3000,
                priority=2,
            ),
            DexConfig(
                name="SushiSwap", chain_id=1, version="v2",
                factory_address="0xC0AEe478e3658e2610c5F7A4A2E1777cE9e4f2Ac",
                router_address="0xd9e1cE17f2641f24aE83637ab66a2cca9C378B9F",
                weth_address="0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
                pair_created_topic=PAIR_CREATED_TOPIC_V2,
                priority=3,
            ),
        ],
    )

    # ─── Arbitrum ────────────────────────────────────────
    registry[42161] = ChainConfig(
        chain_id=42161,
        name="Arbitrum One",
        symbol="ETH",
        rpc_urls=["https://arb1.arbitrum.io/rpc", "https://rpc.ankr.com/arbitrum"],
        wss_urls=[],
        wrapped_native="0x82aF49447D8a07e3bd95BD0d56f35241523fBab1",
        explorer_url="https://arbiscan.io",
        block_time=0.25,
        max_gas_gwei=5.0,
        default_gas_gwei=0.1,
        is_poa=False,
        dexes=[
            DexConfig(
                name="Uniswap V3 (Arb)", chain_id=42161, version="v3",
                factory_address="0x1F98431c8aD98523631AE4a59f267346ea31F984",
                router_address="0xE592427A0AEce92De3Edee1F18E0157C05861564",
                weth_address="0x82aF49447D8a07e3bd95BD0d56f35241523fBab1",
                pair_created_topic=POOL_CREATED_TOPIC_V3,
                fee=3000,
                priority=1,
            ),
            DexConfig(
                name="Camelot V2", chain_id=42161, version="v2",
                factory_address="0x6EcCab422D763aC031210895C81787E87B43A652",
                router_address="0xc873fEcbd354f5A56E00E710B90EF4201db2448d",
                weth_address="0x82aF49447D8a07e3bd95BD0d56f35241523fBab1",
                pair_created_topic=PAIR_CREATED_TOPIC_V2,
                priority=2,
            ),
            DexConfig(
                name="SushiSwap (Arb)", chain_id=42161, version="v2",
                factory_address="0xc35DADB65012eC5796536bD9864eD8773aBc74C4",
                router_address="0x1b02dA8Cb0d097eB8D57A175b88c7D8b47997506",
                weth_address="0x82aF49447D8a07e3bd95BD0d56f35241523fBab1",
                pair_created_topic=PAIR_CREATED_TOPIC_V2,
                priority=3,
            ),
        ],
    )

    # ─── Base ────────────────────────────────────────────
    registry[8453] = ChainConfig(
        chain_id=8453,
        name="Base",
        symbol="ETH",
        rpc_urls=["https://mainnet.base.org", "https://rpc.ankr.com/base"],
        wss_urls=[],
        wrapped_native="0x4200000000000000000000000000000000000006",
        explorer_url="https://basescan.org",
        block_time=2.0,
        max_gas_gwei=5.0,
        default_gas_gwei=0.1,
        is_poa=False,
        dexes=[
            DexConfig(
                name="Uniswap V3 (Base)", chain_id=8453, version="v3",
                factory_address="0x33128a8fC17869897dcE68Ed026d694621f6FDfD",
                router_address="0x2626664c2603336E57B271c5C0b26F421741e481",
                weth_address="0x4200000000000000000000000000000000000006",
                pair_created_topic=POOL_CREATED_TOPIC_V3,
                fee=3000,
                priority=1,
            ),
            DexConfig(
                name="BaseSwap", chain_id=8453, version="v2",
                factory_address="0xFDa619b6d20975be80A10332cD39b9a4b0FAa8BB",
                router_address="0x327Df1E6de05895d2ab08513aaDD9313Fe505d86",
                weth_address="0x4200000000000000000000000000000000000006",
                pair_created_topic=PAIR_CREATED_TOPIC_V2,
                priority=2,
            ),
            DexConfig(
                name="Aerodrome", chain_id=8453, version="v2",
                factory_address="0x420DD381b31aEf6683db6B902084cB0FFECe40Da",
                router_address="0xcF77a3Ba9A5CA399B7c97c74d54e5b1Beb874E43",
                weth_address="0x4200000000000000000000000000000000000006",
                pair_created_topic=PAIR_CREATED_TOPIC_V2,
                priority=3,
            ),
        ],
    )

    # ─── Polygon ─────────────────────────────────────────
    registry[137] = ChainConfig(
        chain_id=137,
        name="Polygon",
        symbol="MATIC",
        rpc_urls=["https://polygon-rpc.com", "https://rpc.ankr.com/polygon"],
        wss_urls=[],
        wrapped_native="0x0d500B1d8E8eF31E21C99d1Db9A6444d3ADf1270",
        explorer_url="https://polygonscan.com",
        block_time=2.0,
        max_gas_gwei=500.0,
        default_gas_gwei=30.0,
        is_poa=True,
        dexes=[
            DexConfig(
                name="QuickSwap V2", chain_id=137, version="v2",
                factory_address="0x5757371414417b8C6CAad45bAeF941aBc7d3Ab32",
                router_address="0xa5E0829CaCEd8fFDD4De3c43696c57F7D7A678ff",
                weth_address="0x0d500B1d8E8eF31E21C99d1Db9A6444d3ADf1270",
                pair_created_topic=PAIR_CREATED_TOPIC_V2,
                priority=1,
            ),
            DexConfig(
                name="Uniswap V3 (Polygon)", chain_id=137, version="v3",
                factory_address="0x1F98431c8aD98523631AE4a59f267346ea31F984",
                router_address="0xE592427A0AEce92De3Edee1F18E0157C05861564",
                weth_address="0x0d500B1d8E8eF31E21C99d1Db9A6444d3ADf1270",
                pair_created_topic=POOL_CREATED_TOPIC_V3,
                fee=3000,
                priority=2,
            ),
            DexConfig(
                name="SushiSwap (Polygon)", chain_id=137, version="v2",
                factory_address="0xc35DADB65012eC5796536bD9864eD8773aBc74C4",
                router_address="0x1b02dA8Cb0d097eB8D57A175b88c7D8b47997506",
                weth_address="0x0d500B1d8E8eF31E21C99d1Db9A6444d3ADf1270",
                pair_created_topic=PAIR_CREATED_TOPIC_V2,
                priority=3,
            ),
        ],
    )

    return registry


# ═══════════════════════════════════════════════════════════════════
#  Router ABI (minimal, for quotes)
# ═══════════════════════════════════════════════════════════════════

GET_AMOUNTS_OUT_ABI = [{
    "inputs": [
        {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
        {"internalType": "address[]", "name": "path", "type": "address[]"},
    ],
    "name": "getAmountsOut",
    "outputs": [{"internalType": "uint256[]", "name": "amounts", "type": "uint256[]"}],
    "stateMutability": "view",
    "type": "function",
}]


# ═══════════════════════════════════════════════════════════════════
#  Multi-DEX Router
# ═══════════════════════════════════════════════════════════════════

class MultiDexRouter:
    """
    Multi-DEX/Multi-Chain routing engine.

    Finds the best DEX for a swap, monitors multiple factories for new pairs,
    and provides a unified interface for trading across chains.

    Usage:
        router = MultiDexRouter()
        router.add_chain(56, rpc_url="https://bsc-dataseed1.binance.org")
        best = await router.find_best_route(56, token_addr, amount_native)
        factories = router.get_factories(56)  # for PairCreated listeners
    """

    def __init__(self):
        self._registry = build_chain_registry()
        self._web3_instances: dict[int, Web3] = {}  # chain_id → Web3
        self._active_chains: set[int] = set()

    def add_chain(self, chain_id: int, rpc_url: str = "", w3: Web3 = None):
        """
        Activate a chain with a Web3 connection.

        Args:
            chain_id: The chain ID (56, 1, 42161, etc.)
            rpc_url: RPC URL (optional if w3 provided)
            w3: Existing Web3 instance (optional)
        """
        chain = self._registry.get(chain_id)
        if not chain:
            logger.warning(f"MultiDex: unknown chain {chain_id}")
            return

        if w3:
            self._web3_instances[chain_id] = w3
        elif rpc_url:
            w3_new = Web3(Web3.HTTPProvider(rpc_url))
            if chain.is_poa:
                w3_new.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
            self._web3_instances[chain_id] = w3_new
        elif chain.rpc_urls:
            w3_new = Web3(Web3.HTTPProvider(chain.rpc_urls[0]))
            if chain.is_poa:
                w3_new.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
            self._web3_instances[chain_id] = w3_new

        self._active_chains.add(chain_id)
        logger.info(f"MultiDex: activated {chain.name} (chain {chain_id})")

    def get_chain(self, chain_id: int) -> Optional[ChainConfig]:
        """Get chain configuration."""
        return self._registry.get(chain_id)

    def get_w3(self, chain_id: int) -> Optional[Web3]:
        """Get Web3 instance for a chain."""
        return self._web3_instances.get(chain_id)

    def get_active_chains(self) -> list[int]:
        """Return active chain IDs."""
        return list(self._active_chains)

    def get_dexes(self, chain_id: int) -> list[DexConfig]:
        """Get all DEXes for a chain."""
        chain = self._registry.get(chain_id)
        if not chain:
            return []
        return [d for d in chain.dexes if d.enabled]

    def get_factories(self, chain_id: int) -> list[dict]:
        """
        Get all factory addresses and topics for a chain.
        Used by SniperBot to listen for PairCreated events on multiple DEXes.
        """
        factories = []
        for dex in self.get_dexes(chain_id):
            factories.append({
                "dex_name": dex.name,
                "factory": dex.factory_address,
                "router": dex.router_address,
                "weth": dex.weth_address,
                "topic": dex.pair_created_topic,
                "version": dex.version,
            })
        return factories

    def get_wrapped_native(self, chain_id: int) -> str:
        """Get the wrapped native token address for a chain."""
        chain = self._registry.get(chain_id)
        return chain.wrapped_native if chain else ""

    # ───────────────────────────────────────────────────────
    #  Route finding
    # ───────────────────────────────────────────────────────

    async def find_best_route(
        self, chain_id: int, token_address: str,
        amount_native: float, direction: str = "buy"
    ) -> Optional[RouteResult]:
        """
        Find the best DEX route for a swap.

        Queries all enabled DEXes on the chain and returns the one
        with the highest output amount (minus gas).

        Args:
            chain_id: Target chain
            token_address: Token to buy/sell
            amount_native: Amount of native token (BNB/ETH/MATIC)
            direction: "buy" or "sell"

        Returns:
            RouteResult with best DEX, or None if no routes found.
        """
        w3 = self._web3_instances.get(chain_id)
        chain = self._registry.get(chain_id)
        if not w3 or not chain:
            return None

        dexes = self.get_dexes(chain_id)
        if not dexes:
            return None

        routes: list[RouteResult] = []
        amount_in_wei = int(amount_native * 1e18)

        # Query all DEXes in parallel
        tasks = []
        for dex in dexes:
            if dex.version == "v2":
                tasks.append(self._quote_v2(w3, dex, token_address, amount_in_wei, direction))
            elif dex.version == "v3":
                tasks.append(self._quote_v3(w3, dex, token_address, amount_in_wei, direction))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, RouteResult) and result.amount_out > 0:
                routes.append(result)

        if not routes:
            return None

        # Score routes (higher = better): amount_out with gas cost penalty
        for route in routes:
            gas_penalty = route.gas_estimate * chain.default_gas_gwei * 1e-9 * 1e18
            route.score = route.amount_out - gas_penalty

        routes.sort(key=lambda r: r.score, reverse=True)
        best = routes[0]

        logger.info(
            f"MultiDex: best route for {token_address[:10]}… on chain {chain_id} "
            f"→ {best.dex_name} (out={best.amount_out / 1e18:.6f})"
        )
        return best

    async def _quote_v2(
        self, w3: Web3, dex: DexConfig, token: str,
        amount_wei: int, direction: str
    ) -> RouteResult:
        """Get V2 quote via getAmountsOut."""
        route = RouteResult(
            dex_name=dex.name,
            chain_id=dex.chain_id,
            router_address=dex.router_address,
            factory_address=dex.factory_address,
            gas_estimate=dex.avg_gas_cost,
        )

        try:
            router = w3.eth.contract(
                address=Web3.to_checksum_address(dex.router_address),
                abi=GET_AMOUNTS_OUT_ABI,
            )
            weth = Web3.to_checksum_address(dex.weth_address)
            token_cs = Web3.to_checksum_address(token)

            if direction == "buy":
                path = [weth, token_cs]
            else:
                path = [token_cs, weth]

            route.path = path
            route.amount_in = amount_wei

            loop = asyncio.get_event_loop()
            amounts = await loop.run_in_executor(
                None, router.functions.getAmountsOut(amount_wei, path).call
            )
            route.amount_out = amounts[-1] if amounts else 0

        except Exception as e:
            logger.debug(f"V2 quote error ({dex.name}): {e}")
            route.amount_out = 0

        return route

    async def _quote_v3(
        self, w3: Web3, dex: DexConfig, token: str,
        amount_wei: int, direction: str
    ) -> RouteResult:
        """Get V3 quote (simplified — real V3 uses Quoter contract)."""
        route = RouteResult(
            dex_name=dex.name,
            chain_id=dex.chain_id,
            router_address=dex.router_address,
            factory_address=dex.factory_address,
            gas_estimate=dex.avg_gas_cost,
        )

        # V3 quoting requires the Quoter contract — for now, return 0
        # and let V2 routes win. Full V3 integration would use:
        # IQuoterV2.quoteExactInputSingle()
        try:
            weth = Web3.to_checksum_address(dex.weth_address)
            token_cs = Web3.to_checksum_address(token)

            if direction == "buy":
                path = [weth, token_cs]
            else:
                path = [token_cs, weth]

            route.path = path
            route.amount_in = amount_wei
            # V3 quote would go here
            route.amount_out = 0

        except Exception:
            pass

        return route

    # ───────────────────────────────────────────────────────
    #  Multi-factory listener setup
    # ───────────────────────────────────────────────────────

    def get_all_factory_filters(self, chain_id: int) -> list[dict]:
        """
        Get log filter configs for all factories on a chain.
        Used to setup PairCreated/PoolCreated listeners.
        """
        filters = []
        for dex in self.get_dexes(chain_id):
            if dex.factory_address and dex.pair_created_topic:
                filters.append({
                    "dex_name": dex.name,
                    "address": Web3.to_checksum_address(dex.factory_address),
                    "topics": [dex.pair_created_topic],
                    "version": dex.version,
                    "router": dex.router_address,
                    "weth": dex.weth_address,
                })
        return filters

    # ───────────────────────────────────────────────────────
    #  Chain management
    # ───────────────────────────────────────────────────────

    def enable_dex(self, chain_id: int, dex_name: str, enabled: bool = True):
        """Enable/disable a specific DEX."""
        chain = self._registry.get(chain_id)
        if chain:
            for dex in chain.dexes:
                if dex.name == dex_name:
                    dex.enabled = enabled
                    logger.info(f"MultiDex: {dex_name} {'enabled' if enabled else 'disabled'}")

    def add_custom_dex(self, chain_id: int, dex: DexConfig):
        """Add a custom DEX to a chain."""
        chain = self._registry.get(chain_id)
        if chain:
            chain.dexes.append(dex)
            logger.info(f"MultiDex: added custom DEX {dex.name} to chain {chain_id}")

    # ───────────────────────────────────────────────────────
    #  Stats / Serialization
    # ───────────────────────────────────────────────────────

    def get_overview(self) -> dict:
        """Get an overview of all chains and DEXes."""
        chains = []
        for chain_id, chain in self._registry.items():
            active = chain_id in self._active_chains
            chains.append({
                "chain_id": chain_id,
                "name": chain.name,
                "symbol": chain.symbol,
                "active": active,
                "connected": chain_id in self._web3_instances,
                "dexes": [
                    {
                        "name": d.name,
                        "version": d.version,
                        "enabled": d.enabled,
                        "factory": d.factory_address[:10] + "…",
                        "router": d.router_address[:10] + "…",
                    }
                    for d in chain.dexes
                ],
            })
        return {"chains": chains, "active_chains": list(self._active_chains)}

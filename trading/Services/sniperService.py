"""
sniperService.py — Core sniper bot service.

Architecture (Professional v7):
  mempool listener → pre-launch detector → token detector → contract analyzer
  → swap simulator → pump analyzer → smart money tracker → dev tracker
  → risk engine → trade executor → rug detector → profit manager
  → resource monitor → alert service → metrics service
  → proxy detector → stress tester → volatility slippage
  → ML predictor → social sentiment → dynamic contract scanner
  → whale activity tracker → anomaly detector
  → MEV protector → multi-DEX router → copy trader → AI optimizer
  → RL learner → orderflow analyzer → market simulator
  → auto strategy → launch scanner → mempool v7 → whale graph

Modules:
  pumpAnalyzer.py      — Advanced pump score engine v3 (0–100, 10 components)
  swapSimulator.py     — On-chain honeypot detection + proxy/stress/volatility (v5)
  mempoolService.py    — Mempool pending tx listener
  rugDetector.py       — Post-buy rug pull monitoring
  preLaunchDetector.py — Pre-launch token detection
  smartMoneyTracker.py — Whale wallet tracking + whale activity analysis (v5)
  devTracker.py        — Developer reputation tracker + ML reputation (v5)
  riskEngine.py        — Unified risk scoring engine (v3)
  tradeExecutor.py     — Backend trade execution engine (v3)
  resourceMonitor.py   — Memory/CPU/WS/RPC monitoring (v4)
  alertService.py      — Telegram/Discord/Email alerts (v4)
  metricsService.py    — Performance metrics & P&L tracking (v4)
  mlPredictor.py       — ML pump/dump prediction + anomaly detection (v5)
  socialSentiment.py   — Multi-platform social sentiment analysis (v5)
  dynamicContractScanner.py — Continuous contract monitoring (v5)
  backtestEngine.py    — Historical backtesting engine (v6)
  copyTrader.py        — Whale copy trading system (v6)
  mevProtection.py     — MEV protection + v2 adaptive gas (v6)
  multiDexRouter.py    — Multi-DEX routing + Solana/Avalanche (v6)
  strategyOptimizer.py — AI strategy optimization (v6)
  reinforcementLearner.py    — RL + Bayesian + bandit decision engine (NEW v7)
  orderflowAnalyzer.py       — Orderflow analysis + bot/manipulation detection (NEW v7)
  marketSimulator.py         — Full market simulation (AMM + MEV + MC) (NEW v7)
  autoStrategyGenerator.py   — Genetic algorithm strategy evolution (NEW v7)
  predictiveLaunchScanner.py — Pre-mempool contract detection (NEW v7)
  mempoolAnalyzer.py         — Advanced mempool analysis (NEW v7)
  whaleNetworkGraph.py       — Wallet network graph analysis (NEW v7)

Uses web3.py to interact with BSC (or other EVM chains + Solana) via WebSocket RPC.
Runs as an async background task inside Django Channels.
"""

import asyncio
import json
import time
import logging
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional

from web3 import Web3, AsyncWeb3
from web3.providers import WebSocketProvider
import aiohttp

# ── Professional modules ──
from trading.Services.pumpAnalyzer import PumpAnalyzer, PumpScore
from trading.Services.swapSimulator import SwapSimulator, BytecodeAnalyzer, SimulationResult
from trading.Services.mempoolService import MempoolListener, MempoolEvent
from trading.Services.rugDetector import RugDetector, RugAlert
from trading.Services.preLaunchDetector import PreLaunchDetector, PreLaunchToken
from trading.Services.smartMoneyTracker import SmartMoneyTracker, SmartMoneySignal
# ── Professional v3 modules ──
from trading.Services.devTracker import DevTracker, DevCheckResult
from trading.Services.riskEngine import RiskEngine, RiskDecision
from trading.Services.tradeExecutor import TradeExecutor, TradeResult
# ── Professional v4 modules ──
from trading.Services.resourceMonitor import ResourceMonitor
from trading.Services.alertService import AlertService
from trading.Services.metricsService import MetricsService, TradeRecord, DetectionEvent
# ── Professional v5 modules ──
from trading.Services.swapSimulator import ProxyDetector, StressTester, VolatilitySlippageCalc
from trading.Services.mlPredictor import PumpDumpPredictor, DevReputationML, AnomalyDetector
from trading.Services.socialSentiment import SocialSentimentAnalyzer
from trading.Services.dynamicContractScanner import DynamicContractScanner
from trading.Services.smartMoneyTracker import WhaleAlert, WhaleActivity
# ── Professional v6 modules ──
from trading.Services.backtestEngine import BacktestEngine, BacktestConfig, BacktestResult
from trading.Services.copyTrader import CopyTrader
from trading.Services.mevProtection import MEVProtector, MEVAnalysis
from trading.Services.multiDexRouter import MultiDexRouter
from trading.Services.strategyOptimizer import StrategyOptimizer, TradeOutcome
# ── Professional v7 modules ──
from trading.Services.reinforcementLearner import ReinforcementLearner
from trading.Services.orderflowAnalyzer import OrderflowAnalyzer
from trading.Services.marketSimulator import MarketSimulator
from trading.Services.autoStrategyGenerator import AutoStrategyGenerator
from trading.Services.predictiveLaunchScanner import PredictiveLaunchScanner
from trading.Services.mempoolAnalyzer import MempoolAnalyzer
from trading.Services.whaleNetworkGraph import WhaleNetworkGraph
from trading.Services.apiResilience import ResilientAPIManager, APICallMetric

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
#  Constants & ABIs
# ═══════════════════════════════════════════════════════════════════

# Public RPC endpoints that support eth_getLogs (free, no API key)
# Multiple endpoints for failover — some public RPCs rate-limit getLogs.
RPC_ENDPOINTS = {
    56: {
        "http": "https://bsc-rpc.publicnode.com",
        "ws":   "wss://bsc-ws-node.nariox.org:443",
        "name": "BSC Mainnet",
    },
    1: {
        "http": "https://eth.llamarpc.com",
        "ws":   "wss://ethereum-rpc.publicnode.com",
        "name": "Ethereum",
    },
}

# Fallback RPC list per chain (rotated on errors)
# Only RPCs that support eth_getLogs with topic filters
RPC_FALLBACKS = {
    56: [
        "https://bsc-rpc.publicnode.com",
        "https://binance.llamarpc.com",
        "https://bsc-pokt.nodies.app",
        "https://bsc.meowrpc.com",
        "https://bsc.drpc.org",
        "https://bsc-dataseed1.binance.org",
        "https://bsc-dataseed2.binance.org",
        "https://bsc-dataseed3.binance.org",
        "https://bsc-dataseed1.defibit.io",
        "https://bsc-dataseed1.ninicoin.io",
    ],
    1: [
        "https://ethereum-rpc.publicnode.com",
        "https://eth.llamarpc.com",
        "https://eth.drpc.org",
        "https://eth.meowrpc.com",
        "https://rpc.ankr.com/eth",
    ],
}

# Rate-limit backoff settings
RPC_BACKOFF_BASE = 2.0       # seconds — initial backoff after 429
RPC_BACKOFF_MAX  = 30.0      # seconds — max backoff per 429 cycle
RPC_BACKOFF_DECAY = 0.8      # multiplier to reduce backoff after a success

# PancakeSwap / Uniswap V2 Factory addresses
FACTORY_ADDRESSES = {
    56: "0xcA143Ce32Fe78f1f7019d7d551a6402fC5350c73",   # PancakeSwap V2
    1:  "0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f",   # Uniswap V2
}

# Common Router addresses for swaps
ROUTER_ADDRESSES = {
    56: "0x10ED43C718714eb63d5aA57B78B54704E256024E",   # PancakeSwap V2
    1:  "0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D",   # Uniswap V2
}

# Wrapped native token
WETH_ADDRESSES = {
    56: "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c",   # WBNB
    1:  "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",   # WETH
}

# PairCreated event signature (must have 0x prefix for RPC calls)
PAIR_CREATED_TOPIC = "0x" + Web3.keccak(
    text="PairCreated(address,address,address,uint256)"
).hex()

# Sync event topic — emitted by V2 pairs on every swap/mint/burn
# Contains reserve0 & reserve1 directly in log data (no extra RPC call!)
SYNC_TOPIC = "0x" + Web3.keccak(
    text="Sync(uint112,uint112)"
).hex()

# WebSocket endpoints that support eth_subscribe
WS_ENDPOINTS = {
    56: [
        "wss://bsc-rpc.publicnode.com",
        "wss://bsc.drpc.org",
    ],
    1: [
        "wss://ethereum-rpc.publicnode.com",
        "wss://eth.drpc.org",
    ],
}

# Minimal ERC-20 ABI for analysis
ERC20_ABI = json.loads("""[
    {"constant":true,"inputs":[],"name":"name","outputs":[{"name":"","type":"string"}],"type":"function"},
    {"constant":true,"inputs":[],"name":"symbol","outputs":[{"name":"","type":"string"}],"type":"function"},
    {"constant":true,"inputs":[],"name":"decimals","outputs":[{"name":"","type":"uint8"}],"type":"function"},
    {"constant":true,"inputs":[],"name":"totalSupply","outputs":[{"name":"","type":"uint256"}],"type":"function"},
    {"constant":true,"inputs":[{"name":"","type":"address"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"type":"function"},
    {"constant":true,"inputs":[],"name":"owner","outputs":[{"name":"","type":"address"}],"type":"function"},
    {"constant":true,"inputs":[{"name":"sender","type":"address"},{"name":"recipient","type":"address"},{"name":"amount","type":"uint256"}],"name":"transfer","outputs":[{"name":"","type":"bool"}],"type":"function"}
]""")

# PancakeSwap/Uniswap V2 Pair ABI (minimal)
PAIR_ABI = json.loads("""[
    {"constant":true,"inputs":[],"name":"getReserves","outputs":[{"name":"_reserve0","type":"uint112"},{"name":"_reserve1","type":"uint112"},{"name":"_blockTimestampLast","type":"uint32"}],"type":"function"},
    {"constant":true,"inputs":[],"name":"token0","outputs":[{"name":"","type":"address"}],"type":"function"},
    {"constant":true,"inputs":[],"name":"token1","outputs":[{"name":"","type":"address"}],"type":"function"}
]""")

# Factory ABI (getPair + PairCreated event)
FACTORY_ABI = json.loads("""[
    {"constant":true,"inputs":[{"name":"tokenA","type":"address"},{"name":"tokenB","type":"address"}],"name":"getPair","outputs":[{"name":"pair","type":"address"}],"type":"function"},
    {"anonymous":false,"inputs":[{"indexed":true,"name":"token0","type":"address"},{"indexed":true,"name":"token1","type":"address"},{"indexed":false,"name":"pair","type":"address"},{"indexed":false,"name":"","type":"uint256"}],"name":"PairCreated","type":"event"}
]""")

# Router ABI for swaps
ROUTER_ABI = json.loads("""[
    {"inputs":[{"name":"amountOutMin","type":"uint256"},{"name":"path","type":"address[]"},{"name":"to","type":"address"},{"name":"deadline","type":"uint256"}],"name":"swapExactETHForTokensSupportingFeeOnTransferTokens","outputs":[],"stateMutability":"payable","type":"function"},
    {"inputs":[{"name":"amountIn","type":"uint256"},{"name":"amountOutMin","type":"uint256"},{"name":"path","type":"address[]"},{"name":"to","type":"address"},{"name":"deadline","type":"uint256"}],"name":"swapExactTokensForETHSupportingFeeOnTransferTokens","outputs":[],"stateMutability":"nonpayable","type":"function"},
    {"inputs":[{"name":"amountIn","type":"uint256"},{"name":"path","type":"address[]"}],"name":"getAmountsOut","outputs":[{"name":"amounts","type":"uint256[]"}],"stateMutability":"view","type":"function"}
]""")


# ═══════════════════════════════════════════════════════════════════
#  Data classes
# ═══════════════════════════════════════════════════════════════════

class TokenRisk(Enum):
    SAFE = "safe"
    WARNING = "warning"
    DANGER = "danger"
    UNKNOWN = "unknown"


@dataclass
class TokenInfo:
    address: str
    name: str = "?"
    symbol: str = "?"
    decimals: int = 18
    total_supply: float = 0
    # Analysis results
    has_owner: bool = False
    owner_address: str = ""
    is_honeypot: bool = False
    buy_tax: float = 0
    sell_tax: float = 0
    # ── Security flags (GoPlus + honeypot.is) ──
    has_blacklist: bool = False
    is_mintable: bool = False
    can_pause_trading: bool = False
    is_proxy: bool = False
    has_hidden_owner: bool = False
    can_self_destruct: bool = False
    has_external_call: bool = False
    cannot_sell_all: bool = False
    owner_can_change_balance: bool = False
    has_trading_cooldown: bool = False
    personal_slippage_modifiable: bool = False
    is_anti_whale: bool = False
    is_open_source: bool = False
    can_take_back_ownership: bool = False   # owner renounced but can reclaim
    is_airdrop_scam: bool = False           # GoPlus airdrop scam detection
    is_true_token: bool = True              # true ERC-20 or fake interface
    lp_holder_count: int = 0
    holder_count: int = 0
    top_holder_percent: float = 0           # % supply held by top non-LP holder
    creator_percent: float = 0              # % supply held by creator
    creator_address: str = ""
    # ── Cross-platform verification ──
    listed_coingecko: bool = False
    coingecko_id: str = ""
    dexscreener_pairs: int = 0
    dexscreener_volume_24h: float = 0
    dexscreener_liquidity: float = 0
    dexscreener_buys_24h: int = 0
    dexscreener_sells_24h: int = 0
    dexscreener_age_hours: float = 0
    has_social_links: bool = False
    has_website: bool = False
    tokensniffer_score: int = -1        # 0–100, -1 = no data
    tokensniffer_is_scam: bool = False
    # ── LP Lock info (from GoPlus) ──
    lp_locked: bool = False
    lp_lock_percent: float = 0          # % of LP tokens locked
    lp_lock_hours_remaining: float = 0  # hours until lock expires
    lp_lock_end_timestamp: float = 0    # unix timestamp
    lp_lock_source: str = ""            # eg PinkLock, Unicrypt
    # ── Price history (DexScreener) ──
    dexscreener_price_change_m5: float = 0
    dexscreener_price_change_h1: float = 0
    dexscreener_price_change_h6: float = 0
    dexscreener_price_change_h24: float = 0
    # ── Pump Score (PumpAnalyzer) ──
    pump_score: int = 0                  # 0–100 overall pump potential
    pump_grade: str = ""                 # HIGH / MEDIUM / LOW / AVOID
    pump_signals: list = field(default_factory=list)
    # ── Swap Simulation (SwapSimulator) ──
    sim_can_buy: bool = False
    sim_can_sell: bool = False
    sim_buy_tax: float = 0
    sim_sell_tax: float = 0
    sim_is_honeypot: bool = False
    sim_honeypot_reason: str = ""
    sim_gas_buy: int = 0
    sim_gas_sell: int = 0
    _simulation_ok: bool = False
    # ── Bytecode Analysis ──
    bytecode_has_selfdestruct: bool = False
    bytecode_has_delegatecall: bool = False
    bytecode_is_proxy: bool = False
    bytecode_size: int = 0
    bytecode_flags: list = field(default_factory=list)
    _bytecode_ok: bool = False
    # ── Smart Money ──
    smart_money_buyers: int = 0
    smart_money_confidence: int = 0
    # ── v3: Dev Tracker ──
    dev_is_tracked: bool = False
    dev_score: int = 0
    dev_label: str = ""
    dev_total_launches: int = 0
    dev_successful_launches: int = 0
    dev_rug_pulls: int = 0
    dev_best_multiplier: float = 0.0
    dev_is_serial_scammer: bool = False
    _dev_ok: bool = False
    # ── v3: Risk Engine ──
    risk_engine_score: int = 0
    risk_engine_action: str = ""           # STRONG_BUY / BUY / WATCH / WEAK / IGNORE
    risk_engine_confidence: float = 0.0
    risk_engine_hard_stop: bool = False
    risk_engine_hard_stop_reason: str = ""
    risk_engine_signals: list = field(default_factory=list)
    _risk_engine_ok: bool = False
    # ── v3: Trade Executor ──
    backend_buy_available: bool = False
    # ── v3: Pump Analyzer extras ──
    pump_mcap_score: int = 0
    pump_market_cap_usd: float = 0.0
    pump_holder_growth_rate: float = 0.0
    pump_lp_growth_percent: float = 0.0
    pump_holder_growth_score: int = 0
    pump_lp_growth_score: int = 0
    # API tracking — did we actually get data?
    _goplus_ok: bool = False
    _honeypot_ok: bool = False
    _dexscreener_ok: bool = False
    _coingecko_ok: bool = False
    _tokensniffer_ok: bool = False
    # ── v5: Proxy Detection ──
    proxy_is_proxy: bool = False
    proxy_type: str = ""                    # transparent / uups / beacon / eip1167 / none
    proxy_risk_level: str = "unknown"       # safe / moderate / dangerous
    proxy_has_multisig: bool = False
    proxy_has_timelock: bool = False
    proxy_signals: list = field(default_factory=list)
    _proxy_ok: bool = False
    # ── v5: Stress Test ──
    stress_max_safe_amount: float = 0.0     # max BNB/ETH for <10% slippage
    stress_liquidity_depth: int = 0         # 0-100
    stress_avg_slippage: float = 0.0
    _stress_ok: bool = False
    # ── v5: Volatility Slippage ──
    volatility_score: float = 0.0
    volatility_recommended_slippage: float = 12.0
    _volatility_ok: bool = False
    # ── v5: ML Pump/Dump Predictor ──
    ml_pump_score: int = 0                  # 0-100
    ml_pump_confidence: float = 0.0         # 0-1
    ml_pump_label: str = "neutral"          # safe / neutral / warning / danger
    _ml_pump_ok: bool = False
    # ── v5: Social Sentiment ──
    social_sentiment_score: int = 0         # 0-100
    social_sentiment_label: str = ""        # strong / moderate / weak / none / suspicious
    social_platforms_active: int = 0
    social_signals: list = field(default_factory=list)
    _social_ok: bool = False
    # ── v5: Anomaly Detection ──
    anomaly_is_anomalous: bool = False
    anomaly_score: float = 0.0              # 0-1
    anomaly_type: str = ""
    _anomaly_ok: bool = False
    # ── v5: Whale Activity ──
    whale_total_buys: int = 0
    whale_total_sells: int = 0
    whale_net_flow: float = 0.0
    whale_coordinated: bool = False
    whale_dev_dumping: bool = False
    whale_concentration_pct: float = 0.0
    whale_risk_score: int = 50
    whale_signals: list = field(default_factory=list)
    _whale_ok: bool = False
    # ── v5: Dynamic Contract Scanner ──
    dynamic_scan_risk: int = 0
    dynamic_scan_blocked: bool = False
    dynamic_scan_block_reason: str = ""
    dynamic_scan_alerts: list = field(default_factory=list)
    _dynamic_scan_ok: bool = False
    # ── v6: MEV Protection ──
    mev_threat_level: str = "unknown"
    mev_strategy_used: str = "none"
    mev_frontrun_risk: float = 0.0
    mev_sandwich_risk: float = 0.0
    _mev_ok: bool = False
    # ── v6: Multi-DEX ──
    best_dex_name: str = ""
    best_dex_amount_out: float = 0.0
    best_dex_route: list = field(default_factory=list)
    _multi_dex_ok: bool = False
    # ── v6: Copy Trading ──
    copy_trade_signal: bool = False
    copy_trade_wallet: str = ""
    copy_trade_confidence: int = 0
    # ── v6: AI Optimizer ──
    ai_regime: str = "unknown"
    ai_suggested_tp: float = 0.0
    ai_suggested_sl: float = 0.0
    # ── v5: Dev ML ──
    dev_ml_reputation: int = 50
    dev_ml_cluster: str = "neutral"
    dev_ml_confidence: float = 0.0
    # ── v7: Reinforcement Learning ──
    rl_decision: str = "hold"              # buy / sell / hold
    rl_confidence: float = 0.0             # 0-1
    rl_action: str = ""                    # Q-learning action name
    rl_suggested_tp: float = 0.0
    rl_suggested_sl: float = 0.0
    rl_suggested_amount: float = 0.0
    _rl_ok: bool = False
    # ── v7: Orderflow Analysis ──
    orderflow_organic_score: float = 0.0    # 0-100
    orderflow_bot_pct: float = 0.0          # 0-100%
    orderflow_manipulation_risk: float = 0.0  # 0-1
    orderflow_patterns: list = field(default_factory=list)
    _orderflow_ok: bool = False
    # ── v7: Market Simulator ──
    sim_v7_risk_score: float = 0.0          # 0-100 overall sim risk
    sim_v7_recommendation: str = "unknown"  # buy / avoid / wait
    sim_v7_slippage_pct: float = 0.0
    sim_v7_mev_vulnerable: bool = False
    sim_v7_expected_pnl: float = 0.0
    _sim_v7_ok: bool = False
    # ── v7: Auto Strategy Generator ──
    strategy_decision: str = "none"         # buy / pass / watch
    strategy_confidence: float = 0.0        # 0-1
    strategy_name: str = ""                 # name of best matching strategy
    _strategy_ok: bool = False
    # ── v7: Predictive Launch Scanner ──
    launch_risk_score: int = 50
    launch_stage: str = "unknown"
    launch_snipe_priority: int = 0          # 0-100
    _launch_ok: bool = False
    # ── v7: Mempool Analyzer ──
    mempool_v7_net_pressure: float = 0.0    # positive=buying, negative=selling
    mempool_v7_frontrun_risk: float = 0.0   # 0-1
    mempool_v7_pending_buys: int = 0
    mempool_v7_pending_sells: int = 0
    mempool_v7_alerts: list = field(default_factory=list)
    _mempool_v7_ok: bool = False
    # ── v7: Whale Network Graph ──
    whale_network_coordination: float = 0.0   # 0-1
    whale_network_sybil_risk: float = 0.0     # 0-1
    whale_smart_money_sentiment: str = "neutral"  # bullish / bearish / neutral
    whale_network_clusters: int = 0
    _whale_network_ok: bool = False
    # Overall
    risk: str = "unknown"
    risk_reasons: list = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


@dataclass
class NewPair:
    pair_address: str
    token0: str
    token1: str
    new_token: str      # whichever isn't WBNB/WETH
    base_token: str     # WBNB or WETH
    chain_id: int
    block_number: int = 0
    timestamp: float = 0
    liquidity_usd: float = 0
    liquidity_native: float = 0
    token_info: Optional[TokenInfo] = None

    def to_dict(self):
        d = asdict(self)
        if self.token_info:
            d["token_info"] = self.token_info.to_dict()
        return d


@dataclass
class ActiveSnipe:
    """Track an active position after buying."""
    token_address: str
    symbol: str
    chain_id: int
    buy_price_usd: float = 0
    buy_amount_native: float = 0
    buy_amount_tokens: float = 0
    buy_tx: str = ""
    current_price_usd: float = 0
    pnl_percent: float = 0
    take_profit: float = 40      # % to auto-sell
    stop_loss: float = 20        # % to auto-sell
    status: str = "active"       # active | sold | stopped
    sell_tx: str = ""
    timestamp: float = 0
    max_hold_hours: float = 0       # 0 = disabled

    def to_dict(self):
        return asdict(self)


# ═══════════════════════════════════════════════════════════════════
#  Contract Analyzer
# ═══════════════════════════════════════════════════════════════════

class ContractAnalyzer:
    """Analyze a token contract for scam indicators using multiple APIs."""

    def __init__(self, w3: Web3, chain_id: int, session: aiohttp.ClientSession | None = None,
                 api_manager: ResilientAPIManager | None = None):
        self.w3 = w3
        self.chain_id = chain_id
        self._session = session   # shared session for connection pooling
        self.api_manager = api_manager or ResilientAPIManager()

    async def analyze(self, token_address: str) -> TokenInfo:
        """Full analysis of a token contract."""
        info = TokenInfo(address=token_address)

        try:
            cs_addr = Web3.to_checksum_address(token_address)
            contract = self.w3.eth.contract(address=cs_addr, abi=ERC20_ABI)
            loop = asyncio.get_event_loop()

            # Basic info — run ALL RPC calls in parallel to avoid sequential waits
            async def _safe(coro, default=None):
                try:
                    return await coro
                except Exception:
                    return default

            name_t  = _safe(loop.run_in_executor(None, contract.functions.name().call), "Unknown")
            sym_t   = _safe(loop.run_in_executor(None, contract.functions.symbol().call), "???")
            dec_t   = _safe(loop.run_in_executor(None, contract.functions.decimals().call), 18)
            sup_t   = _safe(loop.run_in_executor(None, contract.functions.totalSupply().call), None)
            own_t   = _safe(loop.run_in_executor(None, contract.functions.owner().call), None)

            name, symbol, decimals, raw_supply, owner = await asyncio.gather(
                name_t, sym_t, dec_t, sup_t, own_t
            )

            info.name = name
            info.symbol = symbol
            info.decimals = decimals
            if raw_supply is not None:
                info.total_supply = raw_supply / (10 ** info.decimals)
            if owner is not None:
                info.owner_address = owner
                info.has_owner = owner != "0x0000000000000000000000000000000000000000"
            else:
                info.has_owner = False

            # Run API checks in parallel through resilience manager
            # (circuit breakers, caching, retries, health monitoring)
            addr = info.address.lower()
            await asyncio.gather(
                self._resilient_check_honeypot(info, addr),
                self._resilient_check_goplus(info, addr),
                self._resilient_check_dexscreener(info, addr),
                self._resilient_check_coingecko(info, addr),
                self._resilient_check_tokensniffer(info, addr),
                return_exceptions=True,
            )

            # Determine risk level
            self._calculate_risk(info)

        except Exception as e:
            logger.warning(f"Contract analysis failed for {token_address}: {e}")
            info.risk = "unknown"
            info.risk_reasons.append(f"Analysis error: {str(e)[:80]}")

        return info

    async def _get_session(self) -> aiohttp.ClientSession:
        """Return the shared session or create a temporary one."""
        if self._session and not self._session.closed:
            return self._session
        return aiohttp.ClientSession()

    # ─── Resilient API wrappers (circuit breaker + cache + retry) ─────

    async def _resilient_check_honeypot(self, info: TokenInfo, addr: str):
        """Honeypot.is through resilience manager."""
        cached = self.api_manager.cache.get(f"honeypot:{addr}")
        if cached:
            self._apply_honeypot_data(info, cached)
            info._honeypot_ok = True
            self.api_manager.health.record(APICallMetric(
                api_name="honeypot", success=True, latency_ms=0,
                timestamp=time.time(), cached=True))
            return
        result = await self.api_manager.call(
            "honeypot",
            lambda: self._fetch_honeypot_raw(info.address),
            cache_key=f"honeypot:{addr}",
            cache_ttl=120,
        )
        if result:
            self._apply_honeypot_data(info, result)
            info._honeypot_ok = True

    async def _resilient_check_goplus(self, info: TokenInfo, addr: str):
        """GoPlus through resilience manager."""
        cached = self.api_manager.cache.get(f"goplus:{addr}")
        if cached:
            self._apply_goplus_data(info, cached)
            info._goplus_ok = True
            self.api_manager.health.record(APICallMetric(
                api_name="goplus", success=True, latency_ms=0,
                timestamp=time.time(), cached=True))
            return
        result = await self.api_manager.call(
            "goplus",
            lambda: self._fetch_goplus_raw(addr),
            cache_key=f"goplus:{addr}",
            cache_ttl=300,
        )
        if result:
            self._apply_goplus_data(info, result)
            info._goplus_ok = True

    async def _resilient_check_dexscreener(self, info: TokenInfo, addr: str):
        """DexScreener through resilience manager."""
        cached = self.api_manager.cache.get(f"dexscreener:{addr}")
        if cached:
            self._apply_dexscreener_data(info, cached)
            info._dexscreener_ok = True
            self.api_manager.health.record(APICallMetric(
                api_name="dexscreener", success=True, latency_ms=0,
                timestamp=time.time(), cached=True))
            return
        result = await self.api_manager.call(
            "dexscreener",
            lambda: self._fetch_dexscreener_raw(info.address),
            cache_key=f"dexscreener:{addr}",
            cache_ttl=30,
        )
        if result:
            self._apply_dexscreener_data(info, result)
            info._dexscreener_ok = True

    async def _resilient_check_coingecko(self, info: TokenInfo, addr: str):
        """CoinGecko through resilience manager."""
        cached = self.api_manager.cache.get(f"coingecko:{addr}")
        if cached is not None:
            self._apply_coingecko_data(info, cached)
            info._coingecko_ok = True
            self.api_manager.health.record(APICallMetric(
                api_name="coingecko", success=True, latency_ms=0,
                timestamp=time.time(), cached=True))
            return
        result = await self.api_manager.call(
            "coingecko",
            lambda: self._fetch_coingecko_raw(info.address),
            cache_key=f"coingecko:{addr}",
            cache_ttl=600,
        )
        if result is not None:
            self._apply_coingecko_data(info, result)
            info._coingecko_ok = True

    async def _resilient_check_tokensniffer(self, info: TokenInfo, addr: str):
        """TokenSniffer through resilience manager."""
        cached = self.api_manager.cache.get(f"tokensniffer:{addr}")
        if cached:
            self._apply_tokensniffer_data(info, cached)
            info._tokensniffer_ok = True
            self.api_manager.health.record(APICallMetric(
                api_name="tokensniffer", success=True, latency_ms=0,
                timestamp=time.time(), cached=True))
            return
        result = await self.api_manager.call(
            "tokensniffer",
            lambda: self._fetch_tokensniffer_raw(info.address),
            cache_key=f"tokensniffer:{addr}",
            cache_ttl=300,
        )
        if result:
            self._apply_tokensniffer_data(info, result)
            info._tokensniffer_ok = True

    # ─── Raw API fetchers (return parsed dicts, no mutations) ────────

    async def _fetch_honeypot_raw(self, address: str) -> dict | None:
        """Fetch honeypot.is data and return raw parsed dict."""
        url = f"https://api.honeypot.is/v2/IsHoneypot?address={address}&chainID={self.chain_id}"
        session = await self._get_session()
        own = session is not self._session
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status == 200:
                    return await resp.json()
                return None
        finally:
            if own:
                await session.close()

    async def _fetch_goplus_raw(self, addr_lower: str) -> dict | None:
        """Fetch GoPlus data and return the result dict for the address."""
        url = f"https://api.gopluslabs.com/api/v1/token_security/{self.chain_id}?contract_addresses={addr_lower}"
        session = await self._get_session()
        own = session is not self._session
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                result = data.get("result", {}).get(addr_lower, {})
                return result if result else None
        finally:
            if own:
                await session.close()

    async def _fetch_dexscreener_raw(self, address: str) -> dict | None:
        """Fetch DexScreener data and return best-liquidity pair dict."""
        url = f"https://api.dexscreener.com/latest/dex/tokens/{address}"
        session = await self._get_session()
        own = session is not self._session
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                pairs = data.get("pairs") or []
                if not pairs:
                    return None
                best = max(pairs, key=lambda p: float(p.get("liquidity", {}).get("usd", 0) or 0))
                return {"pairs_count": len(pairs), "best": best}
        finally:
            if own:
                await session.close()

    async def _fetch_coingecko_raw(self, address: str) -> dict:
        """Fetch CoinGecko listing status. Returns dict with 'listed' key."""
        platform = "binance-smart-chain" if self.chain_id == 56 else "ethereum"
        url = f"https://api.coingecko.com/api/v3/coins/{platform}/contract/{address.lower()}"
        session = await self._get_session()
        own = session is not self._session
        try:
            headers = {"accept": "application/json"}
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=3)) as resp:
                if resp.status == 404:
                    return {"listed": False, "id": ""}
                if resp.status != 200:
                    raise Exception(f"CoinGecko HTTP {resp.status}")
                data = await resp.json()
                return {"listed": True, "id": data.get("id", "")}
        finally:
            if own:
                await session.close()

    async def _fetch_tokensniffer_raw(self, address: str) -> dict | None:
        """Fetch TokenSniffer data and return parsed dict."""
        url = (f"https://tokensniffer.com/api/v2/tokens/{self.chain_id}/{address.lower()}"
               f"?include_metrics=true&include_tests=true&block_until_ready=false")
        session = await self._get_session()
        own = session is not self._session
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                score = data.get("score")
                if score is None:
                    return None
                return {
                    "score": int(score),
                    "is_flagged": data.get("is_flagged", False),
                }
        finally:
            if own:
                await session.close()

    # ─── Data applicators (apply cached/fresh dict → TokenInfo) ──────

    def _apply_honeypot_data(self, info: TokenInfo, data: dict):
        """Apply honeypot.is API response to TokenInfo."""
        hp = data.get("honeypotResult", {})
        info.is_honeypot = hp.get("isHoneypot", False)
        sim = data.get("simulationResult", {})
        info.buy_tax = sim.get("buyTax", 0)
        info.sell_tax = sim.get("sellTax", 0)
        if info.is_honeypot:
            info.risk_reasons.append("🍯 HONEYPOT — no puedes vender")
        if info.buy_tax > 10:
            info.risk_reasons.append(f"💸 Buy tax alto: {info.buy_tax}%")
        if info.sell_tax > 10:
            info.risk_reasons.append(f"💸 Sell tax alto: {info.sell_tax}%")

    def _apply_goplus_data(self, info: TokenInfo, result: dict):
        """Apply GoPlus API response to TokenInfo."""
        def flag(key):
            return str(result.get(key, "0")) == "1"

        info.is_open_source      = flag("is_open_source")
        info.is_proxy            = flag("is_proxy")
        info.is_mintable         = flag("is_mintable")
        info.has_blacklist       = flag("is_blacklisted")
        info.can_pause_trading   = flag("transfer_pausable")
        info.has_hidden_owner    = flag("hidden_owner")
        info.can_self_destruct   = flag("selfdestruct")
        info.has_external_call   = flag("external_call")
        info.cannot_sell_all     = flag("cannot_sell_all")
        info.owner_can_change_balance = flag("owner_change_balance")
        info.has_trading_cooldown     = flag("trading_cooldown")
        info.personal_slippage_modifiable = flag("personal_slippage_modifiable")
        info.is_anti_whale       = flag("is_anti_whale")
        info.can_take_back_ownership = flag("can_take_back_ownership")
        info.is_airdrop_scam     = flag("is_airdrop_scam")
        info.is_true_token       = flag("is_true_token") if "is_true_token" in result else True

        if flag("is_honeypot") and not info.is_honeypot:
            info.is_honeypot = True
            info.risk_reasons.append("🍯 HONEYPOT (GoPlus)")

        if info.buy_tax == 0:
            try:
                info.buy_tax = round(float(result.get("buy_tax", 0)) * 100, 1)
            except (ValueError, TypeError):
                pass
        if info.sell_tax == 0:
            try:
                info.sell_tax = round(float(result.get("sell_tax", 0)) * 100, 1)
            except (ValueError, TypeError):
                pass

        try:
            info.holder_count = int(result.get("holder_count", 0))
        except (ValueError, TypeError):
            pass
        try:
            info.lp_holder_count = int(result.get("lp_holder_count", 0))
        except (ValueError, TypeError):
            pass

        info.creator_address = result.get("creator_address", "")

        holders = result.get("holders", [])
        if holders:
            dead_addrs = {"0x0000000000000000000000000000000000000000",
                          "0x000000000000000000000000000000000000dead",
                          "0x0000000000000000000000000000000000000001"}
            for h in holders:
                h_addr = (h.get("address") or "").lower()
                if h_addr in dead_addrs:
                    continue
                if str(h.get("is_locked", "0")) == "1" or str(h.get("is_contract", "0")) == "1":
                    continue
                try:
                    pct = float(h.get("percent", 0)) * 100
                    if pct > info.top_holder_percent:
                        info.top_holder_percent = round(pct, 2)
                except (ValueError, TypeError):
                    pass
                break

        if info.creator_address:
            for h in holders:
                if (h.get("address") or "").lower() == info.creator_address.lower():
                    try:
                        info.creator_percent = round(float(h.get("percent", 0)) * 100, 2)
                    except (ValueError, TypeError):
                        pass
                    break

        # ── LP Lock detection ──
        lp_holders = result.get("lp_holders", [])
        total_lp_locked_pct = 0
        best_lock_end = 0
        lock_source = ""
        for lp in lp_holders:
            is_locked = str(lp.get("is_locked", "0")) == "1"
            if is_locked:
                try:
                    pct = float(lp.get("percent", 0)) * 100
                    total_lp_locked_pct += pct
                except (ValueError, TypeError):
                    pass
                locked_details = lp.get("locked_detail", [])
                for detail in locked_details:
                    try:
                        end_ts = float(detail.get("end_time", 0))
                        if end_ts > best_lock_end:
                            best_lock_end = end_ts
                    except (ValueError, TypeError):
                        pass
                lp_addr = (lp.get("address") or "").lower()
                tag = lp.get("tag", "")
                if tag:
                    lock_source = tag
                elif "pink" in lp_addr:
                    lock_source = "PinkLock"
                elif "unicrypt" in lp_addr:
                    lock_source = "Unicrypt"
                elif "team.finance" in lp_addr or "teamfinance" in lp_addr:
                    lock_source = "Team.Finance"

        if total_lp_locked_pct > 0:
            info.lp_locked = True
            info.lp_lock_percent = round(total_lp_locked_pct, 1)
            info.lp_lock_source = lock_source
            if best_lock_end > 0:
                info.lp_lock_end_timestamp = best_lock_end
                remaining_h = max(0, (best_lock_end - time.time()) / 3600)
                info.lp_lock_hours_remaining = round(remaining_h, 1)
        else:
            info.risk_reasons.append("⚠️ Liquidez NO bloqueada — riesgo de rug pull")

        # GoPlus risk reasons
        if not info.is_open_source:
            info.risk_reasons.append("📝 Código no verificado (not open source)")
        if info.is_proxy:
            info.risk_reasons.append("🔄 Contrato proxy (puede cambiar lógica)")
        if info.is_mintable:
            info.risk_reasons.append("🖨️ Token mintable (pueden crear más supply)")
        if info.has_blacklist:
            info.risk_reasons.append("⛔ Tiene blacklist (pueden bloquear tu wallet)")
        if info.can_pause_trading:
            info.risk_reasons.append("⏸️ Puede pausar trading")
        if info.has_hidden_owner:
            info.risk_reasons.append("🕵️ Owner oculto")
        if info.can_self_destruct:
            info.risk_reasons.append("💣 Contrato puede autodestruirse")
        if info.has_external_call:
            info.risk_reasons.append("📡 Llamadas externas (puede cambiar comportamiento)")
        if info.cannot_sell_all:
            info.risk_reasons.append("🔐 No puedes vender todos tus tokens")
        if info.owner_can_change_balance:
            info.risk_reasons.append("⚠️ Owner puede modificar balances")
        if info.has_trading_cooldown:
            info.risk_reasons.append("⏳ Cooldown entre trades")
        if info.personal_slippage_modifiable:
            info.risk_reasons.append("📊 Slippage modificable por el owner")
        logger.info(f"GoPlus analysis for {info.symbol}: {len(info.risk_reasons)} flags")

    def _apply_dexscreener_data(self, info: TokenInfo, data: dict):
        """Apply DexScreener data to TokenInfo."""
        info.dexscreener_pairs = data.get("pairs_count", 0)
        best = data.get("best", {})
        if not best:
            return

        info.dexscreener_liquidity = float(best.get("liquidity", {}).get("usd", 0) or 0)
        vol = best.get("volume", {})
        info.dexscreener_volume_24h = float(vol.get("h24", 0) or 0)
        txns = best.get("txns", {}).get("h24", {})
        info.dexscreener_buys_24h = int(txns.get("buys", 0) or 0)
        info.dexscreener_sells_24h = int(txns.get("sells", 0) or 0)

        created = best.get("pairCreatedAt")
        if created:
            age_ms = time.time() * 1000 - float(created)
            info.dexscreener_age_hours = round(max(0, age_ms / 3_600_000), 1)

        token_info_block = best.get("info", {})
        socials = token_info_block.get("socials", [])
        websites = token_info_block.get("websites", [])
        info.has_social_links = len(socials) > 0
        info.has_website = len(websites) > 0

        pc = best.get("priceChange", {})
        for field_name, key in [
            ("dexscreener_price_change_m5", "m5"),
            ("dexscreener_price_change_h1", "h1"),
            ("dexscreener_price_change_h6", "h6"),
            ("dexscreener_price_change_h24", "h24"),
        ]:
            try:
                setattr(info, field_name, float(pc.get(key, 0) or 0))
            except (ValueError, TypeError):
                pass

        logger.info(f"DexScreener for {info.symbol}: {info.dexscreener_pairs} pairs, ${info.dexscreener_volume_24h:.0f} vol")

    def _apply_coingecko_data(self, info: TokenInfo, data: dict):
        """Apply CoinGecko listing data to TokenInfo."""
        info.listed_coingecko = data.get("listed", False)
        info.coingecko_id = data.get("id", "")
        if info.listed_coingecko:
            logger.info(f"CoinGecko: {info.symbol} IS listed (id={info.coingecko_id})")

    def _apply_tokensniffer_data(self, info: TokenInfo, data: dict):
        """Apply TokenSniffer data to TokenInfo."""
        info.tokensniffer_score = data.get("score", -1)
        info.tokensniffer_is_scam = data.get("is_flagged", False)

        if info.tokensniffer_is_scam:
            info.risk_reasons.append(f"🚩 TokenSniffer: FLAGGED como scam (score {info.tokensniffer_score}/100)")
        elif info.tokensniffer_score < 30:
            info.risk_reasons.append(f"🚩 TokenSniffer score muy bajo: {info.tokensniffer_score}/100")
        elif info.tokensniffer_score < 50:
            info.risk_reasons.append(f"⚠️ TokenSniffer score bajo: {info.tokensniffer_score}/100")

        logger.info(f"TokenSniffer for {info.symbol}: score={info.tokensniffer_score}, flagged={info.tokensniffer_is_scam}")

    # ─── Legacy _check_*_api methods (kept as compatibility stubs) ───
    async def _check_honeypot_api(self, info: TokenInfo):
        """Legacy: delegates to resilient wrapper."""
        await self._resilient_check_honeypot(info, info.address.lower())

    async def _check_goplus_api(self, info: TokenInfo):
        """Legacy: delegates to resilient wrapper."""
        await self._resilient_check_goplus(info, info.address.lower())

    async def _check_dexscreener_api(self, info: TokenInfo):
        """Legacy: delegates to resilient wrapper."""
        await self._resilient_check_dexscreener(info, info.address.lower())

    async def _check_coingecko_api(self, info: TokenInfo):
        """Legacy: delegates to resilient wrapper."""
        await self._resilient_check_coingecko(info, info.address.lower())

    async def _check_tokensniffer_api(self, info: TokenInfo):
        """Legacy: delegates to resilient wrapper."""
        await self._resilient_check_tokensniffer(info, info.address.lower())

    # (Old inline implementations removed — all logic now in _fetch_*_raw + _apply_*_data)

    def _rebuild_flag_reasons(self, info: TokenInfo) -> list[str]:
        """Rebuild descriptive risk reasons from stored security flags.

        Used during periodic refresh so GoPlus / honeypot.is reasons
        are regenerated without re-calling those APIs.
        """
        reasons: list[str] = []

        # ── From honeypot.is flags ──
        if info.is_honeypot:
            reasons.append("🍯 HONEYPOT — no puedes vender")
        if info.buy_tax > 10:
            reasons.append(f"💸 Buy tax alto: {info.buy_tax}%")
        if info.sell_tax > 10:
            reasons.append(f"💸 Sell tax alto: {info.sell_tax}%")

        # ── From GoPlus flags ──
        if info._goplus_ok:
            if not info.is_open_source:
                reasons.append("🔒 Código NO verificado — PELIGRO")
            if info.is_proxy:
                reasons.append("🔄 Contrato proxy — puede cambiar TODA la lógica")
            if info.is_mintable:
                reasons.append("🖨️ Owner puede crear más tokens (mint)")
            if info.has_blacklist:
                reasons.append("🚫 Puede bloquear wallets (blacklist)")
            if info.can_pause_trading:
                reasons.append("⏸️ Puede pausar el trading")
            if info.has_hidden_owner:
                reasons.append("👤 Owner oculto — control invisible")
            if info.can_self_destruct:
                reasons.append("💣 Contrato puede autodestruirse")
            if info.has_external_call:
                reasons.append("📡 Llamadas externas (puede cambiar comportamiento)")
            if info.cannot_sell_all:
                reasons.append("🔐 No puedes vender todos tus tokens")
            if info.owner_can_change_balance:
                reasons.append("⚠️ Owner puede modificar balances")
            if info.has_trading_cooldown:
                reasons.append("⏳ Cooldown entre trades")
            if info.personal_slippage_modifiable:
                reasons.append("📊 Slippage modificable por el owner")
            # ── Hardened contract flags ──
            if info.can_take_back_ownership:
                reasons.append("🚨 Owner puede RECLAMAR ownership después de renunciar")
            if info.is_airdrop_scam:
                reasons.append("🚨 Token de AIRDROP SCAM")
            if not info.is_true_token:
                reasons.append("🚨 Token FALSO — no es ERC-20 real")
            if info.top_holder_percent >= 30:
                reasons.append(f"🐋 Top holder tiene {info.top_holder_percent}% del supply — riesgo de dump")
            elif info.top_holder_percent >= 15:
                reasons.append(f"🐋 Top holder tiene {info.top_holder_percent}% del supply")
            if info.creator_percent >= 20:
                reasons.append(f"👨‍💻 Creator retiene {info.creator_percent}% del supply — riesgo de dump")
            elif info.creator_percent >= 10:
                reasons.append(f"👨‍💻 Creator retiene {info.creator_percent}% del supply")
            if info.holder_count > 0 and info.holder_count < 10:
                reasons.append(f"👥 Solo {info.holder_count} holders — token sospechoso")

        # ── From TokenSniffer flags ──
        if info._tokensniffer_ok:
            if info.tokensniffer_is_scam:
                reasons.append("🚨 TokenSniffer flagged as SCAM")
            elif info.tokensniffer_score != -1 and info.tokensniffer_score < 50:
                reasons.append(f"⚠️ TokenSniffer score bajo: {info.tokensniffer_score}/100")

        # ── LP Lock status ──
        if info._goplus_ok:
            if info.lp_locked:
                # Recalculate remaining hours from stored end_timestamp
                if info.lp_lock_end_timestamp > 0:
                    remaining_h = max(0, (info.lp_lock_end_timestamp - time.time()) / 3600)
                    info.lp_lock_hours_remaining = round(remaining_h, 1)
                reasons.append(f"🔒 LP Locked {info.lp_lock_percent}% — {info.lp_lock_hours_remaining}h restantes")
                if info.lp_lock_source:
                    reasons.append(f"🔐 Lock via: {info.lp_lock_source}")
                # Warn about weak locks
                if info.lp_lock_percent < 80:
                    reasons.append(f"⚠️ Lock insuficiente: solo {info.lp_lock_percent}% (mínimo 80%)")
                if 0 < info.lp_lock_hours_remaining < 24:
                    reasons.append(f"⚠️ Lock demasiado corto: {info.lp_lock_hours_remaining}h (mínimo 24h)")
                elif info.lp_lock_hours_remaining <= 0 and info.lp_lock_end_timestamp > 0:
                    reasons.append("🚨 Lock EXPIRADO — owner puede retirar liquidez")
            else:
                reasons.append("⚠️ Liquidez NO bloqueada — riesgo de rug pull")

        # ── From DexScreener data ──
        if info._dexscreener_ok:
            if info.dexscreener_age_hours < 1:
                reasons.append("🔜 Par creado hace menos de 1 hora")
            if info.dexscreener_volume_24h < 100 and info.dexscreener_pairs <= 1:
                reasons.append("📊 Volumen 24h muy bajo (<$100)")
            total_txns = info.dexscreener_buys_24h + info.dexscreener_sells_24h
            if total_txns < 5:
                reasons.append(f"👥 Muy pocas transacciones ({total_txns} en 24h)")
            if info.dexscreener_price_change_h24 <= -50:
                reasons.append(f"📉 Caída 24h: {info.dexscreener_price_change_h24:.0f}% — posible dump")
            elif info.dexscreener_price_change_h24 <= -30:
                reasons.append(f"📉 Caída 24h: {info.dexscreener_price_change_h24:.0f}%")
            if info.dexscreener_price_change_h6 <= -40:
                reasons.append(f"📉 Caída 6h: {info.dexscreener_price_change_h6:.0f}%")
            if info.dexscreener_price_change_h1 <= -25:
                reasons.append(f"📉 Caída 1h: {info.dexscreener_price_change_h1:.0f}%")

        return reasons

    def _calculate_risk(self, info: TokenInfo):
        """Calculate overall risk level based on ALL collected data."""

        # How many APIs responded?
        api_count = sum([
            info._goplus_ok, info._honeypot_ok,
            info._dexscreener_ok, info._coingecko_ok,
            info._tokensniffer_ok,
        ])

        # If NO API returned data → unknown
        if api_count == 0:
            info.risk = "unknown"
            info.risk_reasons.append("❓ Sin datos de APIs de seguridad")
            return

        # ── DANGER: instant red flags ──
        danger_flags = [
            info.is_honeypot,
            info.can_self_destruct,
            info.owner_can_change_balance,
            info.sell_tax > 30 or info.buy_tax > 30,
            info.cannot_sell_all,
            info.tokensniffer_is_scam,
            info.tokensniffer_score != -1 and info.tokensniffer_score < 20,
            # ── Hardened contract verification ──
            info.is_proxy,                          # can change ALL logic post-deploy
            info.can_take_back_ownership,            # owner fakes renounce
            info.is_airdrop_scam,                    # scam airdrop token
            not info.is_true_token if info._goplus_ok else False,  # fake ERC-20
            not info.is_open_source if info._goplus_ok else False,  # unverified = hidden code
            info.has_hidden_owner,                   # invisible control
            info.top_holder_percent >= 30,           # 1 whale holds 30%+ supply
            info.creator_percent >= 20,              # creator kept 20%+ supply
        ]
        if any(danger_flags):
            info.risk = "danger"
            if info.sell_tax > 30 or info.buy_tax > 30:
                info.risk_reasons.append("💀 Tax > 30%")
            if info.is_proxy:
                info.risk_reasons.append("💀 Proxy — puede cambiar TODA la lógica")
            if info.can_take_back_ownership:
                info.risk_reasons.append("💀 Owner puede reclamar ownership")
            if not info.is_true_token and info._goplus_ok:
                info.risk_reasons.append("💀 Token falso — no es un ERC-20 real")
            if not info.is_open_source and info._goplus_ok:
                info.risk_reasons.append("💀 Código NO verificado — posible trampa")
            if info.has_hidden_owner:
                info.risk_reasons.append("💀 Owner oculto — control invisible")
            if info.top_holder_percent >= 30:
                info.risk_reasons.append(f"💀 Top holder tiene {info.top_holder_percent}% del supply")
            if info.creator_percent >= 20:
                info.risk_reasons.append(f"💀 Creator retiene {info.creator_percent}% del supply")
            return

        # ── WARNING: suspicious but not fatal ──
        warning_flags = [
            info.is_mintable,
            info.has_blacklist,
            info.can_pause_trading,
            info.has_external_call,
            info.personal_slippage_modifiable,
            info.sell_tax > 10 or info.buy_tax > 10,
            info.has_owner,
            info.has_trading_cooldown,
            info.top_holder_percent >= 15,           # whale holds 15-30%
            info.creator_percent >= 10,              # creator kept 10-20%
            info._goplus_ok and info.holder_count > 0 and info.holder_count < 10,
            # Cross-platform signals
            info._dexscreener_ok and info.dexscreener_age_hours < 1,
            info._dexscreener_ok and (info.dexscreener_buys_24h + info.dexscreener_sells_24h) < 5,
            info._tokensniffer_ok and 20 <= info.tokensniffer_score < 50,
        ]
        warning_count = sum(1 for f in warning_flags if f)

        if warning_count >= 3:
            info.risk = "danger"
            info.risk_reasons.append(f"⚠️ {warning_count} señales de riesgo combinadas")
            return
        if warning_count >= 1:
            info.risk = "warning"
            return

        # Only mark "safe" if we have good API coverage
        if api_count >= 2 and (info._goplus_ok or info._tokensniffer_ok):
            # Bonus: listed on CoinGecko is a strong positive signal
            if info.listed_coingecko:
                info.risk_reasons.append("✅ Listado en CoinGecko")
            info.risk = "safe"
        else:
            # Partial data only
            info.risk = "warning"
            info.risk_reasons.append(f"⚠️ Solo {api_count} API(s) respondieron — datos parciales")


# ═══════════════════════════════════════════════════════════════════
#  Sniper Bot Engine
# ═══════════════════════════════════════════════════════════════════

class SniperBot:
    """
    Main sniper bot engine (Professional v2).
    Listens for new pairs, analyzes contracts, and manages positions.

    Integrates:
      - PumpAnalyzer:       scoring potential (0–100)
      - SwapSimulator:      on-chain honeypot detection via eth_call
      - BytecodeAnalyzer:   rug-pull bytecode pattern detection
      - MempoolListener:    pending tx monitoring for early detection
      - RugDetector:        post-buy rug pull monitoring
      - PreLaunchDetector:  pre-launch token detection
      - SmartMoneyTracker:  whale wallet tracking
    """

    def __init__(self, chain_id: int = 56):
        self.chain_id = chain_id
        self.running = False
        self._task = None

        # Settings (configurable from frontend)
        self.settings = {
            "min_liquidity_usd": 5000,
            "max_buy_tax": 10,
            "max_sell_tax": 15,
            "buy_amount_native": 0.05,   # BNB/ETH to spend per snipe
            "take_profit": 40,           # %
            "stop_loss": 20,             # %
            "auto_buy": False,           # auto-execute buys
            "only_safe": True,           # only buy "safe" risk tokens
            "slippage": 12,              # %
            "max_hold_hours": 0,         # 0 = disabled; sell after N hours
            # ── Concurrency / Performance ──
            "max_concurrent": 5,         # parallel pair analyses
            "block_range": 5,            # blocks per scan cycle
            "poll_interval": 1.5,        # seconds between scans
            # ── Professional modules (toggles) ──
            "enable_mempool": True,      # mempool listener
            "enable_pump_score": True,   # pump analyzer
            "enable_swap_sim": True,     # swap simulation honeypot check
            "enable_bytecode": True,     # bytecode analysis
            "enable_rug_detector": True, # post-buy rug monitoring
            "enable_prelaunch": True,    # pre-launch detection
            "enable_smart_money": True,  # whale tracking
            "min_pump_score": 40,        # minimum pump score to buy
            # ── v3 Professional modules (toggles) ──
            "enable_dev_tracker": True,  # developer reputation tracking
            "enable_risk_engine": True,  # unified risk scoring
            "enable_trade_executor": False,  # backend trade execution (requires SNIPER_PRIVATE_KEY)
            "risk_engine_min_score": 50, # minimum risk engine score to buy
            "risk_engine_auto_action": "BUY",  # minimum action to auto-buy (STRONG_BUY / BUY)
            # ── v5 Professional modules (toggles) ──
            "enable_proxy_detector": True,       # proxy/upgradeable detection
            "enable_stress_test": True,          # multi-amount slippage analysis
            "enable_volatility_slippage": True,  # dynamic slippage from volatility
            "enable_ml_predictor": True,         # ML pump/dump scoring
            "enable_social_sentiment": True,     # social sentiment analysis
            "enable_anomaly_detector": True,     # volume/holder anomaly detection
            "enable_whale_activity": True,       # whale order analysis
            "enable_dynamic_scanner": True,      # continuous contract monitoring
            "ml_min_score": 30,                  # min ML pump score to buy
            "social_min_score": 0,               # min social sentiment to buy (0 = disabled)
            # ── v6 Professional modules (toggles) ──
            "enable_mev_protection": False,        # MEV protection (Flashbots/48Club)
            "enable_copy_trading": False,           # whale copy trading
            "enable_multi_dex": True,               # multi-DEX routing
            "enable_ai_optimizer": True,            # AI strategy optimizer
            "enable_backtesting": True,             # backtesting engine
            "mev_auto_protect": False,              # auto-apply MEV protection to all trades
            "copy_trade_max_amount": 0.1,           # max BNB/ETH per copy trade
            "copy_trade_auto_follow": True,         # auto-follow smart money wallets
            # ── v7 Professional modules (toggles) ──
            "enable_rl_learner": True,              # reinforcement learning decisions
            "enable_orderflow": True,               # orderflow analysis
            "enable_market_simulator": True,        # advanced market simulation
            "enable_auto_strategy": True,           # auto-generated strategies
            "enable_launch_scanner": True,          # predictive launch scanner
            "enable_mempool_v7": True,              # advanced mempool analyzer
            "enable_whale_graph": True,             # whale network graph
            "rl_min_confidence": 0.3,               # min RL confidence to act
            "orderflow_max_manipulation": 0.7,      # max manipulation risk to buy
            "sim_v7_max_risk": 70,                  # max simulator risk score
            "launch_min_priority": 40,              # min launch snipe priority
        }

        # State
        self.detected_pairs: list[NewPair] = []
        self.active_snipes: list[ActiveSnipe] = []
        self.events_log: list[dict] = []

        # Web3 (HTTP for calls) — with RPC rotation support
        rpc_info = RPC_ENDPOINTS.get(chain_id, RPC_ENDPOINTS[56])
        self._rpc_list = RPC_FALLBACKS.get(chain_id, [rpc_info["http"]])
        self._rpc_index = 0
        self.w3 = Web3(Web3.HTTPProvider(self._rpc_list[0]))

        # Shared aiohttp session (created/closed in run())
        self._http_session: aiohttp.ClientSession | None = None

        # Resilient API Manager (circuit breakers, cache, retries, health)
        self.api_manager = ResilientAPIManager()
        self.analyzer = ContractAnalyzer(self.w3, chain_id, api_manager=self.api_manager)

        # Concurrency semaphore (limits parallel analyses)
        self._sem = asyncio.Semaphore(self.settings["max_concurrent"])

        # Contracts
        factory_addr = FACTORY_ADDRESSES.get(chain_id)
        if factory_addr:
            self.factory = self.w3.eth.contract(
                address=Web3.to_checksum_address(factory_addr),
                abi=FACTORY_ABI,
            )
        else:
            self.factory = None

        self.weth = WETH_ADDRESSES.get(chain_id, "")
        self.router_addr = ROUTER_ADDRESSES.get(chain_id, "")

        # ── Professional modules ──
        self.pump_analyzer = PumpAnalyzer(self.w3, chain_id)
        self.swap_simulator = SwapSimulator(
            self.w3, chain_id, self.router_addr, self.weth
        ) if self.router_addr and self.weth else None
        self.bytecode_analyzer = BytecodeAnalyzer(self.w3)
        self.mempool_listener = MempoolListener(
            chain_id, self.w3,
            FACTORY_ADDRESSES.get(chain_id, ""),
            self.router_addr, self.weth,
        ) if self.router_addr else None
        self.rug_detector = RugDetector(
            self.w3, chain_id, self.router_addr, self.weth
        ) if self.router_addr else None
        self.prelaunch_detector = PreLaunchDetector(
            self.w3, chain_id, self.router_addr,
            FACTORY_ADDRESSES.get(chain_id, ""),
        ) if self.router_addr else None
        self.smart_money = SmartMoneyTracker(self.w3, chain_id)

        # ── Professional v3 modules ──
        self.dev_tracker = DevTracker(self.w3, chain_id)
        self.risk_engine = RiskEngine()
        self.trade_executor = TradeExecutor(
            self.w3, chain_id, self.router_addr, self.weth, self._rpc_list
        ) if self.router_addr and self.weth else None

        # ── Professional v4 modules ──
        self.resource_monitor = ResourceMonitor()
        self.alert_service = AlertService()
        self.metrics_service = MetricsService()

        # ── Professional v5 modules ──
        self.proxy_detector = ProxyDetector(self.w3)
        self.stress_tester = StressTester(
            self.w3, self.router_addr, self.weth
        ) if self.router_addr and self.weth else None
        self.volatility_slippage = VolatilitySlippageCalc()
        self.ml_predictor = PumpDumpPredictor()
        self.anomaly_detector = AnomalyDetector()
        self.social_sentiment = SocialSentimentAnalyzer()
        self.dynamic_scanner = DynamicContractScanner(self.w3, chain_id)
        self._dynamic_scanner_task: asyncio.Task | None = None

        # ── Professional v6 modules ──
        self.mev_protector = MEVProtector(self.w3, chain_id)
        self.copy_trader = CopyTrader(self.w3, chain_id)
        self.multi_dex_router = MultiDexRouter()
        self.strategy_optimizer = StrategyOptimizer()
        self.backtest_engine = BacktestEngine(self.w3, chain_id)

        # Connect v6 modules
        if self.trade_executor:
            self.copy_trader.set_trade_executor(self.trade_executor)
        self.copy_trader.set_smart_money_tracker(self.smart_money)
        self.multi_dex_router.add_chain(chain_id, w3=self.w3)

        # ── Professional v7 modules ──
        self.rl_learner = ReinforcementLearner()
        self.orderflow_analyzer = OrderflowAnalyzer(self.w3, chain_id)
        self.market_simulator = MarketSimulator(self.w3, chain_id)
        self.auto_strategy = AutoStrategyGenerator()
        self.launch_scanner = PredictiveLaunchScanner(self.w3, chain_id)
        self.mempool_analyzer_v7 = MempoolAnalyzer(self.w3, chain_id)
        self.whale_graph = WhaleNetworkGraph(self.w3, chain_id)
        self._launch_scanner_task: asyncio.Task | None = None
        self._mempool_v7_task: asyncio.Task | None = None

        # Background task handles for professional modules
        self._mempool_task: asyncio.Task | None = None
        self._rug_task: asyncio.Task | None = None
        self._prelaunch_task: asyncio.Task | None = None

        # Callback to push events to frontend
        self._event_callback = None

        # Native price (USD) — fetched periodically
        self.native_price_usd = 0

        # ── Sync WebSocket listener state ──
        self._sync_task: asyncio.Task | None = None
        self._sync_ws = None               # aiohttp WebSocket connection
        self._sync_sub_id: str | None = None
        self._sync_needs_resub = False      # flag: new pairs added, need resubscribe
        self._sync_tracked: set[str] = set()  # lowercase pair addresses currently subscribed

    def set_event_callback(self, cb):
        """Set async callback fn(event_dict) to push events to WS client."""
        self._event_callback = cb

    async def _emit(self, event_type: str, data: dict):
        """Emit an event to the log and optionally to frontend."""
        event = {
            "type": event_type,
            "timestamp": time.time(),
            "data": data,
        }
        self.events_log.append(event)
        # Keep log manageable
        if len(self.events_log) > 500:
            self.events_log = self.events_log[-300:]

        if self._event_callback:
            try:
                await self._event_callback(event)
            except Exception as e:
                logger.debug(f"Event callback error: {e}")

    # ─── Price fetcher ──────────────────────────────────────
    async def _safe_get_block_number(self) -> int:
        """Get current block number with RPC rotation on failure."""
        for _attempt in range(min(len(self._rpc_list), 5)):
            try:
                block = self.w3.eth.block_number
                return block
            except Exception as e:
                err_str = str(e)[:200]
                is_429 = "429" in err_str or "Too Many Requests" in err_str
                logger.debug(f"block_number failed on {self._rpc_list[self._rpc_index]}: {err_str[:80]}")
                self._rpc_index = (self._rpc_index + 1) % len(self._rpc_list)
                new_rpc = self._rpc_list[self._rpc_index]
                self.w3 = Web3(Web3.HTTPProvider(new_rpc))
                self.analyzer = ContractAnalyzer(self.w3, self.chain_id, self._http_session, api_manager=self.api_manager)
                await asyncio.sleep(1.0 if is_429 else 0.3)
        # If all attempts fail, raise to let the main loop handle it
        raise ConnectionError("All RPCs failed for block_number")

    async def _fetch_native_price(self):
        """Get native token USD price from Binance."""
        sym = "BNBUSDT" if self.chain_id == 56 else "ETHUSDT"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"https://api.binance.com/api/v3/ticker/price?symbol={sym}",
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as resp:
                    d = await resp.json()
                    self.native_price_usd = float(d.get("price", 0))
        except Exception:
            pass

    # ─── Liquidity check ────────────────────────────────────
    async def _get_pair_liquidity(self, pair_address: str) -> tuple[float, float]:
        """Get liquidity in native token and USD with RPC retry."""
        loop = asyncio.get_event_loop()
        max_attempts = min(3, len(self._rpc_list))
        last_err = None

        for attempt in range(max_attempts):
            try:
                pair_cs = Web3.to_checksum_address(pair_address)
                pair_contract = self.w3.eth.contract(address=pair_cs, abi=PAIR_ABI)

                reserves = await loop.run_in_executor(None, pair_contract.functions.getReserves().call)
                token0 = await loop.run_in_executor(None, pair_contract.functions.token0().call)

                weth_cs = Web3.to_checksum_address(self.weth)
                if token0.lower() == weth_cs.lower():
                    native_reserve = reserves[0] / 1e18
                else:
                    native_reserve = reserves[1] / 1e18

                # Guard: if native_price_usd is 0, try fetching it again
                if self.native_price_usd <= 0:
                    await self._fetch_native_price()

                usd_value = native_reserve * self.native_price_usd * 2  # both sides
                return native_reserve, usd_value
            except Exception as e:
                last_err = e
                logger.debug(f"Liquidity check attempt {attempt+1}/{max_attempts} failed: {e}")
                # Rotate to next RPC and retry
                self._rpc_index = (self._rpc_index + 1) % len(self._rpc_list)
                new_rpc = self._rpc_list[self._rpc_index]
                self.w3 = Web3(Web3.HTTPProvider(new_rpc))
                self.analyzer = ContractAnalyzer(self.w3, self.chain_id, self._http_session, api_manager=self.api_manager)
                await asyncio.sleep(0.5)

        logger.warning(f"Liquidity check failed after {max_attempts} attempts for {pair_address}: {last_err}")
        return 0, 0

    # ─── Token price via router ─────────────────────────────
    async def _get_token_price_usd(self, token_address: str) -> float:
        """Get token price in USD via DEX router (async-friendly)."""
        loop = asyncio.get_event_loop()
        try:
            router = self.w3.eth.contract(
                address=Web3.to_checksum_address(self.router_addr),
                abi=ROUTER_ABI,
            )
            weth_cs = Web3.to_checksum_address(self.weth)
            token_cs = Web3.to_checksum_address(token_address)

            amounts = await loop.run_in_executor(
                None,
                router.functions.getAmountsOut(
                    10**18,
                    [token_cs, weth_cs]
                ).call,
            )

            native_per_token = amounts[1] / 1e18
            return native_per_token * self.native_price_usd
        except Exception:
            return 0

    # ─── Build token event payload ──────────────────────────
    def _collect_rejection_reasons(self, ti: 'TokenInfo', passes: bool) -> list[str]:
        """Collect human-readable rejection reasons for a token."""
        if passes:
            return []
        reasons = []
        if self.settings["only_safe"] and ti.risk == "danger":
            reasons.append(f"Risk level: {ti.risk}")
        if ti.buy_tax > self.settings["max_buy_tax"]:
            reasons.append(f"Buy tax {ti.buy_tax}% > max {self.settings['max_buy_tax']}%")
        if ti.sell_tax > self.settings["max_sell_tax"]:
            reasons.append(f"Sell tax {ti.sell_tax}% > max {self.settings['max_sell_tax']}%")
        if ti.is_honeypot:
            reasons.append("Honeypot detected")
        if ti._goplus_ok:
            if not ti.is_open_source:
                reasons.append("Code not verified (not open source)")
            if ti.is_proxy:
                reasons.append("Proxy contract — can change logic")
            if ti.has_hidden_owner:
                reasons.append("Hidden owner")
            if ti.can_take_back_ownership:
                reasons.append("Can reclaim ownership")
            if ti.is_airdrop_scam:
                reasons.append("Airdrop scam")
            if not ti.is_true_token:
                reasons.append("Fake ERC-20")
            if ti.top_holder_percent >= 30:
                reasons.append(f"Top holder has {ti.top_holder_percent}% supply")
            if ti.creator_percent >= 20:
                reasons.append(f"Creator retains {ti.creator_percent}% supply")
        if not ti.lp_locked:
            reasons.append("LP not locked")
        elif ti.lp_lock_percent < 80:
            reasons.append(f"LP lock only {ti.lp_lock_percent}%")
        elif ti.lp_lock_hours_remaining < 24:
            reasons.append(f"LP lock only {ti.lp_lock_hours_remaining}h remaining")
        if ti.pump_score > 0 and getattr(ti, 'pump_grade', '') == "AVOID":
            reasons.append(f"Pump grade AVOID (score={ti.pump_score})")
        if ti._simulation_ok and ti.sim_is_honeypot:
            reasons.append(f"Swap sim honeypot: {ti.sim_honeypot_reason}")
        if ti._risk_engine_ok and ti.risk_engine_hard_stop:
            reasons.append(f"Hard stop: {ti.risk_engine_hard_stop_reason}")
        if ti._dev_ok and ti.dev_is_serial_scammer:
            reasons.append(f"Serial scammer dev ({ti.dev_rug_pulls} past rugs)")
        if ti._ml_pump_ok and ti.ml_pump_label == "danger":
            reasons.append(f"ML dump prediction (score={ti.ml_pump_score})")
        if ti._rl_ok and ti.rl_decision == "sell":
            reasons.append(f"RL agent says SELL ({ti.rl_confidence:.0%})")
        if ti._orderflow_ok and ti.orderflow_manipulation_risk >= self.settings.get("orderflow_max_manipulation", 0.7):
            reasons.append(f"Orderflow manipulation {ti.orderflow_manipulation_risk:.0%}")
        if ti._sim_v7_ok and ti.sim_v7_recommendation == "avoid":
            reasons.append(f"Simulator: AVOID (risk={ti.sim_v7_risk_score:.0f})")
        if ti._whale_network_ok and ti.whale_network_sybil_risk >= 0.7:
            reasons.append(f"Sybil network risk {ti.whale_network_sybil_risk:.0%}")
        if ti._mempool_v7_ok and ti.mempool_v7_frontrun_risk >= 0.8:
            reasons.append(f"Mempool frontrun risk {ti.mempool_v7_frontrun_risk:.0%}")
        return reasons[:10]

    def _build_token_event_data(self, token: str, pair: str, info: 'TokenInfo',
                                 native_liq: float, usd_liq: float,
                                 has_liquidity: bool, block: int) -> dict:
        """Build the full data dict for token_detected / token_updated events."""
        return {
            "token": token,
            "pair": pair,
            "symbol": info.symbol,
            "name": info.name,
            "risk": info.risk,
            "buy_tax": info.buy_tax,
            "sell_tax": info.sell_tax,
            "is_honeypot": info.is_honeypot,
            "has_owner": info.has_owner,
            "risk_reasons": list(info.risk_reasons),
            "liquidity_usd": round(usd_liq, 2),
            "liquidity_native": round(native_liq, 4),
            "has_liquidity": has_liquidity,
            "block": block,
            # Security flags
            "is_mintable": info.is_mintable,
            "has_blacklist": info.has_blacklist,
            "can_pause_trading": info.can_pause_trading,
            "is_proxy": info.is_proxy,
            "has_hidden_owner": info.has_hidden_owner,
            "can_self_destruct": info.can_self_destruct,
            "cannot_sell_all": info.cannot_sell_all,
            "owner_can_change_balance": info.owner_can_change_balance,
            "is_open_source": info.is_open_source,
            "holder_count": info.holder_count,
            "total_supply": info.total_supply,
            # Cross-platform data
            "listed_coingecko": info.listed_coingecko,
            "coingecko_id": info.coingecko_id,
            "dexscreener_pairs": info.dexscreener_pairs,
            "dexscreener_volume_24h": info.dexscreener_volume_24h,
            "dexscreener_liquidity": info.dexscreener_liquidity,
            "dexscreener_buys_24h": info.dexscreener_buys_24h,
            "dexscreener_sells_24h": info.dexscreener_sells_24h,
            "dexscreener_age_hours": info.dexscreener_age_hours,
            "has_social_links": info.has_social_links,
            "has_website": info.has_website,
            "tokensniffer_score": info.tokensniffer_score,
            "tokensniffer_is_scam": info.tokensniffer_is_scam,
            # API status
            "goplus_ok": info._goplus_ok,
            "honeypot_ok": info._honeypot_ok,
            "dexscreener_ok": info._dexscreener_ok,
            "coingecko_ok": info._coingecko_ok,
            "tokensniffer_ok": info._tokensniffer_ok,
            # LP Lock
            "lp_locked": info.lp_locked,
            "lp_lock_percent": info.lp_lock_percent,
            "lp_lock_hours_remaining": info.lp_lock_hours_remaining,
            "lp_lock_source": info.lp_lock_source,
            # Price history
            "price_change_m5": info.dexscreener_price_change_m5,
            "price_change_h1": info.dexscreener_price_change_h1,
            "price_change_h6": info.dexscreener_price_change_h6,
            "price_change_h24": info.dexscreener_price_change_h24,
            # ── Professional v2 data ──
            # Pump Score
            "pump_score": info.pump_score,
            "pump_grade": info.pump_grade,
            "pump_signals": list(info.pump_signals),
            # Swap Simulation
            "sim_can_buy": info.sim_can_buy,
            "sim_can_sell": info.sim_can_sell,
            "sim_buy_tax": info.sim_buy_tax,
            "sim_sell_tax": info.sim_sell_tax,
            "sim_is_honeypot": info.sim_is_honeypot,
            "sim_honeypot_reason": info.sim_honeypot_reason,
            "simulation_ok": info._simulation_ok,
            # Bytecode
            "bytecode_has_selfdestruct": info.bytecode_has_selfdestruct,
            "bytecode_has_delegatecall": info.bytecode_has_delegatecall,
            "bytecode_is_proxy": info.bytecode_is_proxy,
            "bytecode_size": info.bytecode_size,
            "bytecode_flags": list(info.bytecode_flags),
            "bytecode_ok": info._bytecode_ok,
            # Smart Money
            "smart_money_buyers": info.smart_money_buyers,
            "smart_money_confidence": info.smart_money_confidence,
            # ── Professional v3 data ──
            # Dev Tracker
            "dev_is_tracked": info.dev_is_tracked,
            "dev_score": info.dev_score,
            "dev_label": info.dev_label,
            "dev_total_launches": info.dev_total_launches,
            "dev_successful_launches": info.dev_successful_launches,
            "dev_rug_pulls": info.dev_rug_pulls,
            "dev_best_multiplier": info.dev_best_multiplier,
            "dev_is_serial_scammer": info.dev_is_serial_scammer,
            "dev_ok": info._dev_ok,
            # Risk Engine
            "risk_engine_score": info.risk_engine_score,
            "risk_engine_action": info.risk_engine_action,
            "risk_engine_confidence": info.risk_engine_confidence,
            "risk_engine_hard_stop": info.risk_engine_hard_stop,
            "risk_engine_hard_stop_reason": info.risk_engine_hard_stop_reason,
            "risk_engine_signals": list(info.risk_engine_signals),
            "risk_engine_ok": info._risk_engine_ok,
            # Pump v3 extras
            "pump_mcap_score": info.pump_mcap_score,
            "pump_market_cap_usd": info.pump_market_cap_usd,
            "pump_holder_growth_rate": info.pump_holder_growth_rate,
            "pump_lp_growth_percent": info.pump_lp_growth_percent,
            "pump_holder_growth_score": info.pump_holder_growth_score,
            "pump_lp_growth_score": info.pump_lp_growth_score,
            # Trade Executor availability
            "backend_buy_available": info.backend_buy_available,
            # ── Professional v5 data ──
            # Proxy Detection
            "proxy_is_proxy": info.proxy_is_proxy,
            "proxy_type": info.proxy_type,
            "proxy_risk_level": info.proxy_risk_level,
            "proxy_has_multisig": info.proxy_has_multisig,
            "proxy_has_timelock": info.proxy_has_timelock,
            "proxy_signals": list(info.proxy_signals),
            "proxy_ok": info._proxy_ok,
            # Stress Test
            "stress_max_safe_amount": info.stress_max_safe_amount,
            "stress_liquidity_depth": info.stress_liquidity_depth,
            "stress_avg_slippage": info.stress_avg_slippage,
            "stress_ok": info._stress_ok,
            # Volatility Slippage
            "volatility_score": info.volatility_score,
            "volatility_recommended_slippage": info.volatility_recommended_slippage,
            "volatility_ok": info._volatility_ok,
            # ML Prediction
            "ml_pump_score": info.ml_pump_score,
            "ml_pump_confidence": info.ml_pump_confidence,
            "ml_pump_label": info.ml_pump_label,
            "ml_pump_ok": info._ml_pump_ok,
            # Social Sentiment
            "social_sentiment_score": info.social_sentiment_score,
            "social_sentiment_label": info.social_sentiment_label,
            "social_platforms_active": info.social_platforms_active,
            "social_signals": list(info.social_signals),
            "social_ok": info._social_ok,
            # Anomaly Detection
            "anomaly_is_anomalous": info.anomaly_is_anomalous,
            "anomaly_score": info.anomaly_score,
            "anomaly_type": info.anomaly_type,
            "anomaly_ok": info._anomaly_ok,
            # Whale Activity
            "whale_total_buys": info.whale_total_buys,
            "whale_total_sells": info.whale_total_sells,
            "whale_net_flow": info.whale_net_flow,
            "whale_coordinated": info.whale_coordinated,
            "whale_dev_dumping": info.whale_dev_dumping,
            "whale_concentration_pct": info.whale_concentration_pct,
            "whale_risk_score": info.whale_risk_score,
            "whale_signals": list(info.whale_signals),
            "whale_ok": info._whale_ok,
            # Dynamic Scanner
            "dynamic_scan_risk": info.dynamic_scan_risk,
            "dynamic_scan_blocked": info.dynamic_scan_blocked,
            "dynamic_scan_block_reason": info.dynamic_scan_block_reason,
            "dynamic_scan_alerts": list(info.dynamic_scan_alerts),
            "dynamic_scan_ok": info._dynamic_scan_ok,
            # Dev ML
            "dev_ml_reputation": info.dev_ml_reputation,
            "dev_ml_cluster": info.dev_ml_cluster,
            "dev_ml_confidence": info.dev_ml_confidence,
            # ── Professional v6 data ──
            # MEV Protection
            "mev_threat_level": info.mev_threat_level,
            "mev_strategy_used": info.mev_strategy_used,
            "mev_frontrun_risk": info.mev_frontrun_risk,
            "mev_sandwich_risk": info.mev_sandwich_risk,
            "mev_ok": info._mev_ok,
            # Multi-DEX Routing
            "best_dex_name": info.best_dex_name,
            "best_dex_amount_out": info.best_dex_amount_out,
            "best_dex_route": info.best_dex_route,
            "multi_dex_ok": info._multi_dex_ok,
            # Copy Trading
            "copy_trade_signal": info.copy_trade_signal,
            "copy_trade_wallet": info.copy_trade_wallet,
            "copy_trade_confidence": info.copy_trade_confidence,
            # AI Optimizer
            "ai_regime": info.ai_regime,
            "ai_suggested_tp": info.ai_suggested_tp,
            "ai_suggested_sl": info.ai_suggested_sl,
        }

    # ─── Refresh detected tokens (periodic re-check) ───────
    async def _enrich_detected_tokens(self, fast_only: bool = False):
        """Re-check liquidity and retry failed APIs for recently detected tokens.
        Only emits 'token_updated' if data actually changed.

        fast_only=True  → only process tokens with incomplete API data (quick retry)
        fast_only=False → process ALL tokens (full refresh including DexScreener prices)
        """
        now = time.time()
        all_pairs = self.detected_pairs[-50:]
        if not all_pairs:
            return

        if fast_only:
            # Only tokens that still have failed APIs AND aren't too old (< 5 min)
            candidates = [
                p for p in all_pairs
                if p.token_info and (now - p.timestamp < 300) and not all([
                    p.token_info._goplus_ok,
                    p.token_info._honeypot_ok,
                    p.token_info._dexscreener_ok,
                    p.token_info._coingecko_ok,
                    p.token_info._tokensniffer_ok,
                ])
            ]
            if not candidates:
                return
        else:
            # Full refresh: only tokens from the last 10 minutes
            candidates = [p for p in all_pairs if now - p.timestamp < 600]
            if not candidates:
                return

        sem = asyncio.Semaphore(3)

        async def _refresh_one(pair: 'NewPair'):
            async with sem:
                try:
                    token = pair.new_token
                    pair_addr = pair.pair_address
                    info = pair.token_info
                    if not info:
                        return

                    # Re-check on-chain liquidity
                    native_liq, usd_liq = await self._get_pair_liquidity(pair_addr)

                    # Retry failed APIs (skip DexScreener re-check if it already succeeded and liq is 0)
                    retry_tasks = []
                    if not info._goplus_ok:
                        retry_tasks.append(self.analyzer._check_goplus_api(info))
                    if not info._honeypot_ok:
                        retry_tasks.append(self.analyzer._check_honeypot_api(info))
                    if not info._coingecko_ok:
                        retry_tasks.append(self.analyzer._check_coingecko_api(info))
                    if not info._tokensniffer_ok:
                        retry_tasks.append(self.analyzer._check_tokensniffer_api(info))

                    # Re-check DexScreener only if it hasn't worked yet or on slow refresh
                    if not info._dexscreener_ok or not fast_only:
                        retry_tasks.append(self.analyzer._check_dexscreener_api(info))

                    if retry_tasks:
                        logger.debug(f"Enriching {info.symbol}: {len(retry_tasks)} API(s)")
                        await asyncio.gather(*retry_tasks, return_exceptions=True)

                    # Use DexScreener liquidity as fallback if on-chain returns 0
                    if usd_liq <= 0 and info and info.dexscreener_liquidity > 0:
                        usd_liq = info.dexscreener_liquidity

                    old_liq = pair.liquidity_usd
                    old_risk = info.risk
                    old_api_count = sum([info._goplus_ok, info._honeypot_ok, info._dexscreener_ok, info._coingecko_ok, info._tokensniffer_ok])

                    pair.liquidity_native = native_liq
                    pair.liquidity_usd = usd_liq

                    has_liquidity = usd_liq >= self.settings["min_liquidity_usd"]

                    if info:
                        # Rebuild reasons from stored flags + fresh DexScreener data
                        info.risk_reasons = self.analyzer._rebuild_flag_reasons(info)
                        self.analyzer._calculate_risk(info)

                        # Only emit token_updated if something actually changed
                        new_api_count = sum([info._goplus_ok, info._honeypot_ok, info._dexscreener_ok, info._coingecko_ok, info._tokensniffer_ok])
                        liq_changed = abs(usd_liq - old_liq) > 1.0
                        risk_changed = info.risk != old_risk
                        apis_changed = new_api_count != old_api_count

                        if liq_changed or risk_changed or apis_changed:
                            event_data = self._build_token_event_data(
                                token, pair_addr, info,
                                native_liq, usd_liq, has_liquidity,
                                pair.block_number,
                            )
                            await self._emit("token_updated", event_data)

                        # If liquidity just appeared and token passes safety, emit opportunity
                        if has_liquidity and old_liq < self.settings["min_liquidity_usd"]:
                            passes = True
                            if self.settings["only_safe"] and info.risk == "danger":
                                passes = False
                            if info.buy_tax > self.settings["max_buy_tax"]:
                                passes = False
                            if info.sell_tax > self.settings["max_sell_tax"]:
                                passes = False
                            if info.is_honeypot:
                                passes = False
                            # Hardened contract checks
                            if info._goplus_ok:
                                if not info.is_open_source:
                                    passes = False
                                if info.is_proxy:
                                    passes = False
                                if info.has_hidden_owner:
                                    passes = False
                                if info.can_take_back_ownership:
                                    passes = False
                                if info.is_airdrop_scam:
                                    passes = False
                                if not info.is_true_token:
                                    passes = False
                                if info.top_holder_percent >= 30:
                                    passes = False
                                if info.creator_percent >= 20:
                                    passes = False
                            if not info.lp_locked:
                                passes = False
                            elif info.lp_lock_percent < 80:
                                passes = False
                            elif info.lp_lock_hours_remaining < 24:
                                passes = False
                            if info._dexscreener_ok:
                                # Reject severe dumps
                                if info.dexscreener_price_change_h24 <= -50:
                                    passes = False
                                if info.dexscreener_price_change_h6 <= -40:
                                    passes = False
                                if info.dexscreener_price_change_h1 <= -25:
                                    passes = False
                                # Reject already pumped (bad entry)
                                if info.dexscreener_price_change_m5 >= 30:
                                    passes = False
                                if info.dexscreener_price_change_h1 >= 50:
                                    passes = False
                            if passes:
                                auto_hold_h = 0
                                if info.lp_lock_hours_remaining > 1:
                                    auto_hold_h = round(info.lp_lock_hours_remaining - 1, 1)
                                await self._emit("snipe_opportunity", {
                                    "token": token,
                                    "symbol": info.symbol,
                                    "name": info.name,
                                    "risk": info.risk,
                                    "liquidity_usd": round(usd_liq, 2),
                                    "pair": pair_addr,
                                    "auto_buy": self.settings["auto_buy"],
                                    "lp_locked": info.lp_locked,
                                    "lp_lock_hours": info.lp_lock_hours_remaining,
                                    "lp_lock_percent": info.lp_lock_percent,
                                    "auto_hold_hours": auto_hold_h,
                                })
                except Exception as e:
                    logger.debug(f"Refresh token failed: {e}")

        tasks = [_refresh_one(p) for p in candidates]
        await asyncio.gather(*tasks, return_exceptions=True)

    # ─── New pair handler ───────────────────────────────────
    async def _handle_new_pair(self, pair_address: str, token0: str, token1: str, block: int):
        """Process a newly detected pair (runs under semaphore)."""
        async with self._sem:
            await self._process_pair(pair_address, token0, token1, block)

    async def _process_pair(self, pair_address: str, token0: str, token1: str, block: int):
        """Internal: full analysis pipeline for one pair."""
        _pipeline_start = time.time()
        weth_lower = self.weth.lower()

        if token0.lower() == weth_lower:
            new_token = token1
            base_token = token0
        elif token1.lower() == weth_lower:
            new_token = token0
            base_token = token1
        else:
            # Neither is WBNB/WETH — skip
            return

        new_pair = NewPair(
            pair_address=pair_address,
            token0=token0,
            token1=token1,
            new_token=new_token,
            base_token=base_token,
            chain_id=self.chain_id,
            block_number=block,
            timestamp=time.time(),
        )

        await self._emit("new_pair_raw", {
            "pair": pair_address,
            "token": new_token,
            "block": block,
        })

        # Run analysis + liquidity check IN PARALLEL (biggest speed-up)
        token_info_task = asyncio.ensure_future(self.analyzer.analyze(new_token))
        liquidity_task = asyncio.ensure_future(self._get_pair_liquidity(pair_address))
        token_info, (native_liq, usd_liq) = await asyncio.gather(token_info_task, liquidity_task)

        new_pair.token_info = token_info

        # Use DexScreener liquidity as fallback if on-chain returns 0
        if usd_liq <= 0 and token_info.dexscreener_liquidity > 0:
            usd_liq = token_info.dexscreener_liquidity
            logger.info(f"Using DexScreener liquidity fallback: ${usd_liq:.2f} for {token_info.symbol}")

        new_pair.liquidity_native = native_liq
        new_pair.liquidity_usd = usd_liq

        has_liquidity = usd_liq >= self.settings["min_liquidity_usd"]

        await self._emit("liquidity_check", {
            "token": new_token,
            "liquidity_native": round(native_liq, 4),
            "liquidity_usd": round(usd_liq, 2),
            "min_required": self.settings["min_liquidity_usd"],
            "passed": has_liquidity,
        })

        # Emit token_detected for ALL tokens (liquid or not) so frontend can show them
        await self._emit("token_detected",
            self._build_token_event_data(new_token, pair_address, token_info, native_liq, usd_liq, has_liquidity, block)
        )

        self.detected_pairs.append(new_pair)
        self._notify_sync_resub()  # subscribe to Sync events for this new pair
        if len(self.detected_pairs) > 200:
            self.detected_pairs = self.detected_pairs[-100:]

        # Emit detailed analysis for liquid tokens
        if has_liquidity:
            await self._emit("contract_analysis", {
                "token": new_token,
                "symbol": token_info.symbol,
                "name": token_info.name,
                "risk": token_info.risk,
                "buy_tax": token_info.buy_tax,
                "sell_tax": token_info.sell_tax,
                "is_honeypot": token_info.is_honeypot,
                "has_owner": token_info.has_owner,
                "risk_reasons": token_info.risk_reasons,
                "liquidity_usd": round(usd_liq, 2),
            })

        # ── Professional v2: parallel deep analysis (runs for ALL tokens) ──
        # v8 Priority Gate: skip heavy modules if token is confirmed honeypot
        _skip_heavy = token_info.is_honeypot and (token_info._honeypot_ok or token_info._goplus_ok)
        if _skip_heavy:
            logger.info(f"⏩ Priority gate: skipping heavy analysis — {token_info.symbol} confirmed HONEYPOT")
            await self._emit("scan_info", {
                "message": f"⏩ {token_info.symbol}: honeypot confirmed, heavy analysis skipped",
            })

        try:
            v2_tasks = []
            # Pump score analysis (no extra API calls — uses existing TokenInfo)
            if self.settings.get("enable_pump_score", True):
                v2_tasks.append(("pump", asyncio.ensure_future(
                    self.pump_analyzer.analyze(token_info, usd_liq, pair_address)
                )))
            # Swap simulation: on-chain honeypot verification — skip if already confirmed honeypot
            if self.settings.get("enable_swap_sim", True) and self.swap_simulator and not _skip_heavy:
                v2_tasks.append(("sim", asyncio.ensure_future(
                    self.swap_simulator.simulate(new_token)
                )))
            # Bytecode analysis: opcode-level malware detection — skip if confirmed honeypot
            if self.settings.get("enable_bytecode", True) and self.bytecode_analyzer and not _skip_heavy:
                v2_tasks.append(("bytecode", asyncio.ensure_future(
                    self.bytecode_analyzer.analyze_bytecode(new_token)
                )))
            # Smart money: check if tracked whales already bought
            if self.settings.get("enable_smart_money", False) and self.smart_money and not _skip_heavy:
                v2_tasks.append(("smart", asyncio.ensure_future(
                    self.smart_money.check_smart_buyers(new_token, pair_address)
                )))

            if v2_tasks:
                results = await asyncio.gather(
                    *[t[1] for t in v2_tasks], return_exceptions=True
                )
                for (label, _), result in zip(v2_tasks, results):
                    if isinstance(result, Exception):
                        logger.warning(f"v2 {label} analysis failed for {token_info.symbol}: {result}")
                        continue
                    if label == "pump" and result:
                        token_info.pump_score = result.total_score
                        token_info.pump_grade = result.grade
                        token_info.pump_signals = result.signals
                        # v3 fields — extract from the same result (no double call)
                        token_info.pump_mcap_score = getattr(result, 'mcap_score', 0)
                        token_info.pump_market_cap_usd = getattr(result, 'market_cap_usd', 0.0)
                        token_info.pump_holder_growth_rate = getattr(result, 'holder_growth_rate', 0.0)
                        token_info.pump_lp_growth_percent = getattr(result, 'lp_growth_percent', 0.0)
                        token_info.pump_holder_growth_score = getattr(result, 'holder_growth_score', 0)
                        token_info.pump_lp_growth_score = getattr(result, 'lp_growth_score', 0)
                    elif label == "sim" and result:
                        token_info.sim_can_buy = result.can_buy
                        token_info.sim_can_sell = result.can_sell
                        token_info.sim_buy_tax = result.buy_tax_percent
                        token_info.sim_sell_tax = result.sell_tax_percent
                        token_info.sim_is_honeypot = result.is_honeypot
                        token_info.sim_honeypot_reason = result.honeypot_reason
                        token_info.sim_gas_buy = result.gas_estimate_buy
                        token_info.sim_gas_sell = result.gas_estimate_sell
                        token_info._simulation_ok = True
                    elif label == "bytecode" and result:
                        token_info.bytecode_has_selfdestruct = result.get("has_selfdestruct", False)
                        token_info.bytecode_has_delegatecall = result.get("has_delegatecall", False)
                        token_info.bytecode_is_proxy = result.get("is_minimal_proxy", False)
                        token_info.bytecode_size = result.get("bytecode_size", 0)
                        token_info.bytecode_flags = result.get("risk_flags", [])
                        token_info._bytecode_ok = True
                    elif label == "smart" and result is not None:
                        # result is list[SmartMoneySignal]
                        token_info.smart_money_buyers = len(result)
                        token_info.smart_money_confidence = (
                            max(s.confidence for s in result) / 100.0 if result else 0.0
                        )

                # (v3 pump fields already extracted above — no redundant double call)

                # ── Professional v3: Dev Tracker ──
                dev_result = None
                if self.settings.get("enable_dev_tracker", True):
                    try:
                        dev_result = await self.dev_tracker.check_creator(new_token, token_info)
                        if dev_result:
                            token_info.dev_is_tracked = dev_result.is_tracked
                            token_info.dev_score = dev_result.dev_score
                            token_info.dev_label = dev_result.dev_label
                            token_info.dev_total_launches = dev_result.total_launches if hasattr(dev_result, 'total_launches') else 0
                            token_info.dev_successful_launches = dev_result.successful_launches if hasattr(dev_result, 'successful_launches') else 0
                            token_info.dev_rug_pulls = dev_result.rug_pulls if hasattr(dev_result, 'rug_pulls') else 0
                            token_info.dev_best_multiplier = dev_result.best_multiplier if hasattr(dev_result, 'best_multiplier') else 0.0
                            token_info.dev_is_serial_scammer = dev_result.is_serial_scammer
                            token_info._dev_ok = True
                            # Record this launch for future reputation
                            await self.dev_tracker.record_launch(
                                dev_result.creator_address, new_token,
                                token_info.symbol, usd_liq,
                                token_info.lp_locked,
                            )
                    except Exception as e:
                        logger.warning(f"v3 dev tracker failed for {token_info.symbol}: {e}")

                # ── Professional v3: Risk Engine ──
                risk_decision = None
                if self.settings.get("enable_risk_engine", True):
                    try:
                        # Gather results for risk engine (use what we have)
                        pump_for_risk = None
                        if token_info.pump_score > 0:
                            pump_for_risk = type('PumpResult', (), {
                                'total_score': token_info.pump_score,
                                'grade': token_info.pump_grade,
                            })()
                        sim_for_risk = None
                        if token_info._simulation_ok:
                            sim_for_risk = type('SimResult', (), {
                                'is_honeypot': token_info.sim_is_honeypot,
                                'buy_tax_percent': token_info.sim_buy_tax,
                                'sell_tax_percent': token_info.sim_sell_tax,
                            })()
                        bytecode_for_risk = None
                        if token_info._bytecode_ok:
                            bytecode_for_risk = {
                                'has_selfdestruct': token_info.bytecode_has_selfdestruct,
                                'has_delegatecall': token_info.bytecode_has_delegatecall,
                                'is_minimal_proxy': token_info.bytecode_is_proxy,
                                'risk_flags': token_info.bytecode_flags,
                            }
                        smart_for_risk = []

                        risk_decision = await self.risk_engine.evaluate(
                            token_info=token_info,
                            pump_result=pump_for_risk,
                            dev_result=dev_result,
                            smart_signals=smart_for_risk,
                            sim_result=sim_for_risk,
                            bytecode_result=bytecode_for_risk,
                            liquidity_usd=usd_liq,
                        )
                        if risk_decision:
                            token_info.risk_engine_score = risk_decision.final_score
                            token_info.risk_engine_action = risk_decision.action
                            token_info.risk_engine_confidence = risk_decision.confidence
                            token_info.risk_engine_hard_stop = risk_decision.hard_stop
                            token_info.risk_engine_hard_stop_reason = risk_decision.hard_stop_reason if hasattr(risk_decision, 'hard_stop_reason') else ""
                            token_info.risk_engine_signals = risk_decision.signals
                            token_info._risk_engine_ok = True
                    except Exception as e:
                        logger.warning(f"v3 risk engine failed for {token_info.symbol}: {e}")

                # Check trade executor availability
                if self.settings.get("enable_trade_executor", False) and self.trade_executor:
                    token_info.backend_buy_available = True

                # Re-emit token_detected with updated v2+v3 data (always, not just liquid)
                await self._emit("token_detected",
                    self._build_token_event_data(new_token, pair_address, token_info, native_liq, usd_liq, has_liquidity, block)
                )
                logger.info(
                    f"v2+v3 analysis for {token_info.symbol}: "
                    f"pump={token_info.pump_score}({token_info.pump_grade}) "
                    f"sim_hp={token_info.sim_is_honeypot} "
                    f"bytecode_flags={token_info.bytecode_flags} "
                    f"smart={token_info.smart_money_buyers} "
                    f"dev={token_info.dev_score}({token_info.dev_label}) "
                    f"risk={token_info.risk_engine_score}({token_info.risk_engine_action})"
                )
        except Exception as e:
            logger.error(f"v2+v3 analysis pipeline error for {token_info.symbol}: {e}")

        # ── Professional v5: Advanced Intelligence ──────────────
        # v8 Priority Gate: skip v5/v6/v7 heavy analysis for confirmed honeypots
        if _skip_heavy:
            logger.info(f"⏩ Priority gate: skipping v5/v6/v7 analysis — {token_info.symbol} confirmed HONEYPOT")

        try:
            v5_tasks = []

            # Proxy detection (async)
            if self.settings.get("enable_proxy_detector", True) and not _skip_heavy:
                v5_tasks.append(("proxy", asyncio.ensure_future(
                    self.proxy_detector.analyze(new_token)
                )))

            # Stress test (multi-amount slippage)
            if self.settings.get("enable_stress_test", True) and self.stress_tester and not _skip_heavy:
                v5_tasks.append(("stress", asyncio.ensure_future(
                    self.stress_tester.stress_test(new_token)
                )))

            # ML Pump/Dump prediction (sync → wrap in executor)
            if self.settings.get("enable_ml_predictor", True) and not _skip_heavy:
                loop = asyncio.get_event_loop()
                v5_tasks.append(("ml", asyncio.ensure_future(
                    loop.run_in_executor(None, self.ml_predictor.predict, token_info)
                )))

            # Social sentiment (async HTTP calls)
            if self.settings.get("enable_social_sentiment", True) and not _skip_heavy:
                v5_tasks.append(("social", asyncio.ensure_future(
                    self.social_sentiment.analyze(
                        new_token,
                        token_symbol=token_info.symbol,
                        token_info=token_info,
                    )
                )))

            # Anomaly detection (sync → wrap in executor)
            if self.settings.get("enable_anomaly_detector", True) and not _skip_heavy:
                loop = asyncio.get_event_loop()
                v5_tasks.append(("anomaly", asyncio.ensure_future(
                    loop.run_in_executor(None, self.anomaly_detector.detect, token_info)
                )))

            # Whale activity analysis
            if self.settings.get("enable_whale_activity", True) and self.smart_money and not _skip_heavy:
                v5_tasks.append(("whale", asyncio.ensure_future(
                    self.smart_money.analyze_whale_activity(
                        new_token, pair_address,
                        creator_address=token_info.creator_address,
                        native_price_usd=self.native_price_usd,
                    )
                )))

            # Volatility-based slippage (async, uses DexScreener data)
            if self.settings.get("enable_volatility_slippage", True) and not _skip_heavy:
                dex_data = {
                    "price_change_m5": getattr(token_info, 'dexscreener_price_change_m5', 0),
                    "price_change_h1": getattr(token_info, 'dexscreener_price_change_h1', 0),
                    "price_change_h6": getattr(token_info, 'dexscreener_price_change_h6', 0),
                }
                v5_tasks.append(("volatility", asyncio.ensure_future(
                    self.volatility_slippage.calculate(new_token, dexscreener_data=dex_data)
                )))

            if v5_tasks:
                v5_results = await asyncio.gather(
                    *[t[1] for t in v5_tasks], return_exceptions=True
                )
                for (label, _), result in zip(v5_tasks, v5_results):
                    if isinstance(result, Exception):
                        logger.warning(f"v5 {label} failed for {token_info.symbol}: {result}")
                        continue

                    if label == "proxy" and result:
                        token_info.proxy_is_proxy = result.is_proxy
                        token_info.proxy_type = result.proxy_type
                        token_info.proxy_risk_level = result.risk_level
                        token_info.proxy_has_multisig = result.has_multisig
                        token_info.proxy_has_timelock = result.has_timelock
                        token_info.proxy_signals = result.signals
                        token_info._proxy_ok = True

                    elif label == "stress" and result:
                        token_info.stress_max_safe_amount = result.max_safe_amount_wei / 10**18 if result.max_safe_amount_wei else 0
                        token_info.stress_liquidity_depth = result.liquidity_depth_score
                        token_info.stress_avg_slippage = result.avg_slippage_pct
                        token_info._stress_ok = True

                    elif label == "ml" and result:
                        token_info.ml_pump_score = result.ml_score
                        token_info.ml_pump_confidence = result.confidence
                        # Derive label from score (no label field on PumpDumpPrediction)
                        if result.ml_score >= 70:
                            token_info.ml_pump_label = "safe"
                        elif result.ml_score >= 40:
                            token_info.ml_pump_label = "neutral"
                        elif result.ml_score >= 20:
                            token_info.ml_pump_label = "warning"
                        else:
                            token_info.ml_pump_label = "danger"
                        token_info._ml_pump_ok = True

                    elif label == "social" and result:
                        token_info.social_sentiment_score = result.sentiment_score
                        token_info.social_sentiment_label = result.sentiment_label
                        token_info.social_platforms_active = result.platforms_active
                        token_info.social_signals = result.signals
                        token_info._social_ok = True

                    elif label == "anomaly" and result:
                        token_info.anomaly_is_anomalous = result.is_anomalous
                        token_info.anomaly_score = result.anomaly_score
                        token_info.anomaly_type = result.anomaly_type
                        token_info._anomaly_ok = True

                    elif label == "whale" and result:
                        token_info.whale_total_buys = result.total_whale_buys
                        token_info.whale_total_sells = result.total_whale_sells
                        token_info.whale_net_flow = result.net_whale_flow
                        token_info.whale_coordinated = result.coordinated_buying
                        token_info.whale_dev_dumping = result.dev_dumping
                        token_info.whale_concentration_pct = result.whale_concentration_pct
                        token_info.whale_risk_score = result.risk_score
                        token_info.whale_signals = result.signals
                        token_info._whale_ok = True

                    elif label == "volatility" and result:
                        token_info.volatility_score = result.volatility_score
                        token_info.volatility_recommended_slippage = result.recommended_slippage_pct
                        token_info._volatility_ok = True

                # v5: Dev ML fields (from the dev_result already obtained in v3)
                if dev_result and hasattr(dev_result, 'ml_reputation_score'):
                    token_info.dev_ml_reputation = dev_result.ml_reputation_score
                    token_info.dev_ml_cluster = dev_result.ml_cluster
                    token_info.dev_ml_confidence = dev_result.ml_confidence

                # v5: Register with dynamic scanner for continuous monitoring
                if self.settings.get("enable_dynamic_scanner", True):
                    try:
                        await self.dynamic_scanner.register(
                            new_token,
                            symbol=token_info.symbol,
                            pair_address=pair_address,
                            is_proxy=token_info.proxy_is_proxy,
                        )
                    except Exception as e:
                        logger.debug(f"Dynamic scanner registration failed: {e}")

                # Re-emit with v5 data
                await self._emit("token_detected",
                    self._build_token_event_data(new_token, pair_address, token_info, native_liq, usd_liq, has_liquidity, block)
                )
                logger.info(
                    f"v5 analysis for {token_info.symbol}: "
                    f"proxy={token_info.proxy_type}({token_info.proxy_risk_level}) "
                    f"stress_depth={token_info.stress_liquidity_depth} "
                    f"ml={token_info.ml_pump_score}({token_info.ml_pump_label}) "
                    f"social={token_info.social_sentiment_score}({token_info.social_sentiment_label}) "
                    f"anomaly={token_info.anomaly_score:.2f} "
                    f"whale_flow={token_info.whale_net_flow:.2f}"
                )
        except Exception as e:
            logger.error(f"v5 analysis pipeline error for {token_info.symbol}: {e}")

        # ═══════════════════════════════════════════════════════
        #  v6 Analysis Pipeline — MEV, Multi-DEX, AI Optimizer
        # ═══════════════════════════════════════════════════════
        try:
            # v6: MEV threat analysis
            if self.settings.get("enable_mev_protection", False) and not _skip_heavy:
                try:
                    buy_amount = self.settings.get("buy_amount_native", 0.05)
                    mev_analysis = await self.mev_protector.analyze_threat(new_token, buy_amount)
                    token_info.mev_threat_level = mev_analysis.threat_level
                    token_info.mev_frontrun_risk = mev_analysis.frontrun_risk
                    token_info.mev_sandwich_risk = mev_analysis.sandwich_risk
                    token_info.mev_strategy_used = mev_analysis.recommended_strategy
                    token_info._mev_ok = True
                except Exception as e:
                    logger.debug(f"MEV analysis failed for {token_info.symbol}: {e}")

            # v6: Multi-DEX best route
            if self.settings.get("enable_multi_dex", True) and not _skip_heavy:
                try:
                    buy_amount = self.settings.get("buy_amount_native", 0.05)
                    best_route = await self.multi_dex_router.find_best_route(
                        self.chain_id, new_token, buy_amount, direction="buy"
                    )
                    if best_route and best_route.amount_out > 0:
                        token_info.best_dex_name = best_route.dex_name
                        token_info.best_dex_amount_out = best_route.amount_out / 1e18
                        token_info.best_dex_route = best_route.path
                        token_info._multi_dex_ok = True
                except Exception as e:
                    logger.debug(f"Multi-DEX routing failed for {token_info.symbol}: {e}")

            # v6: AI regime & suggestions
            if self.settings.get("enable_ai_optimizer", True):
                try:
                    regime = self.strategy_optimizer.detect_market_regime()
                    token_info.ai_regime = regime.regime
                    suggestion = self.strategy_optimizer.get_suggestion()
                    if suggestion.get("proposed_params"):
                        token_info.ai_suggested_tp = suggestion["proposed_params"].get("take_profit_pct", 0)
                        token_info.ai_suggested_sl = suggestion["proposed_params"].get("stop_loss_pct", 0)
                except Exception as e:
                    logger.debug(f"AI optimizer context failed: {e}")

            # Re-emit with v6 data
            await self._emit("token_detected",
                self._build_token_event_data(new_token, pair_address, token_info, native_liq, usd_liq, has_liquidity, block)
            )
            logger.info(
                f"v6 analysis for {token_info.symbol}: "
                f"mev={token_info.mev_threat_level} "
                f"best_dex={token_info.best_dex_name or 'default'} "
                f"ai_regime={token_info.ai_regime}"
            )
        except Exception as e:
            logger.error(f"v6 analysis pipeline error for {token_info.symbol}: {e}")

        # ═══════════════════════════════════════════════════════
        #  v7 Analysis Pipeline — RL, Orderflow, Simulator,
        #  Strategy, Launch Scanner, Mempool v7, Whale Graph
        # ═══════════════════════════════════════════════════════
        try:
            v7_tasks = []

            # v7: Reinforcement Learning decision
            if self.settings.get("enable_rl_learner", True) and not _skip_heavy:
                async def _rl_analyze():
                    try:
                        market_data = {
                            "pump_score": token_info.pump_score,
                            "risk_engine_score": token_info.risk_engine_score,
                            "ml_pump_score": token_info.ml_pump_score,
                            "social_sentiment": token_info.social_sentiment_score,
                            "volatility": token_info.volatility_score,
                            "whale_risk": token_info.whale_risk_score,
                            "mev_frontrun_risk": token_info.mev_frontrun_risk,
                            "lp_lock_percent": token_info.lp_lock_percent,
                            "buy_tax": token_info.buy_tax,
                            "sell_tax": token_info.sell_tax,
                        }
                        return "rl", self.rl_learner.decide(token_info, market_data)
                    except Exception as e:
                        logger.debug(f"RL analysis failed for {token_info.symbol}: {e}")
                        return "rl", None
                v7_tasks.append(_rl_analyze())

            # v7: Orderflow analysis
            if self.settings.get("enable_orderflow", True) and not _skip_heavy:
                async def _orderflow_analyze():
                    try:
                        return "orderflow", await self.orderflow_analyzer.analyze(new_token, pair_address)
                    except Exception as e:
                        logger.debug(f"Orderflow analysis failed for {token_info.symbol}: {e}")
                        return "orderflow", None
                v7_tasks.append(_orderflow_analyze())

            # v7: Market simulation
            if self.settings.get("enable_market_simulator", True) and not _skip_heavy:
                async def _sim_analyze():
                    try:
                        buy_amount = self.settings.get("buy_amount_native", 0.05)
                        tp = self.settings.get("take_profit", 50)
                        sl = self.settings.get("stop_loss", 20)
                        hold_h = self.settings.get("max_hold_hours", 24) or 24
                        mev_risk = token_info.mev_frontrun_risk
                        return "sim", await self.market_simulator.simulate(
                            new_token, pair_address, buy_amount, tp, sl, hold_h, mev_risk
                        )
                    except Exception as e:
                        logger.debug(f"Market simulation failed for {token_info.symbol}: {e}")
                        return "sim", None
                v7_tasks.append(_sim_analyze())

            # v7: Auto strategy evaluation
            if self.settings.get("enable_auto_strategy", True) and not _skip_heavy:
                async def _strategy_analyze():
                    try:
                        return "strategy", self.auto_strategy.evaluate_token(token_info)
                    except Exception as e:
                        logger.debug(f"Strategy evaluation failed for {token_info.symbol}: {e}")
                        return "strategy", None
                v7_tasks.append(_strategy_analyze())

            # v7: Launch scanner analysis
            if self.settings.get("enable_launch_scanner", True):
                async def _launch_analyze():
                    try:
                        return "launch", await self.launch_scanner.scan_token(new_token)
                    except Exception as e:
                        logger.debug(f"Launch scanner failed for {token_info.symbol}: {e}")
                        return "launch", None
                v7_tasks.append(_launch_analyze())

            # v7: Mempool v7 analysis
            if self.settings.get("enable_mempool_v7", True):
                async def _mempool_v7_analyze():
                    try:
                        return "mempool_v7", await self.mempool_analyzer_v7.analyze_token(new_token)
                    except Exception as e:
                        logger.debug(f"Mempool v7 analysis failed for {token_info.symbol}: {e}")
                        return "mempool_v7", None
                v7_tasks.append(_mempool_v7_analyze())

            # v7: Whale network graph
            if self.settings.get("enable_whale_graph", True) and not _skip_heavy:
                async def _whale_graph_analyze():
                    try:
                        trades = []
                        if token_info._whale_ok and token_info.whale_signals:
                            for sig in token_info.whale_signals[:20]:
                                if isinstance(sig, dict):
                                    trades.append(sig)
                        return "whale_graph", await self.whale_graph.analyze_token(new_token, trades)
                    except Exception as e:
                        logger.debug(f"Whale graph analysis failed for {token_info.symbol}: {e}")
                        return "whale_graph", None
                v7_tasks.append(_whale_graph_analyze())

            # Execute all v7 analyses in parallel
            if v7_tasks:
                v7_results = await asyncio.gather(*v7_tasks, return_exceptions=True)
                for result in v7_results:
                    if isinstance(result, Exception):
                        logger.debug(f"v7 task exception: {result}")
                        continue
                    label, data = result
                    if data is None:
                        continue

                    if label == "rl":
                        token_info.rl_decision = data.action.value if hasattr(data.action, 'value') else str(data.action)
                        token_info.rl_confidence = data.confidence
                        token_info.rl_action = data.reasoning if hasattr(data, 'reasoning') else ""
                        token_info.rl_suggested_tp = data.suggested_tp if hasattr(data, 'suggested_tp') else 0
                        token_info.rl_suggested_sl = data.suggested_sl if hasattr(data, 'suggested_sl') else 0
                        token_info.rl_suggested_amount = data.suggested_amount if hasattr(data, 'suggested_amount') else 0
                        token_info._rl_ok = True

                    elif label == "orderflow":
                        token_info.orderflow_organic_score = data.organic_score if hasattr(data, 'organic_score') else 0
                        token_info.orderflow_bot_pct = data.bot_percentage if hasattr(data, 'bot_percentage') else 0
                        token_info.orderflow_manipulation_risk = data.manipulation_risk if hasattr(data, 'manipulation_risk') else 0
                        token_info.orderflow_patterns = data.detected_patterns if hasattr(data, 'detected_patterns') else []
                        token_info._orderflow_ok = True

                    elif label == "sim":
                        token_info.sim_v7_risk_score = data.risk_score if hasattr(data, 'risk_score') else 0
                        token_info.sim_v7_recommendation = data.recommendation if hasattr(data, 'recommendation') else "unknown"
                        token_info.sim_v7_slippage_pct = data.slippage.actual_slippage_pct if hasattr(data, 'slippage') and data.slippage else 0
                        token_info.sim_v7_mev_vulnerable = data.mev_sim.is_vulnerable if hasattr(data, 'mev_sim') and data.mev_sim else False
                        token_info.sim_v7_expected_pnl = data.monte_carlo.expected_pnl_pct if hasattr(data, 'monte_carlo') and data.monte_carlo else 0
                        token_info._sim_v7_ok = True

                    elif label == "strategy":
                        if isinstance(data, dict):
                            token_info.strategy_decision = data.get("decision", "none")
                            token_info.strategy_confidence = data.get("confidence", 0)
                            token_info.strategy_name = data.get("strategy_name", "")
                            token_info._strategy_ok = True

                    elif label == "launch":
                        if isinstance(data, dict):
                            token_info.launch_risk_score = data.get("risk_score", 50)
                            token_info.launch_stage = data.get("stage", "unknown")
                            token_info.launch_snipe_priority = data.get("snipe_priority", 0)
                            token_info._launch_ok = True

                    elif label == "mempool_v7":
                        if isinstance(data, dict):
                            token_info.mempool_v7_net_pressure = data.get("net_pressure", 0)
                            token_info.mempool_v7_frontrun_risk = data.get("frontrun_risk", 0)
                            token_info.mempool_v7_pending_buys = data.get("pending_buys", 0)
                            token_info.mempool_v7_pending_sells = data.get("pending_sells", 0)
                            token_info.mempool_v7_alerts = data.get("alerts", [])
                            token_info._mempool_v7_ok = True

                    elif label == "whale_graph":
                        if isinstance(data, dict):
                            token_info.whale_network_coordination = data.get("coordination_score", 0)
                            token_info.whale_network_sybil_risk = data.get("sybil_risk", 0)
                            token_info.whale_smart_money_sentiment = data.get("smart_money_sentiment", "neutral")
                            token_info.whale_network_clusters = data.get("cluster_count", 0)
                            token_info._whale_network_ok = True

            # Re-emit with v7 data
            await self._emit("token_detected",
                self._build_token_event_data(new_token, pair_address, token_info, native_liq, usd_liq, has_liquidity, block)
            )
            logger.info(
                f"v7 analysis for {token_info.symbol}: "
                f"rl={token_info.rl_decision}({token_info.rl_confidence:.2f}) "
                f"orderflow_organic={token_info.orderflow_organic_score:.0f} "
                f"sim_risk={token_info.sim_v7_risk_score:.0f} "
                f"strategy={token_info.strategy_decision} "
                f"launch_priority={token_info.launch_snipe_priority} "
                f"mempool_pressure={token_info.mempool_v7_net_pressure:.2f} "
                f"whale_sybil={token_info.whale_network_sybil_risk:.2f}"
            )
        except Exception as e:
            logger.error(f"v7 analysis pipeline error for {token_info.symbol}: {e}")

        # Check if safe enough
        passes_safety = True
        if self.settings["only_safe"] and token_info.risk == "danger":
            passes_safety = False
        if token_info.buy_tax > self.settings["max_buy_tax"]:
            passes_safety = False
        if token_info.sell_tax > self.settings["max_sell_tax"]:
            passes_safety = False
        if token_info.is_honeypot:
            passes_safety = False

        # ── Hardened contract verification ──
        if token_info._goplus_ok:
            if not token_info.is_open_source:
                passes_safety = False
                logger.info(f"Skipping {token_info.symbol}: code NOT verified (not open source)")
            if token_info.is_proxy:
                passes_safety = False
                logger.info(f"Skipping {token_info.symbol}: proxy contract — can change logic")
            if token_info.has_hidden_owner:
                passes_safety = False
                logger.info(f"Skipping {token_info.symbol}: hidden owner")
            if token_info.can_take_back_ownership:
                passes_safety = False
                logger.info(f"Skipping {token_info.symbol}: can reclaim ownership")
            if token_info.is_airdrop_scam:
                passes_safety = False
                logger.info(f"Skipping {token_info.symbol}: airdrop scam")
            if not token_info.is_true_token:
                passes_safety = False
                logger.info(f"Skipping {token_info.symbol}: fake ERC-20")
            if token_info.top_holder_percent >= 30:
                passes_safety = False
                logger.info(f"Skipping {token_info.symbol}: top holder has {token_info.top_holder_percent}% supply")
            if token_info.creator_percent >= 20:
                passes_safety = False
                logger.info(f"Skipping {token_info.symbol}: creator retains {token_info.creator_percent}% supply")

        # LP must be locked sufficiently to prevent rug pull
        if not token_info.lp_locked:
            passes_safety = False
            logger.info(f"Skipping {token_info.symbol}: LP not locked")
        elif token_info.lp_lock_percent < 80:
            passes_safety = False
            logger.info(f"Skipping {token_info.symbol}: LP lock only {token_info.lp_lock_percent}% (need >=80%)")
        elif token_info.lp_lock_hours_remaining < 24:
            passes_safety = False
            logger.info(f"Skipping {token_info.symbol}: LP lock only {token_info.lp_lock_hours_remaining}h remaining (need >24h)")

        # ── Professional v2 safety gates ──
        # Pump score gate
        if self.settings.get("enable_pump_score", True) and token_info.pump_score > 0:
            min_pump = self.settings.get("min_pump_score", 40)
            if token_info.pump_grade == "AVOID":
                passes_safety = False
                logger.info(f"Skipping {token_info.symbol}: pump grade AVOID (score={token_info.pump_score})")
            elif token_info.pump_score < min_pump:
                passes_safety = False
                logger.info(f"Skipping {token_info.symbol}: pump score {token_info.pump_score} < {min_pump}")

        # Swap simulation gate — if simulation succeeded, use its results
        if token_info._simulation_ok:
            if token_info.sim_is_honeypot:
                passes_safety = False
                logger.info(f"Skipping {token_info.symbol}: swap simulation honeypot — {token_info.sim_honeypot_reason}")
            if token_info.sim_buy_tax > self.settings["max_buy_tax"]:
                passes_safety = False
                logger.info(f"Skipping {token_info.symbol}: simulated buy tax {token_info.sim_buy_tax}% > {self.settings['max_buy_tax']}%")
            if token_info.sim_sell_tax > self.settings["max_sell_tax"]:
                passes_safety = False
                logger.info(f"Skipping {token_info.symbol}: simulated sell tax {token_info.sim_sell_tax}% > {self.settings['max_sell_tax']}%")

        # Bytecode gate — block dangerous opcodes
        if token_info._bytecode_ok and token_info.bytecode_flags:
            if token_info.bytecode_has_selfdestruct:
                passes_safety = False
                logger.info(f"Skipping {token_info.symbol}: SELFDESTRUCT opcode found")
            if token_info.bytecode_has_delegatecall:
                passes_safety = False
                logger.info(f"Skipping {token_info.symbol}: DELEGATECALL opcode — can be hijacked")

        # Price history check: reject severe dumps AND reject already-pumped tokens
        if token_info._dexscreener_ok:
            # Reject severe dumps (rug pull signs)
            if token_info.dexscreener_price_change_h24 <= -50:
                passes_safety = False
                logger.info(f"Skipping {token_info.symbol}: 24h dump {token_info.dexscreener_price_change_h24}%")
            if token_info.dexscreener_price_change_h6 <= -40:
                passes_safety = False
                logger.info(f"Skipping {token_info.symbol}: 6h dump {token_info.dexscreener_price_change_h6}%")
            if token_info.dexscreener_price_change_h1 <= -25:
                passes_safety = False
                logger.info(f"Skipping {token_info.symbol}: 1h dump {token_info.dexscreener_price_change_h1}%")
            # Reject already pumped (bad entry — need to buy LOW)
            if token_info.dexscreener_price_change_m5 >= 30:
                passes_safety = False
                logger.info(f"Skipping {token_info.symbol}: already pumped +{token_info.dexscreener_price_change_m5:.0f}% in 5m")
            if token_info.dexscreener_price_change_h1 >= 50:
                passes_safety = False
                logger.info(f"Skipping {token_info.symbol}: already pumped +{token_info.dexscreener_price_change_h1:.0f}% in 1h")

        # ── Professional v3 safety gates ──
        # Risk Engine hard stop — overrides everything
        if token_info._risk_engine_ok and token_info.risk_engine_hard_stop:
            passes_safety = False
            logger.info(f"HARD STOP {token_info.symbol}: {token_info.risk_engine_hard_stop_reason}")

        # Dev tracker — serial scammer block
        if token_info._dev_ok and token_info.dev_is_serial_scammer:
            passes_safety = False
            logger.info(f"Skipping {token_info.symbol}: serial scammer dev ({token_info.dev_rug_pulls} past rugs)")

        # Risk Engine score gate
        if token_info._risk_engine_ok and self.settings.get("enable_risk_engine", True):
            min_risk = self.settings.get("risk_engine_min_score", 50)
            allowed_action = self.settings.get("risk_engine_auto_action", "BUY")
            action_order = ["STRONG_BUY", "BUY", "WATCH", "WEAK", "IGNORE"]
            allowed_idx = action_order.index(allowed_action) if allowed_action in action_order else 1
            current_idx = action_order.index(token_info.risk_engine_action) if token_info.risk_engine_action in action_order else 4
            if token_info.risk_engine_score < min_risk:
                passes_safety = False
                logger.info(f"Skipping {token_info.symbol}: risk engine score {token_info.risk_engine_score} < {min_risk}")
            elif current_idx > allowed_idx:
                passes_safety = False
                logger.info(f"Skipping {token_info.symbol}: risk action {token_info.risk_engine_action} below threshold {allowed_action}")

        # ── Professional v5 safety gates ──
        # Proxy danger gate
        if token_info._proxy_ok and token_info.proxy_risk_level == "dangerous":
            passes_safety = False
            logger.info(f"Skipping {token_info.symbol}: proxy danger — {token_info.proxy_type} without multisig/timelock")

        # Dynamic scanner blocked gate
        if token_info._dynamic_scan_ok and token_info.dynamic_scan_blocked:
            passes_safety = False
            logger.info(f"Skipping {token_info.symbol}: dynamic scanner blocked — {token_info.dynamic_scan_block_reason}")

        # ML pump/dump gate
        if token_info._ml_pump_ok and self.settings.get("enable_ml_predictor", True):
            ml_min = self.settings.get("ml_min_score", 30)
            if token_info.ml_pump_label == "danger":
                passes_safety = False
                logger.info(f"Skipping {token_info.symbol}: ML dump prediction (score={token_info.ml_pump_score})")
            elif token_info.ml_pump_score < ml_min:
                passes_safety = False
                logger.info(f"Skipping {token_info.symbol}: ML score {token_info.ml_pump_score} < {ml_min}")

        # Anomaly detection gate
        if token_info._anomaly_ok and token_info.anomaly_is_anomalous and token_info.anomaly_score >= 0.8:
            passes_safety = False
            logger.info(f"Skipping {token_info.symbol}: severe anomaly detected (score={token_info.anomaly_score:.2f}, type={token_info.anomaly_type})")

        # Whale dev-dump gate
        if token_info._whale_ok and token_info.whale_dev_dumping:
            passes_safety = False
            logger.info(f"Skipping {token_info.symbol}: dev wallet dumping detected")

        # Whale coordinated buying – warning but not block (can be a good sign)
        if token_info._whale_ok and token_info.whale_coordinated:
            logger.info(f"⚠️ {token_info.symbol}: coordinated whale buying detected")

        # ── Professional v6 safety gates ──
        # MEV threat gate — block high-risk tokens with active bots
        if token_info._mev_ok and self.settings.get("enable_mev_protection", False):
            if token_info.mev_threat_level == "critical":
                passes_safety = False
                logger.info(f"Skipping {token_info.symbol}: MEV critical threat (frontrun={token_info.mev_frontrun_risk:.0%}, sandwich={token_info.mev_sandwich_risk:.0%})")
            elif token_info.mev_threat_level == "high" and token_info.mev_sandwich_risk > 0.7:
                passes_safety = False
                logger.info(f"Skipping {token_info.symbol}: MEV high sandwich risk ({token_info.mev_sandwich_risk:.0%})")

        # Multi-DEX gate — warn if no route found (but don't block)
        if self.settings.get("enable_multi_dex", True) and not token_info._multi_dex_ok:
            logger.debug(f"⚠️ {token_info.symbol}: no multi-DEX route found, using default router")

        # ── Professional v7 safety gates ──
        # RL learner gate — block if RL says sell with high confidence
        if token_info._rl_ok and self.settings.get("enable_rl_learner", True):
            rl_min_conf = self.settings.get("rl_min_confidence", 0.3)
            if token_info.rl_decision == "sell" and token_info.rl_confidence >= rl_min_conf:
                passes_safety = False
                logger.info(f"Skipping {token_info.symbol}: RL agent says SELL (confidence={token_info.rl_confidence:.2f})")

        # Orderflow manipulation gate
        if token_info._orderflow_ok and self.settings.get("enable_orderflow", True):
            max_manip = self.settings.get("orderflow_max_manipulation", 0.7)
            if token_info.orderflow_manipulation_risk >= max_manip:
                passes_safety = False
                logger.info(f"Skipping {token_info.symbol}: orderflow manipulation risk {token_info.orderflow_manipulation_risk:.2f} >= {max_manip}")

        # Market simulator risk gate
        if token_info._sim_v7_ok and self.settings.get("enable_market_simulator", True):
            max_sim_risk = self.settings.get("sim_v7_max_risk", 70)
            if token_info.sim_v7_risk_score >= max_sim_risk:
                passes_safety = False
                logger.info(f"Skipping {token_info.symbol}: simulation risk {token_info.sim_v7_risk_score:.0f} >= {max_sim_risk}")
            if token_info.sim_v7_recommendation == "avoid":
                passes_safety = False
                logger.info(f"Skipping {token_info.symbol}: simulator recommends AVOID")

        # Whale network sybil gate — block if high sybil risk
        if token_info._whale_network_ok and self.settings.get("enable_whale_graph", True):
            if token_info.whale_network_sybil_risk >= 0.7:
                passes_safety = False
                logger.info(f"Skipping {token_info.symbol}: sybil network risk {token_info.whale_network_sybil_risk:.2f}")

        # Mempool v7 frontrun gate
        if token_info._mempool_v7_ok and self.settings.get("enable_mempool_v7", True):
            if token_info.mempool_v7_frontrun_risk >= 0.8:
                passes_safety = False
                logger.info(f"Skipping {token_info.symbol}: mempool frontrun risk {token_info.mempool_v7_frontrun_risk:.2f}")

        if passes_safety:
            # Auto-calculate max_hold from LP lock duration (sell 1h before expiry)
            auto_hold_h = 0
            if token_info.lp_lock_hours_remaining > 1:
                auto_hold_h = round(token_info.lp_lock_hours_remaining - 1, 1)

            await self._emit("snipe_opportunity", {
                "token": new_token,
                "symbol": token_info.symbol,
                "name": token_info.name,
                "risk": token_info.risk,
                "liquidity_usd": round(usd_liq, 2),
                "pair": pair_address,
                "auto_buy": self.settings["auto_buy"],
                "lp_locked": token_info.lp_locked,
                "lp_lock_hours": token_info.lp_lock_hours_remaining,
                "lp_lock_percent": token_info.lp_lock_percent,
                "auto_hold_hours": auto_hold_h,
                # v3 extras
                "risk_engine_score": token_info.risk_engine_score,
                "risk_engine_action": token_info.risk_engine_action,
                "dev_score": token_info.dev_score,
                "dev_label": token_info.dev_label,
                "backend_buy_available": token_info.backend_buy_available,
            })

            # ── Alert: Snipe opportunity to Discord/Telegram ──
            try:
                result = await self.alert_service.alert_snipe_opportunity(
                    token=new_token,
                    symbol=token_info.symbol,
                    risk=token_info.risk,
                    liquidity_usd=usd_liq,
                    risk_score=token_info.risk_engine_score,
                    risk_action=token_info.risk_engine_action,
                    auto_buy=self.settings.get("auto_buy", False),
                )
                logger.info(f"[ALERT] alert_snipe_opportunity → channels={result.channels_sent}")
            except Exception as e:
                logger.warning(f"[ALERT] alert_snipe_opportunity FAILED: {e}")

            # ── Professional v3: Backend auto-buy via TradeExecutor ──
            if (self.settings.get("enable_trade_executor", False)
                    and self.trade_executor
                    and self.settings.get("auto_buy", False)
                    and token_info.risk_engine_action in ("STRONG_BUY", "BUY")):
                try:
                    buy_amount = self.settings.get("buy_amount_native", 0.01)
                    slippage = self.settings.get("slippage", 15)

                    # v6: Use MEV-protected tx if enabled
                    use_mev = (
                        self.settings.get("enable_mev_protection", False)
                        and self.settings.get("mev_auto_protect", True)
                        and token_info._mev_ok
                        and token_info.mev_threat_level in ("high", "medium")
                    )

                    if use_mev and self.mev_protector:
                        try:
                            mev_analysis = MEVAnalysis(
                                token_address=new_token,
                                threat_level=token_info.mev_threat_level or "unknown",
                                frontrun_risk=token_info.mev_frontrun_risk,
                                sandwich_risk=token_info.mev_sandwich_risk,
                                recommended_strategy=token_info.mev_strategy_used or "gas_boost",
                            )
                            raw_tx = {
                                "to": self.settings.get("router_address", ""),
                                "value": int(buy_amount * 10**18),
                                "token_address": new_token,
                                "slippage": slippage,
                            }
                            protected = await self.mev_protector.protect_transaction(
                                raw_tx, mev_analysis,
                            )
                            if protected and protected.success:
                                tx_hash = protected.bundle_hash or "mev-protected"
                                await self._emit("backend_buy_executed", {
                                    "token": new_token,
                                    "symbol": token_info.symbol,
                                    "tx_hash": tx_hash,
                                    "amount_native": buy_amount,
                                    "gas_price_protected": protected.gas_price_protected,
                                    "mev_protected": True,
                                    "mev_strategy": protected.strategy_used,
                                })
                                logger.info(f"🛡️ MEV-protected BUY for {token_info.symbol}: tx={tx_hash} (strategy={protected.strategy_used})")
                                # Alert: MEV-protected trade
                                try:
                                    await self.alert_service.alert_trade_executed(
                                        token=new_token, symbol=token_info.symbol,
                                        action="buy", amount=buy_amount, tx_hash=tx_hash,
                                        extra={"mev_protected": True, "mev_strategy": protected.strategy_used,
                                               "risk_engine_score": token_info.risk_engine_score},
                                    )
                                except Exception:
                                    pass
                            else:
                                logger.warning(f"MEV protection failed for {token_info.symbol}, falling back to normal buy")
                                use_mev = False  # fall through to normal buy
                        except Exception as e:
                            logger.warning(f"MEV protection error: {e}, falling back to normal buy")
                            use_mev = False

                    if not use_mev:
                        trade_result = await self.trade_executor.execute_buy(
                            token_address=new_token,
                            amount_native=buy_amount,
                            slippage=slippage,
                        )
                        if trade_result and trade_result.success:
                            await self._emit("backend_buy_executed", {
                                "token": new_token,
                                "symbol": token_info.symbol,
                                "tx_hash": trade_result.tx_hash,
                                "amount_native": buy_amount,
                                "gas_used": trade_result.gas_used,
                                "execution_ms": trade_result.execution_time_ms,
                                "mev_protected": False,
                            })
                            logger.info(f"🎯 Backend BUY executed for {token_info.symbol}: tx={trade_result.tx_hash}")
                            # Alert: trade executed
                            try:
                                await self.alert_service.alert_trade_executed(
                                    token=new_token, symbol=token_info.symbol,
                                    action="buy", amount=buy_amount, tx_hash=trade_result.tx_hash,
                                    extra={"risk_engine_score": token_info.risk_engine_score},
                                )
                            except Exception:
                                pass
                        elif trade_result:
                            await self._emit("backend_buy_failed", {
                                "token": new_token,
                                "symbol": token_info.symbol,
                                "error": trade_result.error,
                            })
                            logger.warning(f"Backend BUY failed for {token_info.symbol}: {trade_result.error}")
                            # Alert: trade failed
                            try:
                                await self.alert_service.alert_trade_failed(
                                    token=new_token, symbol=token_info.symbol,
                                    action="buy", error=trade_result.error or "Unknown error",
                                )
                            except Exception:
                                pass

                    # v6: Record trade outcome for AI optimizer
                    if self.settings.get("enable_ai_optimizer", True) and self.strategy_optimizer:
                        try:
                            trade_outcome = TradeOutcome(
                                token_address=new_token,
                                params_name="sniper",
                                pnl_percent=0.0,
                                exit_reason="entry",
                                market_regime=token_info.ai_regime or "unknown",
                                gas_price_gwei=float(self.settings.get("gas_price_gwei", 5.0)),
                                liquidity_usd=float(token_info.liquidity_usd),
                                timestamp=time.time(),
                            )
                            self.strategy_optimizer.record_trade(trade_outcome)
                        except Exception:
                            pass

                except Exception as e:
                    logger.error(f"Backend trade executor error for {token_info.symbol}: {e}")

        # ── v4: Record metrics for this detection ──
        try:
            _pipeline_ms = (time.time() - _pipeline_start) * 1000
            self.resource_monitor.record_token_processed(_pipeline_ms)
            detection = DetectionEvent(
                token_address=new_token,
                symbol=token_info.symbol if token_info else "?",
                analysis_ms=_pipeline_ms,
                result="passed" if passes_safety else "rejected",
                rejection_reason="" if passes_safety else "safety_check",
            )
            self.metrics_service.record_detection(detection)
        except Exception as e:
            logger.debug(f"v4 metrics recording failed: {e}")

        # ── Discord/Telegram alerts for every analyzed token ──
        if token_info:
            try:
                # Collect rejection reasons
                rejection_reasons = self._collect_rejection_reasons(token_info, passes_safety)
                _sym = token_info.symbol or "?"
                _risk = getattr(token_info, "risk", "unknown")
                _name = getattr(token_info, "name", _sym)

                logger.info(f"[ALERT] Sending unified alert for {_sym} ({new_token[:16]}…) "
                            f"risk={_risk} passes={passes_safety} "
                            f"discord_enabled={self.alert_service.config.get('discord_enabled')} "
                            f"webhook={'SET' if self.alert_service.config.get('discord_webhook_url') else 'EMPTY'}")

                # ONE comprehensive alert per token (avoids rate-limit issues)
                try:
                    result = await self.alert_service.alert_token_analysis(
                        token=new_token,
                        symbol=_sym,
                        name=_name,
                        risk=_risk,
                        liquidity_usd=usd_liq,
                        passes_safety=passes_safety,
                        rejection_reasons=rejection_reasons if not passes_safety else None,
                        token_info=token_info,
                    )
                    logger.info(f"[ALERT] alert_token_analysis → channels={result.channels_sent} "
                                f"success={result.success}")
                except Exception as e:
                    logger.warning(f"[ALERT] alert_token_analysis FAILED: {e}", exc_info=True)

            except Exception as e:
                logger.warning(f"[ALERT] Alert dispatch failed for {new_token[:16]}: {e}")

    # ═══════════════════════════════════════════════════════════
    #  Real-time Sync event listener via WebSocket
    # ═══════════════════════════════════════════════════════════

    async def _run_sync_listener(self):
        """
        WebSocket listener for Sync(uint112,uint112) events on tracked pairs.
        Connects to BSC/ETH WSS endpoint and subscribes to log events.
        Provides real-time liquidity updates without polling.
        Auto-reconnects on disconnect.
        """
        ws_list = WS_ENDPOINTS.get(self.chain_id, WS_ENDPOINTS[56])
        ws_idx = 0

        while self.running:
            ws_url = ws_list[ws_idx % len(ws_list)]
            try:
                session = aiohttp.ClientSession()
                ws = await session.ws_connect(
                    ws_url,
                    heartbeat=20,
                    timeout=aiohttp.ClientWSTimeout(ws_close=10),
                )
                self._sync_ws = ws
                self._sync_sub_id = None
                logger.info(f"Sync WS connected: {ws_url}")
                await self._emit("scan_info", {"message": f"🔌 Sync WS connected: {ws_url}"})

                # Initial subscription
                await self._subscribe_pairs_sync(ws)

                async for msg in ws:
                    if not self.running:
                        break
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        try:
                            data = json.loads(msg.data)
                            # Subscription confirmation
                            if "result" in data and data.get("id") == 1:
                                self._sync_sub_id = data["result"]
                                logger.info(f"Sync subscription active: {self._sync_sub_id}")
                            # Subscription event
                            elif data.get("method") == "eth_subscription":
                                log_data = data.get("params", {}).get("result")
                                if log_data:
                                    await self._handle_sync_log(log_data)
                        except Exception as e:
                            logger.debug(f"Sync WS message error: {e}")

                    elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        break

                    # Check if we need to resubscribe (new pairs added)
                    if self._sync_needs_resub:
                        self._sync_needs_resub = False
                        await self._subscribe_pairs_sync(ws)

            except Exception as e:
                logger.warning(f"Sync WS error ({ws_url}): {e}")
                await self._emit("scan_info", {"message": f"⚠️ Sync WS reconnecting…"})
            finally:
                self._sync_ws = None
                self._sync_sub_id = None
                try:
                    await session.close()
                except Exception:
                    pass

            ws_idx += 1  # rotate to next endpoint
            if self.running:
                await asyncio.sleep(2)

    async def _subscribe_pairs_sync(self, ws):
        """Subscribe (or resubscribe) to Sync events for tracked pairs."""
        # Collect pair addresses from the most recent detected pairs
        addresses = []
        for p in self.detected_pairs[-50:]:
            addr = Web3.to_checksum_address(p.pair_address)
            if addr not in addresses:
                addresses.append(addr)

        if not addresses:
            return

        # Unsubscribe old subscription first
        if self._sync_sub_id:
            try:
                await ws.send_str(json.dumps({
                    "jsonrpc": "2.0", "id": 2,
                    "method": "eth_unsubscribe",
                    "params": [self._sync_sub_id],
                }))
                self._sync_sub_id = None
            except Exception:
                pass

        # Subscribe to Sync events on all tracked pair addresses
        await ws.send_str(json.dumps({
            "jsonrpc": "2.0", "id": 1,
            "method": "eth_subscribe",
            "params": ["logs", {
                "address": addresses,
                "topics": [SYNC_TOPIC],
            }],
        }))

        self._sync_tracked = {a.lower() for a in addresses}
        logger.info(f"Subscribed to Sync events for {len(addresses)} pairs")
        await self._emit("scan_info", {
            "message": f"📡 Sync WS tracking {len(addresses)} pairs in real-time",
        })

    async def _handle_sync_log(self, log: dict):
        """
        Process a real-time Sync event from WebSocket.
        Decodes reserve0 & reserve1 directly from log data — zero extra RPC calls.
        """
        try:
            pair_address_raw = log.get("address", "").lower()

            # Find matching detected pair
            pair = None
            for p in self.detected_pairs:
                if p.pair_address.lower() == pair_address_raw:
                    pair = p
                    break
            if not pair:
                return

            # Decode reserves from log data
            # Sync event data: abi.encode(uint112 reserve0, uint112 reserve1)
            data_hex = log.get("data", "0x")
            if len(data_hex) < 130:  # 0x + 64 + 64
                return

            reserve0 = int(data_hex[2:66], 16)
            reserve1 = int(data_hex[66:130], 16)

            # Determine which reserve is native (WBNB/WETH)
            weth_lower = self.weth.lower()
            if pair.token0.lower() == weth_lower:
                native_reserve = reserve0 / 1e18
            else:
                native_reserve = reserve1 / 1e18

            usd_liq = native_reserve * self.native_price_usd * 2
            native_liq = native_reserve

            # DexScreener fallback
            info = pair.token_info
            if usd_liq <= 0 and info and info.dexscreener_liquidity > 0:
                usd_liq = info.dexscreener_liquidity

            old_liq = pair.liquidity_usd
            pair.liquidity_native = native_liq
            pair.liquidity_usd = usd_liq

            has_liquidity = usd_liq >= self.settings["min_liquidity_usd"]

            if info:
                # Emit instant update to frontend
                event_data = self._build_token_event_data(
                    pair.new_token, pair.pair_address, info,
                    native_liq, usd_liq, has_liquidity,
                    pair.block_number,
                )
                await self._emit("token_updated", event_data)

                # Snipe opportunity: liquidity just appeared
                if has_liquidity and old_liq < self.settings["min_liquidity_usd"]:
                    passes = True
                    if self.settings["only_safe"] and info.risk == "danger":
                        passes = False
                    if info.buy_tax > self.settings["max_buy_tax"]:
                        passes = False
                    if info.sell_tax > self.settings["max_sell_tax"]:
                        passes = False
                    if info.is_honeypot:
                        passes = False
                    if not info.lp_locked:
                        passes = False
                    if info._dexscreener_ok:
                        if info.dexscreener_price_change_h24 <= -50:
                            passes = False
                        if info.dexscreener_price_change_h6 <= -40:
                            passes = False
                        if info.dexscreener_price_change_h1 <= -25:
                            passes = False
                    if passes:
                        auto_hold_h = 0
                        if info.lp_lock_hours_remaining > 1:
                            auto_hold_h = round(info.lp_lock_hours_remaining - 1, 1)
                        await self._emit("snipe_opportunity", {
                            "token": pair.new_token,
                            "symbol": info.symbol,
                            "name": info.name,
                            "risk": info.risk,
                            "liquidity_usd": round(usd_liq, 2),
                            "pair": pair.pair_address,
                            "auto_buy": self.settings["auto_buy"],
                            "lp_locked": info.lp_locked,
                            "lp_lock_hours": info.lp_lock_hours_remaining,
                            "lp_lock_percent": info.lp_lock_percent,
                            "auto_hold_hours": auto_hold_h,
                        })

        except Exception as e:
            logger.debug(f"Sync event handling error: {e}")

    def _notify_sync_resub(self):
        """Flag that new pairs were added and WS needs to resubscribe."""
        self._sync_needs_resub = True

    # ─── Main polling loop ──────────────────────────────────
    async def run(self):
        """Main loop — polls for PairCreated events."""
        self.running = True

        # Create shared HTTP session for all API calls
        self._http_session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=15),
            connector=aiohttp.TCPConnector(limit=20, ttl_dns_cache=300),
        )
        self.analyzer._session = self._http_session

        # Refresh semaphore from settings
        self._sem = asyncio.Semaphore(int(self.settings.get("max_concurrent", 5)))

        await self._emit("bot_status", {"status": "starting", "chain_id": self.chain_id})

        # Show RPC connection info
        rpc_info = RPC_ENDPOINTS.get(self.chain_id, RPC_ENDPOINTS[56])
        factory_addr = FACTORY_ADDRESSES.get(self.chain_id, "")
        await self._emit("scan_info", {
            "chain_name": rpc_info["name"],
            "chain_id": self.chain_id,
            "rpc_http": rpc_info["http"],
            "factory": factory_addr,
            "factory_label": "PancakeSwap V2" if self.chain_id == 56 else "Uniswap V2",
            "weth": self.weth,
            "weth_label": "WBNB" if self.chain_id == 56 else "WETH",
            "router": self.router_addr,
            "event_topic": "PairCreated(address,address,address,uint256)",
        })

        # Fetch initial native price
        await self._fetch_native_price()
        native_sym = "BNB" if self.chain_id == 56 else "ETH"
        await self._emit("scan_info", {
            "message": f"{native_sym} price: ${self.native_price_usd:,.2f}",
        })

        await self._emit("bot_status", {
            "status": "running",
            "chain_id": self.chain_id,
            "native_price": self.native_price_usd,
        })

        last_block = self.w3.eth.block_number
        await self._emit("scan_info", {
            "message": f"Starting from block #{last_block:,}",
        })

        # Launch real-time Sync WebSocket listener in background
        self._sync_task = asyncio.ensure_future(self._run_sync_listener())
        await self._emit("scan_info", {"message": "🚀 Real-time Sync WS listener launched"})

        # ── Professional v2: launch background monitoring tasks ──
        if self.settings.get("enable_mempool", False) and self.mempool_listener:
            async def _mempool_event_cb(event):
                # Skip unknown methods — they're noise for the UI
                if event.method.startswith("unknown_"):
                    return
                await self._emit("mempool_event", {
                    "method": event.method,
                    "token": event.token_address,
                    "tx_hash": event.tx_hash,
                    "value_bnb": round(event.value_native, 4),
                    "from": event.from_address[:10] + "..." if event.from_address else "",
                    "gas_gwei": round(event.gas_price_gwei, 1),
                    "total_detected": self.mempool_listener.events_detected if self.mempool_listener else 0,
                })
            self.mempool_listener.set_callbacks(_mempool_event_cb, self._emit)
            self._mempool_task = asyncio.ensure_future(self.mempool_listener.run())
            await self._emit("scan_info", {"message": "📡 Mempool listener launched"})

        if self.settings.get("enable_rug_detector", True) and self.rug_detector:
            async def _rug_alert_cb(alert):
                # Find symbol from watched positions
                token_key = alert.token_address.lower()
                sym = ""
                for pos in self.rug_detector._watched.values():
                    if pos.token_address.lower() == token_key:
                        sym = pos.symbol
                        break
                level = alert.alert_type  # EMERGENCY / WARNING / INFO
                action = "sell_immediately" if alert.should_sell else "monitor"
                await self._emit("rug_alert", {
                    "token": alert.token_address,
                    "symbol": sym,
                    "alert_type": alert.alert_type,
                    "rug_type": alert.rug_type,
                    "severity": alert.severity,
                    "message": alert.description,
                    "level": level,
                    "action": action,
                    "tx_hash": alert.tx_hash,
                })
                # ── Send rug alert to Discord/Telegram ──
                try:
                    await self.alert_service.alert_rug_detected(
                        token=alert.token_address,
                        symbol=sym or "?",
                        message=f"[{alert.rug_type}] {alert.description}\nAction: {action}",
                        severity=alert.severity,
                    )
                except Exception:
                    pass
            self.rug_detector.set_callbacks(_rug_alert_cb, self._emit)
            self._rug_task = asyncio.ensure_future(self.rug_detector.run())
            await self._emit("scan_info", {"message": "🛡️ Rug detector launched"})

        if self.settings.get("enable_prelaunch", False) and self.prelaunch_detector:
            async def _prelaunch_cb(token):
                await self._emit("prelaunch_detected", {
                    "address": token.address,
                    "name": token.name,
                    "symbol": token.symbol,
                    "launch_probability": token.launch_probability,
                    "total_supply": str(getattr(token, 'total_supply', 0)),
                    "router_approved": token.approved_router,
                })
            self.prelaunch_detector.set_callbacks(_prelaunch_cb, self._emit)
            self._prelaunch_task = asyncio.ensure_future(self.prelaunch_detector.run())
            await self._emit("scan_info", {"message": "🔍 Pre-launch detector launched"})

        # ── Professional v3: module startup info ──
        v3_modules = []
        if self.settings.get("enable_dev_tracker", True):
            tracked = len(self.dev_tracker._profiles) if hasattr(self.dev_tracker, '_profiles') else 0
            v3_modules.append(f"DevTracker({tracked} devs)")
        if self.settings.get("enable_risk_engine", True):
            min_s = self.settings.get("risk_engine_min_score", 50)
            v3_modules.append(f"RiskEngine(min={min_s})")
        if self.settings.get("enable_trade_executor", False) and self.trade_executor:
            v3_modules.append("TradeExecutor(ACTIVE)")
        if self.settings.get("enable_pump_score", True):
            v3_modules.append("PumpAnalyzer v3(10 components)")
        if v3_modules:
            await self._emit("scan_info", {"message": f"⚡ v3 modules: {', '.join(v3_modules)}"})

        # ── Professional v5: module startup ──
        v5_modules = []
        if self.settings.get("enable_proxy_detector", True):
            v5_modules.append("ProxyDetector")
        if self.settings.get("enable_stress_test", True) and self.stress_tester:
            v5_modules.append("StressTester")
        if self.settings.get("enable_volatility_slippage", True):
            v5_modules.append("VolatilitySlippage")
        if self.settings.get("enable_ml_predictor", True):
            v5_modules.append("ML-PumpDump")
        if self.settings.get("enable_social_sentiment", True):
            v5_modules.append("SocialSentiment")
        if self.settings.get("enable_anomaly_detector", True):
            v5_modules.append("AnomalyDetector")
        if self.settings.get("enable_whale_activity", True):
            v5_modules.append("WhaleActivity")
        if self.settings.get("enable_dynamic_scanner", True):
            self._dynamic_scanner_task = asyncio.ensure_future(self.dynamic_scanner.run())
            v5_modules.append("DynamicScanner(bg)")
        if v5_modules:
            await self._emit("scan_info", {"message": f"🧠 v5 modules: {', '.join(v5_modules)}"})

        # ── Professional v6: module startup ──
        v6_modules = []
        if self.settings.get("enable_mev_protection", False) and self.mev_protector:
            strategy = self.mev_protector.config.strategy_priority[0] if self.mev_protector.config.strategy_priority else "none"
            v6_modules.append(f"MEVProtector({strategy})")
        if self.settings.get("enable_multi_dex", True) and self.multi_dex_router:
            chain_dexes = len(self.multi_dex_router.get_dexes(self.chain_id))
            v6_modules.append(f"MultiDEX({chain_dexes} dexes)")
        if self.settings.get("enable_ai_optimizer", True) and self.strategy_optimizer:
            v6_modules.append("AI-Optimizer")
        if self.settings.get("enable_backtesting", False) and self.backtest_engine:
            v6_modules.append("BacktestEngine")
        if self.settings.get("enable_copy_trading", False) and self.copy_trader:
            self._copy_trader_task = asyncio.ensure_future(self.copy_trader.start())
            v6_modules.append("CopyTrader(bg)")
        if v6_modules:
            await self._emit("scan_info", {"message": f"🚀 v6 modules: {', '.join(v6_modules)}"})

        # ── Professional v7: module startup ──
        v7_modules = []
        if self.settings.get("enable_rl_learner", True):
            v7_modules.append("RL-Learner")
        if self.settings.get("enable_orderflow", True):
            v7_modules.append("Orderflow")
        if self.settings.get("enable_market_simulator", True):
            v7_modules.append("MarketSimulator")
        if self.settings.get("enable_auto_strategy", True):
            v7_modules.append("AutoStrategy")
        if self.settings.get("enable_launch_scanner", True):
            try:
                self._launch_scanner_task = asyncio.ensure_future(self.launch_scanner.start())
                v7_modules.append("LaunchScanner(bg)")
            except Exception as e:
                logger.debug(f"Launch scanner startup failed: {e}")
                v7_modules.append("LaunchScanner(err)")
        if self.settings.get("enable_mempool_v7", True):
            try:
                self._mempool_v7_task = asyncio.ensure_future(self.mempool_analyzer_v7.start())
                v7_modules.append("MempoolV7(bg)")
            except Exception as e:
                logger.debug(f"Mempool v7 startup failed: {e}")
                v7_modules.append("MempoolV7(err)")
        if self.settings.get("enable_whale_graph", True):
            v7_modules.append("WhaleGraph")
        if v7_modules:
            await self._emit("scan_info", {"message": f"🧬 v7 modules: {', '.join(v7_modules)}"})

        price_tick = 0
        enrich_tick = 0           # Fast enrichment cycle (every ~3s)
        slow_tick = 0              # Full refresh cycle (every ~15s)
        total_blocks_scanned = 0
        total_events_found = 0
        _backoff = 0.0             # current backoff delay (0 = no backoff)
        _consecutive_429 = 0      # consecutive 429 errors across all RPCs

        while self.running:
            try:
                # Apply backoff if we hit rate limits
                if _backoff > 0:
                    await asyncio.sleep(_backoff)

                # Protected block_number call with RPC rotation
                current_block = await self._safe_get_block_number()

                if current_block > last_block:
                    # Scan new blocks for PairCreated events
                    from_block = last_block + 1
                    max_range = int(self.settings.get("block_range", 5))
                    to_block = min(current_block, from_block + max_range - 1)
                    blocks_range = to_block - from_block + 1

                    await self._emit("scan_block", {
                        "from_block": from_block,
                        "to_block": to_block,
                        "blocks_count": blocks_range,
                        "current_block": current_block,
                        "behind": current_block - to_block,
                    })

                    # Try getLogs with RPC rotation on failure
                    logs = None
                    last_rpc_err = ""
                    _is_429_cycle = False
                    for _attempt in range(len(self._rpc_list)):
                        try:
                            logs = self.w3.eth.get_logs({
                                "fromBlock": from_block,
                                "toBlock": to_block,
                                "address": Web3.to_checksum_address(
                                    FACTORY_ADDRESSES.get(self.chain_id, "")
                                ),
                                "topics": [PAIR_CREATED_TOPIC],
                            })
                            # Success — decay backoff
                            if _backoff > 0:
                                _backoff = max(0, _backoff * RPC_BACKOFF_DECAY - 0.5)
                            _consecutive_429 = 0
                            break  # success
                        except Exception as e:
                            err_str = str(e)[:200]
                            last_rpc_err = err_str[:120]
                            is_rate_limit = "429" in err_str or "Too Many Requests" in err_str or "rate" in err_str.lower()
                            logger.debug(f"RPC {self._rpc_list[self._rpc_index]} failed: {err_str}")
                            # v4: record RPC error
                            self.resource_monitor.record_rpc_call(
                                (time.time() - _pipeline_start) * 1000 if '_pipeline_start' in dir() else 0,
                                error=True,
                            )
                            if is_rate_limit:
                                _is_429_cycle = True
                            # Rotate to next RPC
                            self._rpc_index = (self._rpc_index + 1) % len(self._rpc_list)
                            new_rpc = self._rpc_list[self._rpc_index]
                            self.w3 = Web3(Web3.HTTPProvider(new_rpc))
                            self.analyzer = ContractAnalyzer(self.w3, self.chain_id, self._http_session, api_manager=self.api_manager)
                            await self._emit("scan_info", {
                                "message": f"Switched RPC → {new_rpc}",
                            })
                            # Brief pause — longer if 429
                            await asyncio.sleep(1.5 if is_rate_limit else 0.5)

                    # Update backoff based on 429 cycle
                    if _is_429_cycle:
                        _consecutive_429 += 1
                        _backoff = min(RPC_BACKOFF_MAX, RPC_BACKOFF_BASE * (1.5 ** (_consecutive_429 - 1)))
                        await self._emit("scan_info", {
                            "message": f"⏳ Rate-limited — backing off {_backoff:.1f}s (cycle #{_consecutive_429})",
                        })

                    if logs is not None:
                        total_blocks_scanned += blocks_range
                        total_events_found += len(logs)

                        await self._emit("scan_result", {
                            "from_block": from_block,
                            "to_block": to_block,
                            "events_found": len(logs),
                            "total_scanned": total_blocks_scanned,
                            "total_events": total_events_found,
                        })

                        # Launch ALL pair analyses in parallel (semaphore limits concurrency)
                        tasks = []
                        for log in logs:
                            try:
                                token0 = "0x" + log["topics"][1].hex()[-40:]
                                token1 = "0x" + log["topics"][2].hex()[-40:]
                                pair_addr = "0x" + log["data"].hex()[26:66]
                                tasks.append(
                                    self._handle_new_pair(pair_addr, token0, token1, log["blockNumber"])
                                )
                            except Exception as e:
                                logger.debug(f"Log decode error: {e}")

                        if tasks:
                            await asyncio.gather(*tasks, return_exceptions=True)
                    else:
                        await self._emit("scan_error", {
                            "message": f"All RPCs failed for blocks {from_block}-{to_block}: {last_rpc_err}",
                        })
                        # v4: alert on total RPC failure
                        await self.alert_service.alert_rpc_error(
                            self._rpc_list[self._rpc_index],
                            last_rpc_err,
                        )

                    last_block = to_block
                else:
                    await self._emit("scan_idle", {
                        "block": current_block,
                        "message": "Waiting for new blocks...",
                    })

                # Update native price every ~30 seconds
                price_tick += 1
                if price_tick >= 10:
                    await self._fetch_native_price()
                    price_tick = 0

                # FAST retry: incomplete tokens every ~3s (2 ticks × 1.5s)
                enrich_tick += 1
                if enrich_tick >= 2:
                    enrich_tick = 0
                    slow_tick += 1
                    # Always do fast retry for tokens with failed APIs
                    await self._enrich_detected_tokens(fast_only=True)
                    # Update active snipes P&L every ~3s for real-time stop-loss
                    await self._update_active_snipes()
                    # SLOW full refresh every ~15s (5 fast cycles)
                    if slow_tick >= 5:
                        slow_tick = 0
                        await self._enrich_detected_tokens(fast_only=False)

                # Emit heartbeat
                await self._emit("heartbeat", {
                    "block": current_block,
                    "pairs_detected": len(self.detected_pairs),
                    "active_snipes": len([s for s in self.active_snipes if s.status == "active"]),
                    "native_price": round(self.native_price_usd, 2),
                    "total_blocks_scanned": total_blocks_scanned,
                    "total_events_found": total_events_found,
                    "sync_ws_active": self._sync_sub_id is not None,
                    "sync_pairs_tracked": len(self._sync_tracked),
                })

            except Exception as e:
                err_msg = str(e)[:200]
                is_rate_limit = "429" in err_msg or "Too Many Requests" in err_msg or "rate" in err_msg.lower()
                if is_rate_limit:
                    _consecutive_429 += 1
                    _backoff = min(RPC_BACKOFF_MAX, RPC_BACKOFF_BASE * (1.5 ** (_consecutive_429 - 1)))
                    logger.warning(f"Rate-limited (block_number or other) — backoff {_backoff:.1f}s")
                    # Rotate RPC so next attempt uses a different endpoint
                    self._rpc_index = (self._rpc_index + 1) % len(self._rpc_list)
                    new_rpc = self._rpc_list[self._rpc_index]
                    self.w3 = Web3(Web3.HTTPProvider(new_rpc))
                    self.analyzer = ContractAnalyzer(self.w3, self.chain_id, self._http_session, api_manager=self.api_manager)
                    await self._emit("scan_info", {
                        "message": f"⏳ 429 on block fetch — switched to {new_rpc}, backoff {_backoff:.1f}s",
                    })
                else:
                    logger.warning(f"Sniper loop error: {e}")
                    await self._emit("error", {"message": err_msg})

            await asyncio.sleep(float(self.settings.get("poll_interval", 1.5)))

        # Cancel Sync WS listener
        if self._sync_task and not self._sync_task.done():
            self._sync_task.cancel()
            try:
                await self._sync_task
            except (asyncio.CancelledError, Exception):
                pass
            self._sync_task = None

        # Cancel v2 background tasks
        for task_attr in ("_mempool_task", "_rug_task", "_prelaunch_task"):
            task = getattr(self, task_attr, None)
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
            setattr(self, task_attr, None)
        # Stop module loops
        if hasattr(self, 'mempool_listener'):
            self.mempool_listener.stop()
        if hasattr(self, 'rug_detector'):
            self.rug_detector.stop()
        if hasattr(self, 'prelaunch_detector'):
            self.prelaunch_detector.stop()

        # Cleanup shared session
        if self._http_session and not self._http_session.closed:
            await self._http_session.close()
            self._http_session = None

        await self._emit("bot_status", {"status": "stopped"})

    async def _update_active_snipes(self):
        """Update P&L for all active positions."""
        for snipe in self.active_snipes:
            if snipe.status not in ("active", "selling"):
                continue
            try:
                current_price = await self._get_token_price_usd(snipe.token_address)
                if current_price > 0 and snipe.buy_price_usd > 0:
                    snipe.current_price_usd = current_price
                    snipe.pnl_percent = ((current_price - snipe.buy_price_usd) / snipe.buy_price_usd) * 100

                    held_seconds = time.time() - snipe.timestamp if snipe.timestamp else 0
                    await self._emit("snipe_update", {
                        "token": snipe.token_address,
                        "symbol": snipe.symbol,
                        "pnl_percent": round(snipe.pnl_percent, 2),
                        "buy_price": snipe.buy_price_usd,
                        "current_price": current_price,
                        "status": snipe.status,
                        "timestamp": snipe.timestamp,
                        "max_hold_hours": snipe.max_hold_hours,
                        "held_seconds": round(held_seconds),
                    })

                    # Auto take-profit / stop-loss alerts (only fire ONCE, then mark as "selling")
                    if snipe.pnl_percent >= snipe.take_profit:
                        snipe.status = "selling"
                        await self._emit("take_profit_alert", {
                            "token": snipe.token_address,
                            "symbol": snipe.symbol,
                            "pnl_percent": round(snipe.pnl_percent, 2),
                            "target": snipe.take_profit,
                        })
                    elif snipe.pnl_percent <= -snipe.stop_loss:
                        snipe.status = "selling"
                        await self._emit("stop_loss_alert", {
                            "token": snipe.token_address,
                            "symbol": snipe.symbol,
                            "pnl_percent": round(snipe.pnl_percent, 2),
                            "target": -snipe.stop_loss,
                        })

                # ── Time-limit auto-sell ──
                if snipe.status == "active" and snipe.max_hold_hours > 0 and snipe.timestamp > 0:
                    held_h = (time.time() - snipe.timestamp) / 3600
                    if held_h >= snipe.max_hold_hours:
                        snipe.status = "selling"
                        await self._emit("time_limit_alert", {
                            "token": snipe.token_address,
                            "symbol": snipe.symbol,
                            "pnl_percent": round(snipe.pnl_percent, 2),
                            "held_hours": round(held_h, 2),
                            "max_hold_hours": snipe.max_hold_hours,
                        })
            except Exception:
                pass

    def stop(self):
        """Stop the bot."""
        self.running = False
        # v5: stop dynamic scanner
        if hasattr(self, 'dynamic_scanner'):
            self.dynamic_scanner.stop()
        if hasattr(self, '_dynamic_scanner_task') and self._dynamic_scanner_task:
            self._dynamic_scanner_task.cancel()
        # v6: stop copy trader
        if hasattr(self, 'copy_trader') and self.copy_trader:
            try:
                asyncio.ensure_future(self.copy_trader.stop())
            except RuntimeError:
                pass
        if hasattr(self, '_copy_trader_task') and self._copy_trader_task:
            self._copy_trader_task.cancel()
        # v7: stop launch scanner
        if hasattr(self, 'launch_scanner'):
            try:
                asyncio.ensure_future(self.launch_scanner.stop())
            except RuntimeError:
                pass
        if hasattr(self, '_launch_scanner_task') and self._launch_scanner_task:
            self._launch_scanner_task.cancel()
        # v7: stop mempool analyzer v7
        if hasattr(self, 'mempool_analyzer_v7'):
            try:
                asyncio.ensure_future(self.mempool_analyzer_v7.stop())
            except RuntimeError:
                pass
        if hasattr(self, '_mempool_v7_task') and self._mempool_v7_task:
            self._mempool_v7_task.cancel()

    def update_settings(self, new_settings: dict):
        """Update bot settings from frontend."""
        for k, v in new_settings.items():
            if k in self.settings:
                # Type casting
                if isinstance(self.settings[k], bool):
                    self.settings[k] = bool(v)
                elif isinstance(self.settings[k], float):
                    self.settings[k] = float(v)
                elif isinstance(self.settings[k], int):
                    self.settings[k] = int(v)
                else:
                    self.settings[k] = v
        # Refresh semaphore if concurrency changed
        new_max = int(self.settings.get("max_concurrent", 5))
        self._sem = asyncio.Semaphore(new_max)

    def get_state(self) -> dict:
        """Return full bot state for frontend sync."""
        state = {
            "running": self.running,
            "chain_id": self.chain_id,
            "settings": self.settings,
            "native_price_usd": round(self.native_price_usd, 2),
            "detected_pairs": [p.to_dict() for p in self.detected_pairs[-50:]],
            "active_snipes": [s.to_dict() for s in self.active_snipes],
            "recent_events": self.events_log[-30:],
        }
        # v2 module stats
        try:
            if hasattr(self, 'mempool_listener'):
                state["mempool_stats"] = self.mempool_listener.get_stats()
            if hasattr(self, 'rug_detector'):
                state["rug_detector_stats"] = self.rug_detector.get_stats()
            if hasattr(self, 'prelaunch_detector'):
                state["prelaunch_watchlist"] = self.prelaunch_detector.get_watchlist()
            if hasattr(self, 'smart_money'):
                state["smart_money_stats"] = self.smart_money.get_stats()
        except Exception:
            pass
        # v3 module stats
        try:
            if hasattr(self, 'dev_tracker'):
                state["dev_tracker_stats"] = self.dev_tracker.get_stats()
            if hasattr(self, 'risk_engine'):
                state["risk_engine_stats"] = self.risk_engine.get_stats()
            if hasattr(self, 'trade_executor') and self.trade_executor:
                state["trade_executor_stats"] = self.trade_executor.get_stats()
            if hasattr(self, 'pump_analyzer'):
                state["pump_analyzer_stats"] = self.pump_analyzer.get_stats()
        except Exception:
            pass
        # v4 module stats
        try:
            if hasattr(self, 'resource_monitor'):
                state["resource_stats"] = self.resource_monitor.get_stats()
            if hasattr(self, 'alert_service'):
                state["alert_stats"] = self.alert_service.get_stats()
            if hasattr(self, 'metrics_service'):
                state["metrics_stats"] = self.metrics_service.get_stats()
                state["metrics_dashboard"] = self.metrics_service.get_dashboard()
        except Exception:
            pass
        # v5 module stats
        try:
            if hasattr(self, 'dynamic_scanner'):
                state["dynamic_scanner_stats"] = {
                    "monitored": len(self.dynamic_scanner._contracts),
                    "running": self.dynamic_scanner.running,
                }
            if hasattr(self, 'ml_predictor'):
                state["ml_predictor_stats"] = {
                    "predictions": self.ml_predictor._total_predictions if hasattr(self.ml_predictor, '_total_predictions') else 0,
                    "online_samples": len(self.ml_predictor._outcome_buffer) if hasattr(self.ml_predictor, '_outcome_buffer') else 0,
                }
        except Exception:
            pass
        # v6 module stats
        try:
            if hasattr(self, 'mev_protector') and self.mev_protector:
                state["mev_stats"] = self.mev_protector.get_stats() if hasattr(self.mev_protector, 'get_stats') else {}
            if hasattr(self, 'multi_dex_router') and self.multi_dex_router:
                state["multi_dex_stats"] = {
                    "chains": len(self.multi_dex_router.get_active_chains()),
                    "current_chain_dexes": len(self.multi_dex_router.get_dexes(self.chain_id)),
                }
            if hasattr(self, 'strategy_optimizer') and self.strategy_optimizer:
                state["ai_optimizer_stats"] = self.strategy_optimizer.get_stats() if hasattr(self.strategy_optimizer, 'get_stats') else {}
            if hasattr(self, 'copy_trader') and self.copy_trader:
                state["copy_trader_stats"] = self.copy_trader.get_stats() if hasattr(self.copy_trader, 'get_stats') else {}
            if hasattr(self, 'backtest_engine') and self.backtest_engine:
                state["backtest_stats"] = {"available": True}
        except Exception:
            pass
        # v7 module stats
        try:
            if hasattr(self, 'rl_learner') and self.rl_learner:
                state["rl_learner_stats"] = self.rl_learner.get_stats()
            if hasattr(self, 'orderflow_analyzer') and self.orderflow_analyzer:
                state["orderflow_stats"] = self.orderflow_analyzer.get_stats()
            if hasattr(self, 'market_simulator') and self.market_simulator:
                state["market_simulator_stats"] = self.market_simulator.get_stats()
            if hasattr(self, 'auto_strategy') and self.auto_strategy:
                state["auto_strategy_stats"] = self.auto_strategy.get_stats()
            if hasattr(self, 'launch_scanner') and self.launch_scanner:
                state["launch_scanner_stats"] = self.launch_scanner.get_stats()
            if hasattr(self, 'mempool_analyzer_v7') and self.mempool_analyzer_v7:
                state["mempool_v7_stats"] = self.mempool_analyzer_v7.get_stats()
            if hasattr(self, 'whale_graph') and self.whale_graph:
                state["whale_graph_stats"] = self.whale_graph.get_stats()
        except Exception:
            pass
        return state

    def add_manual_snipe(self, token_address: str, symbol: str, buy_price: float, amount_native: float, tx_hash: str, auto_hold_hours: float = 0):
        """Register a position bought from the frontend (user executed the swap)."""
        # Use auto_hold_hours from LP lock detection if available; fallback to manual setting
        hold_hours = auto_hold_hours if auto_hold_hours > 0 else float(self.settings.get("max_hold_hours", 0))
        snipe = ActiveSnipe(
            token_address=token_address,
            symbol=symbol,
            chain_id=self.chain_id,
            buy_price_usd=buy_price,
            buy_amount_native=amount_native,
            buy_tx=tx_hash,
            current_price_usd=buy_price,
            take_profit=self.settings["take_profit"],
            stop_loss=self.settings["stop_loss"],
            timestamp=time.time(),
            max_hold_hours=hold_hours,
        )
        self.active_snipes.append(snipe)

        # Register with rug detector for post-buy monitoring
        if self.settings.get("enable_rug_detector", True) and hasattr(self, 'rug_detector'):
            try:
                # Find token info for pair address and creator
                pair_addr = ""
                creator = ""
                for p in reversed(self.detected_pairs):
                    if p.new_token.lower() == token_address.lower():
                        pair_addr = p.pair_address
                        if p.token_info:
                            creator = getattr(p.token_info, 'owner_address', '') or ''
                        break
                self.rug_detector.add_position(
                    token_address=token_address,
                    pair_address=pair_addr,
                    symbol=symbol,
                    creator_address=creator,
                )
            except Exception as e:
                logger.debug(f"Rug detector registration failed: {e}")

        return snipe.to_dict()

    def mark_snipe_sold(self, token_address: str, sell_tx: str = "", sell_price: float = 0) -> dict | None:
        """Mark an active position as sold (triggered from frontend after swap)."""
        for snipe in self.active_snipes:
            if snipe.token_address.lower() == token_address.lower() and snipe.status == "active":
                snipe.status = "sold"
                snipe.sell_tx = sell_tx
                # v4: record trade metrics
                try:
                    self.metrics_service.record_trade_from_snipe(
                        snipe.to_dict(), sell_price, exit_reason="manual",
                    )
                except Exception:
                    pass
                # Remove from rug detector monitoring
                if hasattr(self, 'rug_detector'):
                    try:
                        self.rug_detector.remove_position(token_address)
                    except Exception:
                        pass
                return snipe.to_dict()
        return None

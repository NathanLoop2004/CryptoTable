"""
sniperService.py — Core sniper bot service.

Architecture (Professional v4):
  mempool listener → pre-launch detector → token detector → contract analyzer
  → swap simulator → pump analyzer → smart money tracker → dev tracker
  → risk engine → trade executor → rug detector → profit manager
  → resource monitor → alert service → metrics service

Modules:
  pumpAnalyzer.py      — Advanced pump score engine v3 (0–100, 10 components)
  swapSimulator.py     — On-chain honeypot detection via eth_call
  mempoolService.py    — Mempool pending tx listener
  rugDetector.py       — Post-buy rug pull monitoring
  preLaunchDetector.py — Pre-launch token detection
  smartMoneyTracker.py — Whale wallet tracking
  devTracker.py        — Developer reputation tracker (v3)
  riskEngine.py        — Unified risk scoring engine (v3)
  tradeExecutor.py     — Backend trade execution engine (v3)
  resourceMonitor.py   — Memory/CPU/WS/RPC monitoring (NEW v4)
  alertService.py      — Telegram/Discord/Email alerts (NEW v4)
  metricsService.py    — Performance metrics & P&L tracking (NEW v4)

Uses web3.py to interact with BSC (or other EVM chains) via WebSocket RPC.
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
        "https://bsc.drpc.org",
        "https://bsc-rpc.publicnode.com",
        "https://binance.llamarpc.com",
        "https://bsc-pokt.nodies.app",
        "https://bsc.meowrpc.com",
    ],
    1: [
        "https://eth.drpc.org",
        "https://ethereum-rpc.publicnode.com",
        "https://eth.llamarpc.com",
        "https://eth.meowrpc.com",
    ],
}

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

    def __init__(self, w3: Web3, chain_id: int, session: aiohttp.ClientSession | None = None):
        self.w3 = w3
        self.chain_id = chain_id
        self._session = session   # shared session for connection pooling

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

            # Run API checks in parallel (honeypot.is + GoPlus + DexScreener + CoinGecko + TokenSniffer)
            await asyncio.gather(
                self._check_honeypot_api(info),
                self._check_goplus_api(info),
                self._check_dexscreener_api(info),
                self._check_coingecko_api(info),
                self._check_tokensniffer_api(info),
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

    async def _check_honeypot_api(self, info: TokenInfo):
        """Use honeypot.is API to check for scams."""
        try:
            url = f"https://api.honeypot.is/v2/IsHoneypot?address={info.address}&chainID={self.chain_id}"
            own_session = self._session is None or self._session.closed
            session = await self._get_session() if not own_session else aiohttp.ClientSession()
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        hp = data.get("honeypotResult", {})
                        info.is_honeypot = hp.get("isHoneypot", False)

                        sim = data.get("simulationResult", {})
                        info.buy_tax = sim.get("buyTax", 0)
                        info.sell_tax = sim.get("sellTax", 0)

                        info._honeypot_ok = True   # API returned valid data

                        if info.is_honeypot:
                            info.risk_reasons.append("🍯 HONEYPOT — no puedes vender")
                        if info.buy_tax > 10:
                            info.risk_reasons.append(f"💸 Buy tax alto: {info.buy_tax}%")
                        if info.sell_tax > 10:
                            info.risk_reasons.append(f"💸 Sell tax alto: {info.sell_tax}%")
            finally:
                if own_session:
                    await session.close()
        except Exception as e:
            logger.debug(f"Honeypot API failed: {e}")

    async def _check_goplus_api(self, info: TokenInfo):
        """Use GoPlus Security API for deep contract analysis (free, no key)."""
        try:
            chain_id_str = str(self.chain_id)
            addr = info.address.lower()
            url = f"https://api.gopluslabs.com/api/v1/token_security/{chain_id_str}?contract_addresses={addr}"
            own_session = self._session is None or self._session.closed
            session = await self._get_session() if not own_session else aiohttp.ClientSession()
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    if resp.status != 200:
                        return
                    data = await resp.json()
            finally:
                if own_session:
                    await session.close()

            result = data.get("result", {}).get(addr, {})
            if not result:
                return

            info._goplus_ok = True   # GoPlus returned valid data

            # Helper: GoPlus returns "1"/"0" strings
            def flag(key):
                return str(result.get(key, "0")) == "1"

            # ── Map GoPlus flags to TokenInfo ──
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

            # If honeypot.is didn't catch it, GoPlus might
            if flag("is_honeypot") and not info.is_honeypot:
                info.is_honeypot = True
                info.risk_reasons.append("🍯 HONEYPOT (GoPlus)")

            # Buy/sell tax from GoPlus (if honeypot.is didn't get them)
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

            # Holder / LP info
            try:
                info.holder_count = int(result.get("holder_count", 0))
            except (ValueError, TypeError):
                pass
            try:
                info.lp_holder_count = int(result.get("lp_holder_count", 0))
            except (ValueError, TypeError):
                pass

            info.creator_address = result.get("creator_address", "")

            # ── Top holder concentration ──
            holders = result.get("holders", [])
            if holders:
                # Find the largest non-LP, non-dead, non-lock holder
                dead_addrs = {"0x0000000000000000000000000000000000000000",
                              "0x000000000000000000000000000000000000dead",
                              "0x0000000000000000000000000000000000000001"}
                lp_addrs = {lp.get("address", "").lower() for lp in result.get("lp_holders", [])}
                for h in holders:
                    h_addr = h.get("address", "").lower()
                    if h_addr in dead_addrs or h_addr in lp_addrs:
                        continue
                    if str(h.get("is_locked", "0")) == "1":
                        continue
                    try:
                        pct = float(h.get("percent", 0)) * 100
                        if pct > info.top_holder_percent:
                            info.top_holder_percent = round(pct, 2)
                    except (ValueError, TypeError):
                        pass

            # Creator balance check
            if info.creator_address:
                creator_lower = info.creator_address.lower()
                for h in holders:
                    if h.get("address", "").lower() == creator_lower:
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
                    # Common lock contracts
                    lp_addr = lp.get("address", "").lower()
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
                    info.risk_reasons.append(f"🔒 LP Locked {info.lp_lock_percent}% — {info.lp_lock_hours_remaining}h restantes")
                    if lock_source:
                        info.risk_reasons.append(f"🔐 Lock via: {lock_source}")
                else:
                    info.risk_reasons.append(f"🔒 LP Locked {info.lp_lock_percent}% (sin fecha de expiración)")
            else:
                info.risk_reasons.append("⚠️ Liquidez NO bloqueada — riesgo de rug pull")

            # ── Build risk_reasons from flags ──
            if not info.is_open_source:
                info.risk_reasons.append("🔒 Código no verificado (no open source)")
            if info.is_proxy:
                info.risk_reasons.append("🔄 Contrato proxy — puede cambiar la lógica")
            if info.is_mintable:
                info.risk_reasons.append("🖨️ Owner puede crear más tokens (mint)")
            if info.has_blacklist:
                info.risk_reasons.append("🚫 Puede bloquear wallets (blacklist)")
            if info.can_pause_trading:
                info.risk_reasons.append("⏸️ Puede pausar el trading")
            if info.has_hidden_owner:
                info.risk_reasons.append("👤 Owner oculto")
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

        except Exception as e:
            logger.debug(f"GoPlus API failed for {info.address}: {e}")

    # ─── DexScreener API ───────────────────────────────────
    async def _check_dexscreener_api(self, info: TokenInfo):
        """Use DexScreener API to check trading activity and social presence (free, no key)."""
        try:
            url = f"https://api.dexscreener.com/latest/dex/tokens/{info.address}"
            own_session = self._session is None or self._session.closed
            session = await self._get_session() if not own_session else aiohttp.ClientSession()
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    if resp.status != 200:
                        return
                    data = await resp.json()
            finally:
                if own_session:
                    await session.close()

            pairs = data.get("pairs") or []
            if not pairs:
                return

            info._dexscreener_ok = True
            info.dexscreener_pairs = len(pairs)

            # Use the highest-liquidity pair as reference
            best = max(pairs, key=lambda p: float(p.get("liquidity", {}).get("usd", 0) or 0))

            info.dexscreener_liquidity = float(best.get("liquidity", {}).get("usd", 0) or 0)

            vol = best.get("volume", {})
            info.dexscreener_volume_24h = float(vol.get("h24", 0) or 0)

            txns = best.get("txns", {}).get("h24", {})
            info.dexscreener_buys_24h = int(txns.get("buys", 0) or 0)
            info.dexscreener_sells_24h = int(txns.get("sells", 0) or 0)

            # Pair age
            created = best.get("pairCreatedAt")
            if created:
                age_ms = time.time() * 1000 - float(created)
                info.dexscreener_age_hours = round(max(0, age_ms / 3_600_000), 1)

            # Social / website (from token info block)
            token_info_block = best.get("info", {})
            socials = token_info_block.get("socials", [])
            websites = token_info_block.get("websites", [])
            info.has_social_links = len(socials) > 0
            info.has_website = len(websites) > 0

            # ── Price change history ──
            pc = best.get("priceChange", {})
            try:
                info.dexscreener_price_change_m5 = float(pc.get("m5", 0) or 0)
            except (ValueError, TypeError):
                pass
            try:
                info.dexscreener_price_change_h1 = float(pc.get("h1", 0) or 0)
            except (ValueError, TypeError):
                pass
            try:
                info.dexscreener_price_change_h6 = float(pc.get("h6", 0) or 0)
            except (ValueError, TypeError):
                pass
            try:
                info.dexscreener_price_change_h24 = float(pc.get("h24", 0) or 0)
            except (ValueError, TypeError):
                pass

            # (Risk reasons from DexScreener data are built in _rebuild_flag_reasons)

            logger.info(f"DexScreener for {info.symbol}: {info.dexscreener_pairs} pairs, ${info.dexscreener_volume_24h:.0f} vol")

        except Exception as e:
            logger.debug(f"DexScreener API failed for {info.address}: {e}")

    # ─── CoinGecko API ────────────────────────────────────
    async def _check_coingecko_api(self, info: TokenInfo):
        """Check if token is listed on CoinGecko (free, no key). Listing = legitimacy signal."""
        try:
            platform = "binance-smart-chain" if self.chain_id == 56 else "ethereum"
            url = f"https://api.coingecko.com/api/v3/coins/{platform}/contract/{info.address.lower()}"
            own_session = self._session is None or self._session.closed
            session = await self._get_session() if not own_session else aiohttp.ClientSession()
            try:
                headers = {"accept": "application/json"}
                async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=3)) as resp:
                    if resp.status == 404:
                        # Not listed — valid response, record it
                        info._coingecko_ok = True
                        info.listed_coingecko = False
                        return
                    if resp.status != 200:
                        return
                    data = await resp.json()
            finally:
                if own_session:
                    await session.close()

            info._coingecko_ok = True
            info.listed_coingecko = True
            info.coingecko_id = data.get("id", "")

            logger.info(f"CoinGecko: {info.symbol} IS listed (id={info.coingecko_id})")

        except Exception as e:
            logger.debug(f"CoinGecko API failed for {info.address}: {e}")

    # ─── TokenSniffer API ──────────────────────────────────
    async def _check_tokensniffer_api(self, info: TokenInfo):
        """Check TokenSniffer for scam score (free, no key for basic queries)."""
        try:
            chain_slug = "bsc" if self.chain_id == 56 else "eth"
            url = f"https://tokensniffer.com/api/v2/tokens/{self.chain_id}/{info.address.lower()}?include_metrics=true&include_tests=true&block_until_ready=false"
            own_session = self._session is None or self._session.closed
            session = await self._get_session() if not own_session else aiohttp.ClientSession()
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    if resp.status != 200:
                        return
                    data = await resp.json()
            finally:
                if own_session:
                    await session.close()

            score = data.get("score")
            if score is not None:
                info._tokensniffer_ok = True
                info.tokensniffer_score = int(score)
                info.tokensniffer_is_scam = data.get("is_flagged", False)

                if info.tokensniffer_is_scam:
                    info.risk_reasons.append(f"🚩 TokenSniffer: FLAGGED como scam (score {info.tokensniffer_score}/100)")
                elif info.tokensniffer_score < 30:
                    info.risk_reasons.append(f"🚩 TokenSniffer score muy bajo: {info.tokensniffer_score}/100")
                elif info.tokensniffer_score < 50:
                    info.risk_reasons.append(f"⚠️ TokenSniffer score bajo: {info.tokensniffer_score}/100")

                logger.info(f"TokenSniffer for {info.symbol}: score={info.tokensniffer_score}, flagged={info.tokensniffer_is_scam}")

        except Exception as e:
            logger.debug(f"TokenSniffer API failed for {info.address}: {e}")

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
        self.analyzer = ContractAnalyzer(self.w3, chain_id)

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
        """Get liquidity in native token and USD (async-friendly)."""
        loop = asyncio.get_event_loop()
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

            usd_value = native_reserve * self.native_price_usd * 2  # both sides
            return native_reserve, usd_value
        except Exception as e:
            logger.debug(f"Liquidity check failed: {e}")
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
        }

    # ─── Refresh detected tokens (periodic re-check) ───────
    async def _enrich_detected_tokens(self, fast_only: bool = False):
        """Re-check liquidity and retry ALL failed APIs for recently detected tokens.
        Emits 'token_updated' for every token so the frontend stays current.

        fast_only=True  → only process tokens with incomplete API data (quick retry)
        fast_only=False → process ALL tokens (full refresh including DexScreener prices)
        """
        all_pairs = self.detected_pairs[-50:]
        if not all_pairs:
            return

        if fast_only:
            # Only tokens that still have failed APIs
            candidates = [
                p for p in all_pairs
                if p.token_info and not all([
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
            candidates = all_pairs

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

                    # Retry failed APIs + always refresh DexScreener for live data
                    retry_tasks = []
                    if not info._goplus_ok:
                        retry_tasks.append(self.analyzer._check_goplus_api(info))
                    if not info._honeypot_ok:
                        retry_tasks.append(self.analyzer._check_honeypot_api(info))
                    if not info._coingecko_ok:
                        retry_tasks.append(self.analyzer._check_coingecko_api(info))
                    if not info._tokensniffer_ok:
                        retry_tasks.append(self.analyzer._check_tokensniffer_api(info))

                    # Always re-check DexScreener (fresh price/volume/liquidity)
                    retry_tasks.append(self.analyzer._check_dexscreener_api(info))

                    if retry_tasks:
                        logger.debug(f"Enriching {info.symbol}: {len(retry_tasks)} API(s)")
                        await asyncio.gather(*retry_tasks, return_exceptions=True)

                    # Use DexScreener liquidity as fallback if on-chain returns 0
                    if usd_liq <= 0 and info and info.dexscreener_liquidity > 0:
                        usd_liq = info.dexscreener_liquidity

                    old_liq = pair.liquidity_usd
                    pair.liquidity_native = native_liq
                    pair.liquidity_usd = usd_liq

                    has_liquidity = usd_liq >= self.settings["min_liquidity_usd"]

                    if info:
                        # Rebuild reasons from stored flags + fresh DexScreener data
                        info.risk_reasons = self.analyzer._rebuild_flag_reasons(info)
                        self.analyzer._calculate_risk(info)

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

        if not has_liquidity:
            return

        # Emit detailed analysis only for liquid tokens
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

        # ── Professional v2: parallel deep analysis ──
        try:
            v2_tasks = []
            # Pump score analysis (no extra API calls — uses existing TokenInfo)
            if self.settings.get("enable_pump_score", True):
                v2_tasks.append(("pump", asyncio.ensure_future(
                    self.pump_analyzer.analyze(token_info, usd_liq, pair_address)
                )))
            # Swap simulation: on-chain honeypot verification
            if self.settings.get("enable_swap_sim", True) and self.swap_simulator:
                v2_tasks.append(("sim", asyncio.ensure_future(
                    self.swap_simulator.simulate(new_token)
                )))
            # Bytecode analysis: opcode-level malware detection
            if self.settings.get("enable_bytecode", True) and self.bytecode_analyzer:
                v2_tasks.append(("bytecode", asyncio.ensure_future(
                    self.bytecode_analyzer.analyze_bytecode(new_token)
                )))
            # Smart money: check if tracked whales already bought
            if self.settings.get("enable_smart_money", False) and self.smart_money:
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

                # ── Professional v3: populate pump v3 extra fields ──
                if self.settings.get("enable_pump_score", True) and token_info.pump_score > 0:
                    # Re-fetch the pump result to extract v3 fields
                    try:
                        pump_result = await self.pump_analyzer.analyze(token_info, usd_liq, pair_address)
                        if pump_result:
                            token_info.pump_mcap_score = pump_result.mcap_score
                            token_info.pump_market_cap_usd = pump_result.market_cap_usd
                            token_info.pump_holder_growth_rate = pump_result.holder_growth_rate
                            token_info.pump_lp_growth_percent = pump_result.lp_growth_percent
                            token_info.pump_holder_growth_score = pump_result.holder_growth_score
                            token_info.pump_lp_growth_score = pump_result.lp_growth_score
                    except Exception as e:
                        logger.debug(f"v3 pump extras failed for {token_info.symbol}: {e}")

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

                # Re-emit token_detected with updated v2+v3 data
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

            # ── Professional v3: Backend auto-buy via TradeExecutor ──
            if (self.settings.get("enable_trade_executor", False)
                    and self.trade_executor
                    and self.settings.get("auto_buy", False)
                    and token_info.risk_engine_action in ("STRONG_BUY", "BUY")):
                try:
                    buy_amount = self.settings.get("buy_amount_native", 0.01)
                    slippage = self.settings.get("slippage", 15)
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
                        })
                        logger.info(f"🎯 Backend BUY executed for {token_info.symbol}: tx={trade_result.tx_hash}")
                    elif trade_result:
                        await self._emit("backend_buy_failed", {
                            "token": new_token,
                            "symbol": token_info.symbol,
                            "error": trade_result.error,
                        })
                        logger.warning(f"Backend BUY failed for {token_info.symbol}: {trade_result.error}")
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

            # Alert on suspicious tokens
            if token_info and token_info.is_honeypot:
                await self.alert_service.alert_token_suspicious(
                    token_info.symbol, new_token,
                    "Honeypot detected", "warning",
                )
            if token_info and token_info._risk_engine_ok and token_info.risk_engine_hard_stop:
                await self.alert_service.alert_token_suspicious(
                    token_info.symbol, new_token,
                    f"Hard stop: {token_info.risk_engine_hard_stop_reason}", "error",
                )
        except Exception as e:
            logger.debug(f"v4 metrics recording failed: {e}")

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
                await self._emit("mempool_event", {
                    "type": event.method,
                    "token": event.token_address,
                    "tx_hash": event.tx_hash,
                    "method": event.method,
                    "value_bnb": event.value_native,
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

        price_tick = 0
        enrich_tick = 0           # Fast enrichment cycle (every ~3s)
        slow_tick = 0              # Full refresh cycle (every ~15s)
        total_blocks_scanned = 0
        total_events_found = 0

        while self.running:
            try:
                current_block = self.w3.eth.block_number

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
                            break  # success
                        except Exception as e:
                            last_rpc_err = str(e)[:120]
                            logger.debug(f"RPC {self._rpc_list[self._rpc_index]} failed: {e}")
                            # v4: record RPC error
                            self.resource_monitor.record_rpc_call(
                                (time.time() - _pipeline_start) * 1000 if '_pipeline_start' in dir() else 0,
                                success=False,
                            )
                            # Rotate to next RPC
                            self._rpc_index = (self._rpc_index + 1) % len(self._rpc_list)
                            new_rpc = self._rpc_list[self._rpc_index]
                            self.w3 = Web3(Web3.HTTPProvider(new_rpc))
                            self.analyzer = ContractAnalyzer(self.w3, self.chain_id, self._http_session)
                            await self._emit("scan_info", {
                                "message": f"Switched RPC → {new_rpc}",
                            })
                            await asyncio.sleep(0.5)

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
                logger.warning(f"Sniper loop error: {e}")
                await self._emit("error", {"message": str(e)[:200]})

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

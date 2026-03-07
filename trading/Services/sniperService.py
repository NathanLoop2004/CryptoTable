"""
sniperService.py — Core sniper bot service.

Architecture:
  mempool listener → token detector → contract analyzer
  → liquidity detector → sniper engine → trade executor → profit manager

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
    lp_holder_count: int = 0
    holder_count: int = 0
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
    stop_loss: float = 15        # % to auto-sell
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
                reasons.append("🔒 Código no verificado (no open source)")
            if info.is_proxy:
                reasons.append("🔄 Contrato proxy — puede cambiar la lógica")
            if info.is_mintable:
                reasons.append("🖨️ Owner puede crear más tokens (mint)")
            if info.has_blacklist:
                reasons.append("🚫 Puede bloquear wallets (blacklist)")
            if info.can_pause_trading:
                reasons.append("⏸️ Puede pausar el trading")
            if info.has_hidden_owner:
                reasons.append("👤 Owner oculto")
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
        ]
        if any(danger_flags):
            info.risk = "danger"
            if info.sell_tax > 30 or info.buy_tax > 30:
                info.risk_reasons.append("💀 Tax > 30%")
            return

        # ── WARNING: suspicious but not fatal ──
        warning_flags = [
            info.is_mintable,
            info.has_blacklist,
            info.can_pause_trading,
            info.is_proxy,
            info.has_hidden_owner,
            info.has_external_call,
            info.personal_slippage_modifiable,
            info.sell_tax > 10 or info.buy_tax > 10,
            info.has_owner,
            not info.is_open_source if info._goplus_ok else False,
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
    Main sniper bot engine.
    Listens for new pairs, analyzes contracts, and manages positions.
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
            "stop_loss": 15,             # %
            "auto_buy": False,           # auto-execute buys
            "only_safe": True,           # only buy "safe" risk tokens
            "slippage": 12,              # %
            "max_hold_hours": 0,         # 0 = disabled; sell after N hours
            # ── Concurrency / Performance ──
            "max_concurrent": 5,         # parallel pair analyses
            "block_range": 5,            # blocks per scan cycle
            "poll_interval": 1.5,        # seconds between scans
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

        # LP must be locked to buy
        if not token_info.lp_locked:
            passes_safety = False
            logger.info(f"Skipping {token_info.symbol}: LP not locked")

        # Price history check: reject tokens with severe dumps
        if token_info._dexscreener_ok:
            if token_info.dexscreener_price_change_h24 <= -50:
                passes_safety = False
                logger.info(f"Skipping {token_info.symbol}: 24h dump {token_info.dexscreener_price_change_h24}%")
            if token_info.dexscreener_price_change_h6 <= -40:
                passes_safety = False
                logger.info(f"Skipping {token_info.symbol}: 6h dump {token_info.dexscreener_price_change_h6}%")
            if token_info.dexscreener_price_change_h1 <= -25:
                passes_safety = False
                logger.info(f"Skipping {token_info.symbol}: 1h dump {token_info.dexscreener_price_change_h1}%")

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
            })

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

                    last_block = to_block
                else:
                    await self._emit("scan_idle", {
                        "block": current_block,
                        "message": "Waiting for new blocks...",
                    })

                # Update price every ~30 seconds
                price_tick += 1
                if price_tick >= 10:
                    await self._fetch_native_price()
                    price_tick = 0

                    # Update active snipes P&L
                    await self._update_active_snipes()

                # FAST retry: incomplete tokens every ~3s (2 ticks × 1.5s)
                enrich_tick += 1
                if enrich_tick >= 2:
                    enrich_tick = 0
                    slow_tick += 1
                    # Always do fast retry for tokens with failed APIs
                    await self._enrich_detected_tokens(fast_only=True)
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
        return {
            "running": self.running,
            "chain_id": self.chain_id,
            "settings": self.settings,
            "native_price_usd": round(self.native_price_usd, 2),
            "detected_pairs": [p.to_dict() for p in self.detected_pairs[-50:]],
            "active_snipes": [s.to_dict() for s in self.active_snipes],
            "recent_events": self.events_log[-30:],
        }

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
        return snipe.to_dict()

    def mark_snipe_sold(self, token_address: str, sell_tx: str = "") -> dict | None:
        """Mark an active position as sold (triggered from frontend after swap)."""
        for snipe in self.active_snipes:
            if snipe.token_address.lower() == token_address.lower() and snipe.status == "active":
                snipe.status = "sold"
                snipe.sell_tx = sell_tx
                return snipe.to_dict()
        return None

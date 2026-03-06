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

            # ── DexScreener-based risk signals ──
            if info.dexscreener_age_hours < 1:
                info.risk_reasons.append("🔜 Par creado hace menos de 1 hora")
            if info.dexscreener_volume_24h < 100 and info.dexscreener_pairs <= 1:
                info.risk_reasons.append("📊 Volumen 24h muy bajo (<$100)")
            total_txns = info.dexscreener_buys_24h + info.dexscreener_sells_24h
            if total_txns < 5:
                info.risk_reasons.append(f"👥 Muy pocas transacciones ({total_txns} en 24h)")

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
        await self._emit("token_detected", {
            "token": new_token,
            "pair": pair_address,
            "symbol": token_info.symbol,
            "name": token_info.name,
            "risk": token_info.risk,
            "buy_tax": token_info.buy_tax,
            "sell_tax": token_info.sell_tax,
            "is_honeypot": token_info.is_honeypot,
            "has_owner": token_info.has_owner,
            "risk_reasons": token_info.risk_reasons,
            "liquidity_usd": round(usd_liq, 2),
            "liquidity_native": round(native_liq, 4),
            "has_liquidity": has_liquidity,
            "block": block,
            # Security flags
            "is_mintable": token_info.is_mintable,
            "has_blacklist": token_info.has_blacklist,
            "can_pause_trading": token_info.can_pause_trading,
            "is_proxy": token_info.is_proxy,
            "has_hidden_owner": token_info.has_hidden_owner,
            "can_self_destruct": token_info.can_self_destruct,
            "cannot_sell_all": token_info.cannot_sell_all,
            "owner_can_change_balance": token_info.owner_can_change_balance,
            "is_open_source": token_info.is_open_source,
            "holder_count": token_info.holder_count,
            "total_supply": token_info.total_supply,
            # Cross-platform data
            "listed_coingecko": token_info.listed_coingecko,
            "coingecko_id": token_info.coingecko_id,
            "dexscreener_pairs": token_info.dexscreener_pairs,
            "dexscreener_volume_24h": token_info.dexscreener_volume_24h,
            "dexscreener_liquidity": token_info.dexscreener_liquidity,
            "dexscreener_buys_24h": token_info.dexscreener_buys_24h,
            "dexscreener_sells_24h": token_info.dexscreener_sells_24h,
            "dexscreener_age_hours": token_info.dexscreener_age_hours,
            "has_social_links": token_info.has_social_links,
            "has_website": token_info.has_website,
            "tokensniffer_score": token_info.tokensniffer_score,
            "tokensniffer_is_scam": token_info.tokensniffer_is_scam,
            # API status
            "goplus_ok": token_info._goplus_ok,
            "honeypot_ok": token_info._honeypot_ok,
            "dexscreener_ok": token_info._dexscreener_ok,
            "coingecko_ok": token_info._coingecko_ok,
            "tokensniffer_ok": token_info._tokensniffer_ok,
        })

        self.detected_pairs.append(new_pair)
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

        if passes_safety:
            await self._emit("snipe_opportunity", {
                "token": new_token,
                "symbol": token_info.symbol,
                "name": token_info.name,
                "risk": token_info.risk,
                "liquidity_usd": round(usd_liq, 2),
                "pair": pair_address,
                "auto_buy": self.settings["auto_buy"],
            })

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
        price_tick = 0
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

                # Emit heartbeat
                await self._emit("heartbeat", {
                    "block": current_block,
                    "pairs_detected": len(self.detected_pairs),
                    "active_snipes": len([s for s in self.active_snipes if s.status == "active"]),
                    "native_price": round(self.native_price_usd, 2),
                    "total_blocks_scanned": total_blocks_scanned,
                    "total_events_found": total_events_found,
                })

            except Exception as e:
                logger.warning(f"Sniper loop error: {e}")
                await self._emit("error", {"message": str(e)[:200]})

            await asyncio.sleep(float(self.settings.get("poll_interval", 1.5)))

        # Cleanup shared session
        if self._http_session and not self._http_session.closed:
            await self._http_session.close()
            self._http_session = None

        await self._emit("bot_status", {"status": "stopped"})

    async def _update_active_snipes(self):
        """Update P&L for all active positions."""
        for snipe in self.active_snipes:
            if snipe.status != "active":
                continue
            try:
                current_price = await self._get_token_price_usd(snipe.token_address)
                if current_price > 0 and snipe.buy_price_usd > 0:
                    snipe.current_price_usd = current_price
                    snipe.pnl_percent = ((current_price - snipe.buy_price_usd) / snipe.buy_price_usd) * 100

                    await self._emit("snipe_update", {
                        "token": snipe.token_address,
                        "symbol": snipe.symbol,
                        "pnl_percent": round(snipe.pnl_percent, 2),
                        "buy_price": snipe.buy_price_usd,
                        "current_price": current_price,
                        "status": snipe.status,
                    })

                    # Auto take-profit / stop-loss alerts
                    if snipe.pnl_percent >= snipe.take_profit:
                        await self._emit("take_profit_alert", {
                            "token": snipe.token_address,
                            "symbol": snipe.symbol,
                            "pnl_percent": round(snipe.pnl_percent, 2),
                            "target": snipe.take_profit,
                        })
                    elif snipe.pnl_percent <= -snipe.stop_loss:
                        await self._emit("stop_loss_alert", {
                            "token": snipe.token_address,
                            "symbol": snipe.symbol,
                            "pnl_percent": round(snipe.pnl_percent, 2),
                            "target": -snipe.stop_loss,
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

    def add_manual_snipe(self, token_address: str, symbol: str, buy_price: float, amount_native: float, tx_hash: str):
        """Register a position bought from the frontend (user executed the swap)."""
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
        )
        self.active_snipes.append(snipe)
        return snipe.to_dict()

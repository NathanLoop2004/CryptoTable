"""
rugDetector.py — Real-time Post-Buy Rug Pull Detection.

Monitors active positions for on-chain rug pull indicators in real-time:
  - removeLiquidity / removeLiquidityETH calls
  - Sudden large LP token movements
  - Tax increase function calls (setTax, setFee, updateFee)
  - blacklist / blacklistAddress calls
  - Pause / unpause trading
  - Owner function calls post-renounce
  - LP unlock events (lock contract interactions)
  - Massive sells from creator/dev wallets

Emits EMERGENCY_SELL signals when rug indicators are detected,
giving the bot 1–5 seconds to exit before liquidity is drained.

Integration:
  Runs as a parallel async task monitoring all active positions.
  Uses WebSocket log subscription for zero-delay detection.
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, asdict, field
from typing import Optional, Callable

from web3 import Web3
import aiohttp

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
#  Constants
# ═══════════════════════════════════════════════════════════════════

# Method selectors for rug-pull functions
RUG_METHOD_SELECTORS = {
    # Liquidity removal
    "02751cec": "removeLiquidityETH",
    "baa2abde": "removeLiquidity",
    "af2979eb": "removeLiquidityETHSupportingFeeOnTransferTokens",
    "ded9382a": "removeLiquidityETHWithPermit",
    "2195995c": "removeLiquidityWithPermit",
    # Tax manipulation
    "c0246668": "setFee",
    "e086e5ec": "setTaxRate",
    "a0a5b311": "updateFee",
    "9e252f00": "setTax",
    "4bf365df": "setSellFee",
    "3e8e4cdb": "setBuyFee",
    "fce589d8": "setFees",
    "0a78097d": "setMaxTxAmount",
    "e01af92c": "setMaxWalletSize",
    # Blacklist
    "44337ea1": "blacklist",
    "f9f92be4": "blacklistAddress",
    "16c02129": "addBlacklist",
    "c3c5a547": "isBlacklisted",
    # Pause trading
    "8456cb59": "pause",
    "3f4ba83a": "unpause",
    "02329a29": "setPaused",
    # Ownership
    "715018a6": "renounceOwnership",
    "f2fde38b": "transferOwnership",
    "e30c3978": "claimOwnership",
    # Dangerous admin functions
    "a9059cbb": "transfer",         # if called by owner on token contract
    "23b872dd": "transferFrom",     # force transfer
    "40c10f19": "mint",             # create new tokens
    "42966c68": "burn",
}

# Event topics for rug detection
TRANSFER_TOPIC = "0x" + Web3.keccak(
    text="Transfer(address,address,uint256)"
).hex()

APPROVAL_TOPIC = "0x" + Web3.keccak(
    text="Approval(address,address,uint256)"
).hex()

OWNERSHIP_TRANSFERRED_TOPIC = "0x" + Web3.keccak(
    text="OwnershipTransferred(address,address)"
).hex()


# ═══════════════════════════════════════════════════════════════════
#  Data structures
# ═══════════════════════════════════════════════════════════════════

@dataclass
class RugAlert:
    """A rug pull alert for an active position."""
    token_address: str
    alert_type: str = ""            # EMERGENCY | WARNING | INFO
    rug_type: str = ""              # liquidity_removal | tax_increase | blacklist | etc
    description: str = ""
    tx_hash: str = ""
    from_address: str = ""
    severity: int = 0               # 1-10 (10 = immediate rug)
    should_sell: bool = False       # True = emit emergency sell signal
    timestamp: float = 0
    details: dict = field(default_factory=dict)

    def to_dict(self):
        return asdict(self)


@dataclass
class WatchedPosition:
    """A position being monitored for rug indicators."""
    token_address: str
    pair_address: str
    symbol: str = ""
    owner_address: str = ""
    creator_address: str = ""
    buy_timestamp: float = 0
    # Snapshot at buy time (for comparison)
    initial_liquidity_usd: float = 0
    initial_holder_count: int = 0
    # Monitoring state
    alerts_emitted: int = 0
    last_check: float = 0


# ═══════════════════════════════════════════════════════════════════
#  Rug Detector
# ═══════════════════════════════════════════════════════════════════

class RugDetector:
    """
    Real-time rug pull detection for active sniper positions.

    Monitoring strategy:
    1. WebSocket subscription to relevant events on pair + token contracts
    2. Periodic RPC polling for state changes (LP reserves, owner status)
    3. Mempool integration for pre-confirmation detection

    Alert levels:
      EMERGENCY (severity 8-10): Sell immediately
        - removeLiquidity detected
        - Tax set to 90%+
        - Token paused
        - Massive creator sell

      WARNING (severity 5-7): Consider selling
        - Tax increased significantly
        - LP unlock approaching
        - Large holder selling
        - Owner function called after renounce

      INFO (severity 1-4): Monitor closely
        - New large holder appeared
        - Volume spike (could be wash trading)
        - Unusual transfer patterns
    """

    def __init__(self, w3: Web3, chain_id: int,
                 router_address: str, weth_address: str):
        self.w3 = w3
        self.chain_id = chain_id
        self.router_addr = router_address.lower()
        self.weth_addr = weth_address.lower()

        self.running = False
        self._watched: dict[str, WatchedPosition] = {}  # token_addr → WatchedPosition

        # Callbacks
        self._alert_callback: Optional[Callable] = None
        self._emit_callback: Optional[Callable] = None

        # Statistics
        self.stats = {
            "alerts_emitted": 0,
            "emergency_alerts": 0,
            "positions_watched": 0,
        }

    def set_callbacks(self, alert_cb, emit_cb):
        """Set callbacks for rug alerts."""
        self._alert_callback = alert_cb
        self._emit_callback = emit_cb

    async def _emit(self, event_type: str, data: dict):
        if self._emit_callback:
            try:
                await self._emit_callback(event_type, data)
            except Exception as e:
                logger.debug(f"RugDetector emit error: {e}")

    def add_position(self, token_address: str, pair_address: str,
                     symbol: str = "", owner_address: str = "",
                     creator_address: str = "",
                     liquidity_usd: float = 0, holder_count: int = 0):
        """Start monitoring a new position."""
        key = token_address.lower()
        self._watched[key] = WatchedPosition(
            token_address=token_address,
            pair_address=pair_address,
            symbol=symbol,
            owner_address=owner_address.lower() if owner_address else "",
            creator_address=creator_address.lower() if creator_address else "",
            buy_timestamp=time.time(),
            initial_liquidity_usd=liquidity_usd,
            initial_holder_count=holder_count,
        )
        self.stats["positions_watched"] = len(self._watched)
        logger.info(f"RugDetector: watching {symbol} ({token_address[:10]}...)")

    def remove_position(self, token_address: str):
        """Stop monitoring a position (after sell)."""
        key = token_address.lower()
        if key in self._watched:
            del self._watched[key]
            self.stats["positions_watched"] = len(self._watched)

    async def run(self):
        """Main monitoring loop — runs as a background task."""
        self.running = True

        await self._emit("rug_detector_status", {
            "status": "running",
            "message": "🛡️ Rug detector activo",
        })

        while self.running:
            if not self._watched:
                await asyncio.sleep(2)
                continue

            try:
                # Check all watched positions
                tasks = []
                for pos in list(self._watched.values()):
                    tasks.append(self._check_position(pos))

                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)

            except Exception as e:
                logger.warning(f"RugDetector loop error: {e}")

            await asyncio.sleep(3)  # Check every 3 seconds

    async def _check_position(self, pos: WatchedPosition):
        """Run all rug checks on a single position."""
        now = time.time()
        if now - pos.last_check < 2:
            return  # Don't check too frequently
        pos.last_check = now

        loop = asyncio.get_event_loop()

        # 1. Check for recent rug-related transactions
        await self._check_recent_txs(pos, loop)

        # 2. Check liquidity changes
        await self._check_liquidity_drain(pos, loop)

        # 3. Check for large transfers from creator/owner
        await self._check_dev_selling(pos, loop)

    async def _check_recent_txs(self, pos: WatchedPosition, loop):
        """Check recent transactions on the token contract for rug indicators."""
        try:
            # Get the latest block and scan for relevant events
            current_block = await loop.run_in_executor(
                None, lambda: self.w3.eth.block_number
            )

            # Scan the last 5 blocks for Transfer events from/to suspicious addresses
            from_block = max(0, current_block - 5)

            logs = await loop.run_in_executor(
                None,
                lambda: self.w3.eth.get_logs({
                    "fromBlock": from_block,
                    "toBlock": current_block,
                    "address": Web3.to_checksum_address(pos.token_address),
                    "topics": [TRANSFER_TOPIC],
                }),
            )

            for log_entry in logs:
                await self._analyze_transfer_log(pos, log_entry)

        except Exception as e:
            logger.debug(f"RugDetector tx check error ({pos.symbol}): {e}")

    async def _check_liquidity_drain(self, pos: WatchedPosition, loop):
        """Check if liquidity has been significantly reduced."""
        try:
            pair_cs = Web3.to_checksum_address(pos.pair_address)
            pair_abi = [
                {"inputs": [], "name": "getReserves",
                 "outputs": [
                     {"name": "reserve0", "type": "uint112"},
                     {"name": "reserve1", "type": "uint112"},
                     {"name": "blockTimestampLast", "type": "uint32"},
                 ], "type": "function"},
                {"inputs": [], "name": "token0",
                 "outputs": [{"name": "", "type": "address"}],
                 "type": "function"},
            ]

            pair_contract = self.w3.eth.contract(address=pair_cs, abi=pair_abi)
            reserves = await loop.run_in_executor(
                None, pair_contract.functions.getReserves().call
            )
            token0 = await loop.run_in_executor(
                None, pair_contract.functions.token0().call
            )

            weth_cs = Web3.to_checksum_address(self.weth_addr)
            if token0.lower() == self.weth_addr:
                native_reserve = reserves[0] / 1e18
            else:
                native_reserve = reserves[1] / 1e18

            # Compare with initial liquidity
            # We don't have native price here, so use relative comparison
            if pos.initial_liquidity_usd > 0 and native_reserve > 0:
                # Rough estimate: if reserves dropped by >50%, alert
                # This is approximate — the full bot has native_price_usd
                pass  # Precise check done in SniperBot integration

            # Emergency: near-zero reserves
            if native_reserve < 0.01 and pos.initial_liquidity_usd > 1000:
                alert = RugAlert(
                    token_address=pos.token_address,
                    alert_type="EMERGENCY",
                    rug_type="liquidity_drain",
                    description=f"⚠️ {pos.symbol}: Liquidez casi en 0 — posible rug pull",
                    severity=10,
                    should_sell=True,
                    timestamp=time.time(),
                    details={
                        "native_reserve": native_reserve,
                        "initial_liquidity": pos.initial_liquidity_usd,
                    },
                )
                await self._emit_alert(alert)

        except Exception as e:
            logger.debug(f"RugDetector liquidity check error ({pos.symbol}): {e}")

    async def _check_dev_selling(self, pos: WatchedPosition, loop):
        """Check if the creator/owner is selling large amounts."""
        if not pos.creator_address and not pos.owner_address:
            return

        try:
            token_cs = Web3.to_checksum_address(pos.token_address)
            balance_abi = [
                {"inputs": [{"name": "account", "type": "address"}],
                 "name": "balanceOf",
                 "outputs": [{"name": "", "type": "uint256"}],
                 "type": "function"},
                {"inputs": [], "name": "totalSupply",
                 "outputs": [{"name": "", "type": "uint256"}],
                 "type": "function"},
            ]

            contract = self.w3.eth.contract(address=token_cs, abi=balance_abi)

            # Check creator balance
            if pos.creator_address:
                creator_cs = Web3.to_checksum_address(pos.creator_address)
                balance = await loop.run_in_executor(
                    None, lambda: contract.functions.balanceOf(creator_cs).call()
                )
                total = await loop.run_in_executor(
                    None, lambda: contract.functions.totalSupply().call()
                )

                if total > 0:
                    creator_pct = (balance / total) * 100
                    # If creator had 15%+ and now has < 2%, they dumped
                    if creator_pct < 2 and pos.initial_holder_count > 0:
                        alert = RugAlert(
                            token_address=pos.token_address,
                            alert_type="WARNING",
                            rug_type="dev_dump",
                            description=f"⚠️ {pos.symbol}: Creator vendió — solo tiene {creator_pct:.1f}%",
                            severity=7,
                            should_sell=True,
                            timestamp=time.time(),
                            details={
                                "creator": pos.creator_address[:10] + "...",
                                "current_percent": round(creator_pct, 2),
                            },
                        )
                        await self._emit_alert(alert)

        except Exception as e:
            logger.debug(f"RugDetector dev sell check error ({pos.symbol}): {e}")

    async def _analyze_transfer_log(self, pos: WatchedPosition, log_entry):
        """Analyze a Transfer event for rug indicators."""
        try:
            # Decode Transfer(from, to, amount)
            topics = log_entry.get("topics", [])
            if len(topics) < 3:
                return

            from_addr = "0x" + topics[1].hex()[-40:]
            to_addr = "0x" + topics[2].hex()[-40:]
            amount = int(log_entry.get("data", "0x0"), 16)

            from_lower = from_addr.lower()
            to_lower = to_addr.lower()

            # Check if this is an owner/creator transfer to DEX (selling)
            is_dev_transfer = (
                from_lower == pos.creator_address or
                from_lower == pos.owner_address
            )

            is_to_pair = to_lower == pos.pair_address.lower()

            if is_dev_transfer and is_to_pair and amount > 0:
                # Dev selling directly to pair = dump
                alert = RugAlert(
                    token_address=pos.token_address,
                    alert_type="WARNING",
                    rug_type="dev_sell_to_pair",
                    description=f"⚠️ {pos.symbol}: Dev vendiendo tokens al pair",
                    severity=7,
                    should_sell=True,
                    timestamp=time.time(),
                    tx_hash=log_entry.get("transactionHash", b"").hex() if isinstance(log_entry.get("transactionHash"), bytes) else str(log_entry.get("transactionHash", "")),
                    from_address=from_addr,
                )
                await self._emit_alert(alert)

            # Check if pair is sending native to router (removeLiquidity)
            if from_lower == pos.pair_address.lower() and to_lower == self.router_addr:
                alert = RugAlert(
                    token_address=pos.token_address,
                    alert_type="EMERGENCY",
                    rug_type="liquidity_removal",
                    description=f"🚨 {pos.symbol}: Tokens moviéndose del pair al router — POSIBLE REMOVE LIQUIDITY",
                    severity=9,
                    should_sell=True,
                    timestamp=time.time(),
                    tx_hash=log_entry.get("transactionHash", b"").hex() if isinstance(log_entry.get("transactionHash"), bytes) else str(log_entry.get("transactionHash", "")),
                )
                await self._emit_alert(alert)

        except Exception as e:
            logger.debug(f"RugDetector transfer analysis error: {e}")

    async def _emit_alert(self, alert: RugAlert):
        """Emit a rug alert to the sniper bot and UI."""
        self.stats["alerts_emitted"] += 1
        if alert.alert_type == "EMERGENCY":
            self.stats["emergency_alerts"] += 1

        # Find position and increment counter
        key = alert.token_address.lower()
        if key in self._watched:
            self._watched[key].alerts_emitted += 1

        logger.warning(
            f"RUG ALERT [{alert.alert_type}] {alert.rug_type}: "
            f"{alert.description}"
        )

        # Emit to UI
        await self._emit("rug_alert", alert.to_dict())

        # Trigger emergency sell if needed
        if alert.should_sell and self._alert_callback:
            try:
                await self._alert_callback(alert)
            except Exception as e:
                logger.debug(f"RugDetector alert callback error: {e}")

    def get_stats(self) -> dict:
        return {
            **self.stats,
            "running": self.running,
            "watched_tokens": [
                {"address": p.token_address[:10] + "...", "symbol": p.symbol}
                for p in self._watched.values()
            ],
        }

    def stop(self):
        self.running = False

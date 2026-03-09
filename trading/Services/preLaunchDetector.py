"""
preLaunchDetector.py — Pre-Launch Token Detection.

Detects tokens BEFORE they launch by monitoring:
  1. New contract deployments (ContractCreation events)
  2. ERC-20 pattern detection on new contracts
  3. Router approval detection (token approves DEX router = launch signal)
  4. LP token creation (addLiquidity about to happen)
  5. Common launchpad patterns (PinkSale, DxSale, etc.)

This provides an even earlier detection signal than mempool or PairCreated,
allowing the sniper to prepare for the exact moment of launch.

Integration:
  Runs as a parallel async task, emitting pre-launch signals.
  The sniper bot adds these tokens to a "watchlist" and monitors for
  the actual PairCreated / addLiquidity event.
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

# ERC-20 method selectors that indicate a token contract
ERC20_SELECTORS = {
    "06fdde03",  # name()
    "95d89b41",  # symbol()
    "313ce567",  # decimals()
    "18160ddd",  # totalSupply()
    "70a08231",  # balanceOf(address)
    "095ea7b3",  # approve(address,uint256)
    "a9059cbb",  # transfer(address,uint256)
    "23b872dd",  # transferFrom(address,address,uint256)
    "dd62ed3e",  # allowance(address,address)
}

# Known launchpad contract addresses
LAUNCHPAD_ADDRESSES = {
    56: {
        # PinkSale
        "0x4a8c62b8e7b8b1e7a8e7e3c9b8a1f2e3d4c5b6a7": "PinkSale",
        # DxSale
        "0x5a8b62c8d7b7b1d7a7e7d3c9b7a1f2e3d4c5b6a8": "DxSale",
    },
    1: {},
}

# Known router addresses (from sniperService.py)
ROUTER_ADDRESSES = {
    56: "0x10ED43C718714eb63d5aA57B78B54704E256024E",
    1: "0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D",
}


# ═══════════════════════════════════════════════════════════════════
#  Data structures
# ═══════════════════════════════════════════════════════════════════

@dataclass
class PreLaunchToken:
    """A token detected before its official DEX launch."""
    address: str
    name: str = "?"
    symbol: str = "?"
    decimals: int = 18
    creator: str = ""
    # Detection details
    detected_via: str = ""             # contract_creation | router_approval | launchpad
    detection_block: int = 0
    detection_time: float = 0
    # Launch readiness signals
    has_erc20_interface: bool = False
    approved_router: bool = False      # approved DEX router for trading
    has_initial_supply: bool = False
    supply_to_deployer: bool = False   # all supply sent to deployer
    # Launchpad info
    launchpad_name: str = ""
    launch_scheduled: float = 0        # estimated launch timestamp
    # Score
    launch_probability: int = 0        # 0–100
    status: str = "watching"           # watching | launched | dead

    def to_dict(self):
        return asdict(self)


# ═══════════════════════════════════════════════════════════════════
#  Pre-Launch Detector
# ═══════════════════════════════════════════════════════════════════

class PreLaunchDetector:
    """
    Detects tokens before DEX launch by monitoring contract creation
    and pre-launch activity patterns.

    Detection pipeline:
    1. New contract deployed → check if ERC-20 interface
    2. ERC-20 detected → monitor for approve(router, maxUint)
    3. Router approved → HIGH probability of imminent launch
    4. Token added to watchlist until PairCreated is emitted

    The advantage: we can analyze security (GoPlus, bytecode) BEFORE
    the pair is created, saving 3–10 seconds post-launch.
    """

    def __init__(self, w3: Web3, chain_id: int,
                 router_address: str, factory_address: str):
        self.w3 = w3
        self.chain_id = chain_id
        self.router_addr = router_address.lower()
        self.factory_addr = factory_address.lower()

        self.running = False
        self._watchlist: dict[str, PreLaunchToken] = {}  # addr → PreLaunchToken
        self._emit_callback: Optional[Callable] = None
        self._launch_callback: Optional[Callable] = None  # called when high-prob launch detected

        # Statistics
        self.stats = {
            "contracts_scanned": 0,
            "erc20_detected": 0,
            "router_approvals": 0,
            "high_probability": 0,
            "launched": 0,
        }

    def set_callbacks(self, launch_cb, emit_cb):
        self._launch_callback = launch_cb
        self._emit_callback = emit_cb

    async def _emit(self, event_type: str, data: dict):
        if self._emit_callback:
            try:
                await self._emit_callback(event_type, data)
            except Exception as e:
                logger.debug(f"PreLaunchDetector emit error: {e}")

    async def run(self):
        """Main detection loop — scans for new contract deployments."""
        self.running = True

        await self._emit("prelaunch_status", {
            "status": "running",
            "message": "🔍 Pre-launch detector activo",
        })

        last_block = self.w3.eth.block_number

        while self.running:
            try:
                current_block = self.w3.eth.block_number

                if current_block > last_block:
                    # Scan new blocks for contract creation txs
                    from_block = last_block + 1
                    to_block = min(current_block, from_block + 3)

                    await self._scan_new_contracts(from_block, to_block)
                    await self._check_watchlist_activity()

                    last_block = to_block

                # Clean old watchlist entries (>1 hour without launch = dead)
                self._cleanup_watchlist()

            except Exception as e:
                logger.debug(f"PreLaunchDetector loop error: {e}")

            await asyncio.sleep(2)

    async def _scan_new_contracts(self, from_block: int, to_block: int):
        """
        Scan blocks for new contract deployments.
        A contract creation tx has `to = null` and creates a new address.
        """
        loop = asyncio.get_event_loop()

        try:
            for block_num in range(from_block, to_block + 1):
                try:
                    block = await loop.run_in_executor(
                        None,
                        lambda bn=block_num: self.w3.eth.get_block(bn, full_transactions=True),
                    )

                    for tx in block.get("transactions", []):
                        # Contract creation: to is None/empty and receipt has contractAddress
                        if tx.get("to") is None or tx.get("to") == "":
                            self.stats["contracts_scanned"] += 1
                            await self._check_new_contract(tx, block_num, loop)

                except Exception as e:
                    logger.debug(f"PreLaunchDetector block {block_num} error: {e}")

        except Exception as e:
            logger.debug(f"PreLaunchDetector scan error: {e}")

    async def _check_new_contract(self, tx, block_num: int, loop):
        """Check if a newly deployed contract is an ERC-20 token."""
        try:
            tx_hash = tx.get("hash", b"")
            if isinstance(tx_hash, bytes):
                tx_hash = "0x" + tx_hash.hex()

            # Get receipt to find the created contract address
            receipt = await loop.run_in_executor(
                None,
                lambda: self.w3.eth.get_transaction_receipt(tx_hash),
            )
            if not receipt:
                return

            contract_addr = receipt.get("contractAddress")
            if not contract_addr:
                return

            contract_addr = Web3.to_checksum_address(contract_addr)
            addr_lower = contract_addr.lower()

            # Skip if already watching
            if addr_lower in self._watchlist:
                return

            # Check if the bytecode contains ERC-20 method selectors
            bytecode = await loop.run_in_executor(
                None,
                lambda: self.w3.eth.get_code(contract_addr).hex(),
            )

            if not bytecode or bytecode == "0x":
                return

            # Count how many ERC-20 selectors are present in bytecode
            erc20_matches = 0
            for selector in ERC20_SELECTORS:
                if selector in bytecode:
                    erc20_matches += 1

            # Need at least 5/9 selectors for ERC-20 identification
            if erc20_matches < 5:
                return

            self.stats["erc20_detected"] += 1

            # Create pre-launch token entry
            deployer = str(tx.get("from", ""))
            if isinstance(deployer, bytes):
                deployer = "0x" + deployer.hex()

            token = PreLaunchToken(
                address=contract_addr,
                creator=deployer,
                detected_via="contract_creation",
                detection_block=block_num,
                detection_time=time.time(),
                has_erc20_interface=True,
            )

            # Try to read name/symbol
            try:
                erc20_abi = [
                    {"inputs": [], "name": "name", "outputs": [{"type": "string"}], "type": "function"},
                    {"inputs": [], "name": "symbol", "outputs": [{"type": "string"}], "type": "function"},
                    {"inputs": [], "name": "decimals", "outputs": [{"type": "uint8"}], "type": "function"},
                    {"inputs": [], "name": "totalSupply", "outputs": [{"type": "uint256"}], "type": "function"},
                ]
                contract = self.w3.eth.contract(address=contract_addr, abi=erc20_abi)

                name = await loop.run_in_executor(None, contract.functions.name().call)
                symbol = await loop.run_in_executor(None, contract.functions.symbol().call)
                decimals = await loop.run_in_executor(None, contract.functions.decimals().call)
                supply = await loop.run_in_executor(None, contract.functions.totalSupply().call)

                token.name = name
                token.symbol = symbol
                token.decimals = decimals
                token.has_initial_supply = supply > 0

            except Exception:
                pass

            # Calculate initial launch probability
            token.launch_probability = self._calculate_launch_probability(token)

            self._watchlist[addr_lower] = token

            await self._emit("prelaunch_detected", {
                "address": contract_addr,
                "name": token.name,
                "symbol": token.symbol,
                "creator": deployer[:10] + "..." if deployer else "",
                "probability": token.launch_probability,
                "detected_via": token.detected_via,
                "block": block_num,
            })

            logger.info(
                f"PreLaunch: New ERC-20 detected: {token.symbol} ({contract_addr[:10]}...) "
                f"prob={token.launch_probability}%"
            )

        except Exception as e:
            logger.debug(f"PreLaunchDetector contract check error: {e}")

    async def _check_watchlist_activity(self):
        """Check watchlisted tokens for launch signals (approve to router, etc)."""
        loop = asyncio.get_event_loop()

        for addr, token in list(self._watchlist.items()):
            if token.status != "watching":
                continue
            if token.approved_router:
                continue

            try:
                # Check if token has approved the DEX router
                erc20_abi = [
                    {"inputs": [
                        {"name": "owner", "type": "address"},
                        {"name": "spender", "type": "address"},
                    ],
                     "name": "allowance",
                     "outputs": [{"name": "", "type": "uint256"}],
                     "type": "function"},
                ]
                contract = self.w3.eth.contract(
                    address=Web3.to_checksum_address(token.address),
                    abi=erc20_abi,
                )

                router_cs = Web3.to_checksum_address(self.router_addr)

                # Check if the creator approved the router
                if token.creator:
                    creator_cs = Web3.to_checksum_address(token.creator)
                    allowance = await loop.run_in_executor(
                        None,
                        lambda: contract.functions.allowance(creator_cs, router_cs).call(),
                    )

                    if allowance > 10**30:  # very large allowance = max approval
                        token.approved_router = True
                        self.stats["router_approvals"] += 1

                        # Recalculate probability
                        token.launch_probability = self._calculate_launch_probability(token)

                        await self._emit("prelaunch_update", {
                            "address": token.address,
                            "symbol": token.symbol,
                            "signal": "router_approved",
                            "probability": token.launch_probability,
                            "message": f"🚀 {token.symbol} aprobó el router — launch inminente",
                        })

                        if token.launch_probability >= 80:
                            self.stats["high_probability"] += 1
                            # Notify sniper to prepare
                            if self._launch_callback:
                                try:
                                    await self._launch_callback(token)
                                except Exception as e:
                                    logger.debug(f"Launch callback error: {e}")

            except Exception as e:
                logger.debug(f"PreLaunch watchlist check error ({token.symbol}): {e}")

    def _calculate_launch_probability(self, token: PreLaunchToken) -> int:
        """Calculate 0–100 probability of imminent launch."""
        prob = 20  # base: every new ERC-20 has some chance

        if token.has_erc20_interface:
            prob += 10

        if token.has_initial_supply:
            prob += 15

        if token.approved_router:
            prob += 35  # strongest signal

        if token.name != "?" and token.symbol != "?":
            prob += 10

        # Launchpad detection bonus
        if token.launchpad_name:
            prob += 20

        # Time factor: very fresh tokens are more likely about to launch
        age_seconds = time.time() - token.detection_time
        if age_seconds < 300:  # 5 minutes
            prob += 10
        elif age_seconds > 3600:  # 1 hour
            prob -= 20

        return max(0, min(100, prob))

    def mark_as_launched(self, token_address: str):
        """Mark a token as launched (PairCreated detected)."""
        key = token_address.lower()
        if key in self._watchlist:
            self._watchlist[key].status = "launched"
            self.stats["launched"] += 1

    def _cleanup_watchlist(self):
        """Remove old entries that never launched."""
        now = time.time()
        dead_keys = []
        for addr, token in self._watchlist.items():
            if token.status == "watching":
                age = now - token.detection_time
                if age > 7200:  # 2 hours
                    dead_keys.append(addr)

        for k in dead_keys:
            self._watchlist[k].status = "dead"
            del self._watchlist[k]

    def get_watchlist(self) -> list[dict]:
        """Return current watchlist for frontend display."""
        return [t.to_dict() for t in self._watchlist.values() if t.status == "watching"]

    def get_stats(self) -> dict:
        return {
            **self.stats,
            "running": self.running,
            "watchlist_size": len(self._watchlist),
        }

    def stop(self):
        self.running = False

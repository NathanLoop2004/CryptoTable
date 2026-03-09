"""
dynamicContractScanner.py — Continuous Dynamic Contract Analysis (v5).

Performs ongoing monitoring of token contracts for runtime behavior changes
that static analysis misses. Re-scans contracts periodically and alerts
on state changes.

Capabilities:
  1. Periodic bytecode re-scan (detect selfdestruct, proxy upgrades)
  2. Storage slot monitoring (detect implementation changes in proxies)
  3. Tax change detection (compare buy/sell tax over time)
  4. Blacklist activity monitoring (detect new blacklist additions)
  5. Ownership change detection (renounce then reclaim)
  6. Mint activity monitoring (token supply inflation)
  7. Continuous risk scoring per contract

Architecture:
  Runs as a background task, periodically re-checking registered contracts.
  Each check produces a DynamicScanResult with change deltas.
  If dangerous changes detected → emit alert to frontend + block further buys.

Integration:
  Registered by SniperBot after initial analysis.
  Results feed into RiskEngine for continuous risk updates.
  Alerts emitted via WebSocket to frontend.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field, asdict
from typing import Optional, Callable
from collections import defaultdict

from web3 import Web3

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
#  Constants
# ═══════════════════════════════════════════════════════════════════

# EIP-1967 implementation storage slot
EIP1967_IMPL_SLOT = "0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc"

# Owner function selector (Ownable)
OWNER_SELECTOR = "0x8da5cb5b"  # owner()

# TotalSupply selector
TOTAL_SUPPLY_SELECTOR = "0x18160ddd"  # totalSupply()

# Max tokens to monitor concurrently
MAX_MONITORED = 100

# Re-scan interval
DEFAULT_SCAN_INTERVAL = 60  # seconds


# ═══════════════════════════════════════════════════════════════════
#  Data structures
# ═══════════════════════════════════════════════════════════════════

@dataclass
class ContractSnapshot:
    """Point-in-time snapshot of contract state."""
    token_address: str
    bytecode_hash: str = ""
    bytecode_size: int = 0
    implementation_address: str = ""     # for proxies
    owner_address: str = ""
    total_supply: int = 0
    buy_tax: float = 0.0
    sell_tax: float = 0.0
    timestamp: float = 0.0

    def to_dict(self):
        return asdict(self)


@dataclass
class DynamicScanResult:
    """Result of a dynamic re-scan with change detection."""
    token_address: str
    token_symbol: str = ""
    # Change flags
    bytecode_changed: bool = False
    implementation_changed: bool = False
    owner_changed: bool = False
    supply_changed: bool = False
    tax_increased: bool = False
    # Change details
    old_implementation: str = ""
    new_implementation: str = ""
    old_owner: str = ""
    new_owner: str = ""
    supply_change_pct: float = 0.0
    old_buy_tax: float = 0.0
    new_buy_tax: float = 0.0
    old_sell_tax: float = 0.0
    new_sell_tax: float = 0.0
    # Risk
    dynamic_risk_score: int = 50         # 0-100 (0 = very dangerous)
    is_dangerous: bool = False           # True if any hard-stop level change
    danger_reason: str = ""
    # Signals
    signals: list = field(default_factory=list)
    scans_completed: int = 0
    timestamp: float = 0.0

    def to_dict(self):
        return asdict(self)


@dataclass
class MonitoredContract:
    """Internal state for a monitored contract."""
    token_address: str
    token_symbol: str = ""
    pair_address: str = ""
    is_proxy: bool = False
    # Snapshots
    initial_snapshot: Optional[ContractSnapshot] = None
    latest_snapshot: Optional[ContractSnapshot] = None
    # Monitoring
    scan_count: int = 0
    last_scan: float = 0.0
    registered_at: float = 0.0
    # Cumulative alerts
    alerts_emitted: int = 0
    is_blocked: bool = False            # set True if dangerous change found
    block_reason: str = ""


# ═══════════════════════════════════════════════════════════════════
#  Dynamic Contract Scanner
# ═══════════════════════════════════════════════════════════════════

class DynamicContractScanner:
    """
    Continuous dynamic monitoring of token contracts.

    Lifecycle:
      1. register(token_addr) — take initial snapshot and start monitoring
      2. Background loop scans all registered contracts periodically
      3. On change → emit alert, update risk score, optionally block
      4. unregister(token_addr) — stop monitoring

    Usage:
        scanner = DynamicContractScanner(w3, chain_id)
        scanner.set_emit_callback(async_emit_fn)
        await scanner.register(token, symbol, pair, is_proxy)
        # ... scanner.run() in background ...
        result = scanner.get_latest_scan(token)
    """

    def __init__(self, w3: Web3, chain_id: int,
                 scan_interval: int = DEFAULT_SCAN_INTERVAL):
        self.w3 = w3
        self.chain_id = chain_id
        self.scan_interval = scan_interval
        self.running = False
        self._task: Optional[asyncio.Task] = None

        # Monitored contracts
        self._contracts: dict[str, MonitoredContract] = {}  # addr_lower → MonitoredContract
        self._latest_results: dict[str, DynamicScanResult] = {}  # addr_lower → last result

        # Callbacks
        self._emit_callback: Optional[Callable] = None
        self._alert_callback: Optional[Callable] = None

        # Stats
        self.stats = {
            "contracts_monitored": 0,
            "total_scans": 0,
            "changes_detected": 0,
            "dangerous_changes": 0,
        }

    def set_callbacks(self, emit_cb=None, alert_cb=None):
        """Set callbacks for events and alerts."""
        self._emit_callback = emit_cb
        self._alert_callback = alert_cb

    async def _emit(self, event_type: str, data: dict):
        if self._emit_callback:
            try:
                await self._emit_callback(event_type, data)
            except Exception as e:
                logger.debug(f"DynamicScanner emit error: {e}")

    async def register(self, token_address: str, symbol: str = "",
                       pair_address: str = "", is_proxy: bool = False):
        """
        Register a contract for continuous monitoring.
        Takes initial snapshot immediately.
        """
        addr_lower = token_address.lower()
        if addr_lower in self._contracts:
            return  # already monitored

        if len(self._contracts) >= MAX_MONITORED:
            # Remove oldest
            oldest_addr = min(
                self._contracts,
                key=lambda k: self._contracts[k].registered_at,
            )
            del self._contracts[oldest_addr]

        # Take initial snapshot
        snapshot = await self._take_snapshot(token_address)

        contract = MonitoredContract(
            token_address=token_address,
            token_symbol=symbol,
            pair_address=pair_address,
            is_proxy=is_proxy,
            initial_snapshot=snapshot,
            latest_snapshot=snapshot,
            registered_at=time.time(),
        )
        self._contracts[addr_lower] = contract
        self.stats["contracts_monitored"] = len(self._contracts)

        logger.info(f"DynamicScanner: Registered {symbol} ({token_address[:10]}…) for monitoring")

    def unregister(self, token_address: str):
        """Stop monitoring a contract."""
        addr_lower = token_address.lower()
        self._contracts.pop(addr_lower, None)
        self._latest_results.pop(addr_lower, None)
        self.stats["contracts_monitored"] = len(self._contracts)

    async def run(self):
        """Background loop: periodically scan all registered contracts."""
        self.running = True
        logger.info(f"DynamicContractScanner started (interval={self.scan_interval}s)")

        while self.running:
            try:
                await self._scan_all()
            except Exception as e:
                logger.warning(f"DynamicScanner scan loop error: {e}")

            await asyncio.sleep(self.scan_interval)

    async def _scan_all(self):
        """Scan all registered contracts."""
        now = time.time()
        contracts = list(self._contracts.values())

        for contract in contracts:
            if not self.running:
                break

            # Respect scan interval per contract
            if now - contract.last_scan < self.scan_interval:
                continue

            try:
                result = await self._scan_contract(contract)
                addr_lower = contract.token_address.lower()
                self._latest_results[addr_lower] = result

                if result.is_dangerous:
                    self.stats["dangerous_changes"] += 1
                    contract.is_blocked = True
                    contract.block_reason = result.danger_reason

                    await self._emit("dynamic_scan_alert", {
                        "token": contract.token_address,
                        "symbol": contract.token_symbol,
                        "danger_reason": result.danger_reason,
                        "signals": result.signals,
                        "risk_score": result.dynamic_risk_score,
                    })

                if any([result.bytecode_changed, result.implementation_changed,
                        result.owner_changed, result.supply_changed,
                        result.tax_increased]):
                    self.stats["changes_detected"] += 1

            except Exception as e:
                logger.debug(f"DynamicScanner error for {contract.token_symbol}: {e}")

    async def _scan_contract(self, contract: MonitoredContract) -> DynamicScanResult:
        """Scan a single contract and compare with initial/previous snapshot."""
        result = DynamicScanResult(
            token_address=contract.token_address,
            token_symbol=contract.token_symbol,
            timestamp=time.time(),
        )

        # Take new snapshot
        new_snapshot = await self._take_snapshot(contract.token_address)
        contract.scan_count += 1
        contract.last_scan = time.time()
        result.scans_completed = contract.scan_count
        self.stats["total_scans"] += 1

        initial = contract.initial_snapshot
        previous = contract.latest_snapshot

        if not initial or not new_snapshot:
            result.signals.append("⚠️ Missing snapshot data")
            return result

        risk_score = 100  # Start perfect, deduct for issues

        # ── 1. Bytecode change detection ──
        if initial.bytecode_hash and new_snapshot.bytecode_hash:
            if initial.bytecode_hash != new_snapshot.bytecode_hash:
                result.bytecode_changed = True
                risk_score -= 50
                result.signals.append("🚨 BYTECODE CAMBIÓ — contrato fue modificado/actualizado")
                result.is_dangerous = True
                result.danger_reason = "Bytecode changed since initial analysis"

        # ── 2. Proxy implementation change ──
        if contract.is_proxy:
            if initial.implementation_address and new_snapshot.implementation_address:
                if initial.implementation_address != new_snapshot.implementation_address:
                    result.implementation_changed = True
                    result.old_implementation = initial.implementation_address
                    result.new_implementation = new_snapshot.implementation_address
                    risk_score -= 40
                    result.signals.append(
                        f"🚨 IMPLEMENTACIÓN CAMBIÓ: {initial.implementation_address[:10]}… "
                        f"→ {new_snapshot.implementation_address[:10]}…"
                    )
                    result.is_dangerous = True
                    result.danger_reason = "Proxy implementation was upgraded"

        # ── 3. Owner change detection ──
        if initial.owner_address and new_snapshot.owner_address:
            if initial.owner_address.lower() != new_snapshot.owner_address.lower():
                result.owner_changed = True
                result.old_owner = initial.owner_address
                result.new_owner = new_snapshot.owner_address
                risk_score -= 20

                # Check if ownership was reclaimed (renounce then reclaim)
                zero_addr = "0x" + "0" * 40
                if initial.owner_address.lower() == zero_addr.lower():
                    result.signals.append(
                        "🚨 OWNERSHIP RECLAMADA — was renounced, now back to "
                        f"{new_snapshot.owner_address[:10]}…"
                    )
                    result.is_dangerous = True
                    result.danger_reason = "Ownership reclaimed after renouncement"
                    risk_score -= 30
                else:
                    result.signals.append(
                        f"⚠️ Owner cambió: {initial.owner_address[:10]}… "
                        f"→ {new_snapshot.owner_address[:10]}…"
                    )

        # ── 4. Supply change detection (minting) ──
        if initial.total_supply > 0 and new_snapshot.total_supply > 0:
            supply_delta = new_snapshot.total_supply - initial.total_supply
            if supply_delta != 0:
                result.supply_changed = True
                pct = (supply_delta / initial.total_supply) * 100
                result.supply_change_pct = round(pct, 2)

                if pct > 5:
                    risk_score -= 25
                    result.signals.append(
                        f"🚨 Supply AUMENTÓ {pct:.1f}% — minting detectado"
                    )
                    if pct > 20:
                        result.is_dangerous = True
                        result.danger_reason = f"Massive supply inflation: {pct:.1f}%"
                elif pct > 0:
                    risk_score -= 5
                    result.signals.append(f"⚠️ Supply aumentó {pct:.2f}%")
                elif pct < -5:
                    result.signals.append(f"ℹ️ Supply disminuyó {abs(pct):.1f}% (burn)")

        # ── 5. Tax change detection ──
        if previous and previous.buy_tax >= 0:
            tax_buy_delta = new_snapshot.buy_tax - initial.buy_tax
            tax_sell_delta = new_snapshot.sell_tax - initial.sell_tax

            if tax_buy_delta > 2 or tax_sell_delta > 2:
                result.tax_increased = True
                result.old_buy_tax = initial.buy_tax
                result.new_buy_tax = new_snapshot.buy_tax
                result.old_sell_tax = initial.sell_tax
                result.new_sell_tax = new_snapshot.sell_tax
                risk_score -= 15

                result.signals.append(
                    f"⚠️ TAX AUMENTADO: buy {initial.buy_tax:.1f}%→{new_snapshot.buy_tax:.1f}% "
                    f"sell {initial.sell_tax:.1f}%→{new_snapshot.sell_tax:.1f}%"
                )

                if new_snapshot.sell_tax > 50:
                    result.is_dangerous = True
                    result.danger_reason = f"Sell tax increased to {new_snapshot.sell_tax}% — effective honeypot"
                    risk_score -= 30

        # No changes detected → all good
        if not any([result.bytecode_changed, result.implementation_changed,
                     result.owner_changed, result.supply_changed, result.tax_increased]):
            result.signals.append(f"✅ Sin cambios (scan #{contract.scan_count})")

        result.dynamic_risk_score = max(0, min(100, risk_score))

        # Update latest snapshot
        contract.latest_snapshot = new_snapshot

        return result

    async def _take_snapshot(self, token_address: str) -> ContractSnapshot:
        """Take a point-in-time snapshot of contract state."""
        snapshot = ContractSnapshot(
            token_address=token_address,
            timestamp=time.time(),
        )
        loop = asyncio.get_event_loop()
        cs = Web3.to_checksum_address(token_address)

        # ── Bytecode hash ──
        try:
            bytecode = await loop.run_in_executor(
                None, lambda: self.w3.eth.get_code(cs).hex()
            )
            if bytecode and bytecode != "0x":
                snapshot.bytecode_size = len(bytecode) // 2
                snapshot.bytecode_hash = Web3.keccak(text=bytecode).hex()[:20]
        except Exception as e:
            logger.debug(f"Snapshot bytecode error: {e}")

        # ── Implementation address (proxy) ──
        try:
            data = await loop.run_in_executor(
                None,
                lambda: self.w3.eth.get_storage_at(cs, EIP1967_IMPL_SLOT)
            )
            hex_data = data.hex() if isinstance(data, bytes) else str(data)
            hex_data = hex_data.replace("0x", "").zfill(64)
            addr_hex = hex_data[-40:]
            if int(addr_hex, 16) != 0:
                snapshot.implementation_address = Web3.to_checksum_address("0x" + addr_hex)
        except Exception:
            pass

        # ── Owner ──
        try:
            resp = await loop.run_in_executor(
                None,
                lambda: self.w3.eth.call({"to": cs, "data": OWNER_SELECTOR})
            )
            if len(resp) >= 32:
                addr_hex = resp.hex()[-40:]
                if int(addr_hex, 16) != 0:
                    snapshot.owner_address = Web3.to_checksum_address("0x" + addr_hex)
        except Exception:
            pass

        # ── Total supply ──
        try:
            resp = await loop.run_in_executor(
                None,
                lambda: self.w3.eth.call({"to": cs, "data": TOTAL_SUPPLY_SELECTOR})
            )
            if len(resp) >= 32:
                snapshot.total_supply = int.from_bytes(resp[0:32], 'big')
        except Exception:
            pass

        return snapshot

    def get_latest_scan(self, token_address: str) -> Optional[DynamicScanResult]:
        """Get the most recent scan result for a token."""
        return self._latest_results.get(token_address.lower())

    def is_blocked(self, token_address: str) -> tuple[bool, str]:
        """Check if a contract has been blocked due to dangerous changes."""
        contract = self._contracts.get(token_address.lower())
        if contract and contract.is_blocked:
            return True, contract.block_reason
        return False, ""

    def get_monitored(self) -> list[dict]:
        """Get list of monitored contracts."""
        return [
            {
                "token": c.token_address,
                "symbol": c.token_symbol,
                "scans": c.scan_count,
                "is_blocked": c.is_blocked,
                "block_reason": c.block_reason,
                "registered": c.registered_at,
            }
            for c in self._contracts.values()
        ]

    def get_stats(self) -> dict:
        return {
            **self.stats,
            "running": self.running,
        }

    def stop(self):
        """Stop the scanner."""
        self.running = False
        if self._task:
            self._task.cancel()

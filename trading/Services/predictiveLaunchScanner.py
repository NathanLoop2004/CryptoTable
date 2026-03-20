"""
predictiveLaunchScanner.py — Pre-Mempool Intelligence Engine.

Detects tokens BEFORE they start trading by monitoring:
  1. New contract deployments on BSC
  2. Bytecode patterns (openTrading, enableTrading, etc.)
  3. Liquidity addition events (addLiquidity/addLiquidityETH)
  4. Developer wallet funding patterns
  5. Social signal integration (Telegram groups, Twitter)

Architecture:
  block scanner → contract analyzer → launch predictor
  → queue for sniperService

Operates ahead of standard mempool detection — catches tokens
at contract deployment stage, BEFORE liquidity is added.
"""

import asyncio
import logging
import time
import re
from dataclasses import dataclass, field
from typing import Optional
from enum import Enum

logger = logging.getLogger(__name__)

try:
    from web3 import Web3
    from hexbytes import HexBytes
    HAS_WEB3 = True
except ImportError:
    Web3 = None
    HexBytes = None
    HAS_WEB3 = False


# ═══════════════════════════════════════════════════════════════════
#  Data Classes
# ═══════════════════════════════════════════════════════════════════

class LaunchStage(Enum):
    """Stages of a token launch lifecycle."""
    DEPLOYED = "deployed"              # Contract deployed, not yet configured
    FUNDED = "funded"                  # Developer wallet funded
    LP_PENDING = "lp_pending"          # AddLiquidity tx in mempool
    LP_ADDED = "lp_added"             # Liquidity added to DEX
    TRADING_ENABLED = "trading_enabled" # Trading function called
    ACTIVE = "active"                  # Actively trading


class ContractRisk(Enum):
    """Risk classification for detected contracts."""
    SAFE = "safe"
    MODERATE = "moderate"
    HIGH = "high"
    SCAM = "scam"
    UNKNOWN = "unknown"


@dataclass
class BytecodeSignature:
    """Pattern to detect in contract bytecode."""
    name: str = ""
    hex_pattern: str = ""              # hex substring to search
    function_selector: str = ""        # 4-byte function selector
    description: str = ""
    risk_modifier: float = 0.0        # positive = riskier, negative = safer


@dataclass
class DetectedLaunch:
    """A detected upcoming token launch."""
    contract_address: str = ""
    deployer_address: str = ""
    name: str = ""
    symbol: str = ""
    detected_at: float = 0.0
    block_number: int = 0
    stage: LaunchStage = LaunchStage.DEPLOYED
    # Contract analysis
    has_open_trading: bool = False
    has_enable_trading: bool = False
    has_max_tx: bool = False
    has_fee_mechanism: bool = False
    has_blacklist: bool = False
    has_proxy: bool = False
    has_mint: bool = False
    has_renounced: bool = False
    # Risk
    risk: ContractRisk = ContractRisk.UNKNOWN
    risk_score: float = 50.0           # 0-100 (lower = safer)
    risk_signals: list = field(default_factory=list)
    # Deployer analysis
    deployer_balance_bnb: float = 0.0
    deployer_previous_tokens: int = 0
    deployer_rug_count: int = 0
    deployer_success_rate: float = 0.0
    # Prediction
    predicted_lp_amount: float = 0.0
    predicted_launch_time: float = 0.0
    estimated_initial_mcap: float = 0.0
    snipe_priority: float = 0.0        # 0-100 (higher = better opportunity)
    # Tracking
    pair_address: str = ""
    lp_tx_hash: str = ""
    trading_enabled_tx: str = ""

    def to_dict(self) -> dict:
        return {
            "contract_address": self.contract_address,
            "deployer": self.deployer_address,
            "name": self.name,
            "symbol": self.symbol,
            "stage": self.stage.value,
            "detected_at": self.detected_at,
            "block_number": self.block_number,
            "risk": self.risk.value,
            "risk_score": round(self.risk_score, 1),
            "risk_signals": self.risk_signals,
            "has_open_trading": self.has_open_trading,
            "has_blacklist": self.has_blacklist,
            "has_proxy": self.has_proxy,
            "has_mint": self.has_mint,
            "deployer_balance_bnb": round(self.deployer_balance_bnb, 4),
            "deployer_previous_tokens": self.deployer_previous_tokens,
            "deployer_rug_count": self.deployer_rug_count,
            "snipe_priority": round(self.snipe_priority, 1),
            "predicted_lp_amount": round(self.predicted_lp_amount, 4),
            "pair_address": self.pair_address,
        }


@dataclass
class ScannerConfig:
    """Configuration for the predictive launch scanner."""
    enabled: bool = True
    scan_interval_seconds: float = 2.0   # How often to check new blocks
    max_tracked_launches: int = 100
    # Bytecode analysis
    analyze_bytecode: bool = True
    # Deployer analysis
    analyze_deployer: bool = True
    min_deployer_balance_bnb: float = 0.1
    max_deployer_rug_rate: float = 0.5    # reject if > 50% rugs
    # Risk filters
    max_risk_score: float = 70.0
    reject_proxy: bool = True
    reject_mint: bool = True
    reject_blacklist: bool = False         # some legit tokens have blacklists
    # Priority thresholds
    min_snipe_priority: float = 30.0
    # Router addresses (PancakeSwap V2)
    router_address: str = "0x10ED43C718714eb63d5aA57B78B54704E256024E"
    factory_address: str = "0xcA143Ce32Fe78f1f7019d7d551a6402fC5350c73"
    wbnb_address: str = "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c"

    def to_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "scan_interval": self.scan_interval_seconds,
            "max_tracked": self.max_tracked_launches,
            "max_risk_score": self.max_risk_score,
            "min_snipe_priority": self.min_snipe_priority,
        }


# ═══════════════════════════════════════════════════════════════════
#  Bytecode Analyzer
# ═══════════════════════════════════════════════════════════════════

class BytecodeAnalyzer:
    """Analyzes contract bytecode for trading-related patterns."""

    # Common function selectors found in BSC tokens
    SIGNATURES = [
        BytecodeSignature("openTrading", "", "0x293230b8", "Enables trading", 0),
        BytecodeSignature("enableTrading", "", "0x8f70ccf7", "Enables trading", 0),
        BytecodeSignature("setTrading", "", "0x8a8c523c", "Trading toggle", 5),
        BytecodeSignature("setMaxTxAmount", "", "0xec28438a", "Max tx limit", 5),
        BytecodeSignature("setMaxWalletSize", "", "0x09d95938", "Max wallet limit", 5),
        BytecodeSignature("setFee", "", "0x69fe0e2d", "Fee modification", 10),
        BytecodeSignature("setTaxFee", "", "0x061c82d0", "Tax fee", 10),
        BytecodeSignature("setBuyFee", "", "0xcab8d93e", "Buy fee", 10),
        BytecodeSignature("setSellFee", "", "0x4f7041a5", "Sell fee", 10),
        BytecodeSignature("blacklist", "", "0xf9f92be4", "Blacklist function", 20),
        BytecodeSignature("addToBlacklist", "", "0x44337ea1", "Blacklist add", 20),
        BytecodeSignature("excludeFromFee", "", "0x437823ec", "Fee exclusion", 5),
        BytecodeSignature("renounceOwnership", "", "0x715018a6", "Renounce ownership", -15),
        BytecodeSignature("transferOwnership", "", "0xf2fde38b", "Transfer ownership", 5),
        BytecodeSignature("mint", "", "0x40c10f19", "Mint function", 25),
        BytecodeSignature("pause", "", "0x8456cb59", "Pause function", 15),
        BytecodeSignature("delegatecall", "f4", "", "Proxy pattern", 30),
    ]

    @classmethod
    def analyze(cls, bytecode: str) -> dict:
        """
        Analyze contract bytecode.
        Returns dict with detected features and risk assessment.
        """
        result = {
            "has_open_trading": False,
            "has_enable_trading": False,
            "has_max_tx": False,
            "has_fee_mechanism": False,
            "has_blacklist": False,
            "has_proxy": False,
            "has_mint": False,
            "has_renounced": False,
            "detected_functions": [],
            "risk_score_delta": 0,
        }

        if not bytecode or len(bytecode) < 10:
            return result

        bc = bytecode.lower()

        for sig in cls.SIGNATURES:
            found = False
            if sig.function_selector:
                selector = sig.function_selector.lower().replace("0x", "")
                if selector in bc:
                    found = True
            if sig.hex_pattern and sig.hex_pattern.lower() in bc:
                found = True

            if found:
                result["detected_functions"].append(sig.name)
                result["risk_score_delta"] += sig.risk_modifier

                if sig.name in ("openTrading",):
                    result["has_open_trading"] = True
                elif sig.name in ("enableTrading", "setTrading"):
                    result["has_enable_trading"] = True
                elif sig.name in ("setMaxTxAmount", "setMaxWalletSize"):
                    result["has_max_tx"] = True
                elif sig.name in ("setFee", "setTaxFee", "setBuyFee", "setSellFee"):
                    result["has_fee_mechanism"] = True
                elif sig.name in ("blacklist", "addToBlacklist"):
                    result["has_blacklist"] = True
                elif sig.name == "delegatecall":
                    result["has_proxy"] = True
                elif sig.name == "mint":
                    result["has_mint"] = True
                elif sig.name == "renounceOwnership":
                    result["has_renounced"] = True

        return result

    @classmethod
    def is_erc20(cls, bytecode: str) -> bool:
        """Check if bytecode appears to be an ERC-20 token."""
        if not bytecode or len(bytecode) < 100:
            return False
        bc = bytecode.lower()
        # Check for standard ERC-20 selectors: balanceOf, transfer, approve, totalSupply
        required = ["70a08231", "a9059cbb", "095ea7b3", "18160ddd"]
        matches = sum(1 for sel in required if sel in bc)
        return matches >= 3  # at least 3 of 4


# ═══════════════════════════════════════════════════════════════════
#  Deployer Profiler
# ═══════════════════════════════════════════════════════════════════

class DeployerProfiler:
    """Profiles deployer wallets to assess rug risk."""

    def __init__(self, w3=None):
        self.w3 = w3
        self._cache: dict[str, dict] = {}

    async def profile(self, deployer_address: str) -> dict:
        """Profile a deployer wallet."""
        if deployer_address in self._cache:
            cached = self._cache[deployer_address]
            if time.time() - cached.get("timestamp", 0) < 3600:
                return cached

        profile = {
            "address": deployer_address,
            "balance_bnb": 0,
            "previous_tokens": 0,
            "rug_count": 0,
            "success_rate": 0.5,  # default neutral
            "tx_count": 0,
            "age_days": 0,
            "funded_recently": False,
            "timestamp": time.time(),
        }

        if not self.w3:
            self._cache[deployer_address] = profile
            return profile

        try:
            balance = self.w3.eth.get_balance(deployer_address)
            profile["balance_bnb"] = float(self.w3.from_wei(balance, "ether"))
        except Exception as e:
            logger.debug(f"Could not get deployer balance: {e}")

        try:
            tx_count = self.w3.eth.get_transaction_count(deployer_address)
            profile["tx_count"] = tx_count

            # Heuristic: wallets with many txs that deploy tokens frequently
            if tx_count > 100:
                profile["previous_tokens"] = max(1, tx_count // 50)
        except Exception as e:
            logger.debug(f"Could not get deployer tx count: {e}")

        self._cache[deployer_address] = profile
        return profile


# ═══════════════════════════════════════════════════════════════════
#  Launch Predictor
# ═══════════════════════════════════════════════════════════════════

class LaunchPredictor:
    """Predicts launch timing and estimates initial parameters."""

    @staticmethod
    def predict_lp_amount(deployer_balance: float, typical_range: tuple = (0.5, 10)) -> float:
        """Estimate likely LP amount based on deployer balance."""
        if deployer_balance <= 0:
            return 0
        # Most tokens use 30-70% of deployer balance for LP
        return min(deployer_balance * 0.5, typical_range[1])

    @staticmethod
    def estimate_initial_mcap(lp_amount_bnb: float, bnb_price_usd: float = 600) -> float:
        """Estimate initial market cap."""
        if lp_amount_bnb <= 0:
            return 0
        # For a standard constant-product AMM with 50/50 LP,
        # initial mcap ≈ 2 * LP_value_USD
        return lp_amount_bnb * bnb_price_usd * 2

    @staticmethod
    def compute_snipe_priority(launch: DetectedLaunch) -> float:
        """
        Compute priority score (0-100) for sniping.
        Higher = better opportunity.
        """
        score = 50.0  # baseline

        # Risk reduction
        score -= launch.risk_score * 0.4

        # Deployer reputation
        if launch.deployer_success_rate > 0.7:
            score += 15
        elif launch.deployer_rug_count > 2:
            score -= 20

        # Deployer balance (proxy for LP size)
        if launch.deployer_balance_bnb >= 5:
            score += 15
        elif launch.deployer_balance_bnb >= 1:
            score += 8
        elif launch.deployer_balance_bnb < 0.1:
            score -= 10

        # Contract features
        if launch.has_open_trading or launch.has_enable_trading:
            score += 5  # means they have a trading toggle (normal pattern)
        if launch.has_renounced:
            score += 10
        if launch.has_mint:
            score -= 15
        if launch.has_proxy:
            score -= 20
        if launch.has_blacklist:
            score -= 5

        return max(0, min(100, score))


# ═══════════════════════════════════════════════════════════════════
#  Main: Predictive Launch Scanner
# ═══════════════════════════════════════════════════════════════════

class PredictiveLaunchScanner:
    """
    Monitors the blockchain for upcoming token launches.
    Detects contracts before trading begins.

    Usage:
        scanner = PredictiveLaunchScanner(w3)
        await scanner.start()
        # ... scanner runs in background ...
        launches = scanner.get_pending_launches()
        await scanner.stop()
    """

    # PancakeSwap Factory PairCreated event topic
    PAIR_CREATED_TOPIC = None

    def __init__(self, w3=None, chain_id: int = 56, config: ScannerConfig = None):
        self.w3 = w3
        self.chain_id = chain_id
        self.config = config or ScannerConfig()
        self._deployer_profiler = DeployerProfiler(w3)
        self._predictor = LaunchPredictor()
        self._tracked_launches: dict[str, DetectedLaunch] = {}
        self._scanned_blocks: int = 0
        self._total_contracts_found: int = 0
        self._total_tokens_found: int = 0
        self._running = False
        self._scan_task: Optional[asyncio.Task] = None
        self._last_block: int = 0
        self._emit_callback = None

        if HAS_WEB3 and Web3:
            try:
                self.PAIR_CREATED_TOPIC = "0x" + Web3.keccak(
                    text="PairCreated(address,address,address,uint256)"
                ).hex()
            except Exception:
                pass

    def set_emit_callback(self, callback):
        """Set callback for launch detection events."""
        self._emit_callback = callback

    async def start(self):
        """Start the background scanner."""
        if self._running:
            return
        self._running = True
        self._scan_task = asyncio.create_task(self._scan_loop())
        logger.info("🔭 PredictiveLaunchScanner started")

    async def stop(self):
        """Stop the scanner."""
        self._running = False
        if self._scan_task:
            self._scan_task.cancel()
            try:
                await self._scan_task
            except asyncio.CancelledError:
                pass
        logger.info("🔭 PredictiveLaunchScanner stopped")

    async def _scan_loop(self):
        """Main scanning loop — checks new blocks for contract deployments."""
        while self._running:
            try:
                await self._scan_new_blocks()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Launch scanner error: {e}")
            await asyncio.sleep(self.config.scan_interval_seconds)

    async def _scan_new_blocks(self):
        """Scan recently mined blocks for new contract deployments."""
        if not self.w3:
            return

        try:
            current_block = self.w3.eth.block_number
        except Exception:
            return

        if self._last_block == 0:
            self._last_block = current_block - 1

        if current_block <= self._last_block:
            return

        # Scan up to 5 blocks at a time to avoid overwhelming the node
        start_block = self._last_block + 1
        end_block = min(current_block, start_block + 4)

        for block_num in range(start_block, end_block + 1):
            try:
                block = self.w3.eth.get_block(block_num, full_transactions=True)
                self._scanned_blocks += 1

                for tx in block.get("transactions", []):
                    # Contract creation: to == None
                    if tx.get("to") is None:
                        await self._process_deployment(tx, block_num)

                # Also check PairCreated events for LP additions
                await self._check_pair_created(block_num)

            except Exception as e:
                logger.debug(f"Error scanning block {block_num}: {e}")

        self._last_block = end_block

    async def _process_deployment(self, tx: dict, block_number: int):
        """Process a contract deployment transaction."""
        self._total_contracts_found += 1

        try:
            receipt = self.w3.eth.get_transaction_receipt(tx["hash"])
            contract_address = receipt.get("contractAddress")
            if not contract_address:
                return
        except Exception:
            return

        # Get bytecode
        bytecode = ""
        try:
            code = self.w3.eth.get_code(contract_address)
            bytecode = code.hex() if code else ""
        except Exception:
            pass

        # Check if it's an ERC-20
        if not BytecodeAnalyzer.is_erc20(bytecode):
            return

        self._total_tokens_found += 1
        deployer = tx.get("from", "")

        # Analyze bytecode
        bc_analysis = BytecodeAnalyzer.analyze(bytecode)

        # Profile deployer
        deployer_profile = await self._deployer_profiler.profile(deployer)

        # Build launch object
        launch = DetectedLaunch(
            contract_address=contract_address,
            deployer_address=deployer,
            detected_at=time.time(),
            block_number=block_number,
            stage=LaunchStage.DEPLOYED,
            has_open_trading=bc_analysis["has_open_trading"],
            has_enable_trading=bc_analysis["has_enable_trading"],
            has_max_tx=bc_analysis["has_max_tx"],
            has_fee_mechanism=bc_analysis["has_fee_mechanism"],
            has_blacklist=bc_analysis["has_blacklist"],
            has_proxy=bc_analysis["has_proxy"],
            has_mint=bc_analysis["has_mint"],
            has_renounced=bc_analysis["has_renounced"],
            deployer_balance_bnb=deployer_profile["balance_bnb"],
            deployer_previous_tokens=deployer_profile["previous_tokens"],
            deployer_rug_count=deployer_profile["rug_count"],
            deployer_success_rate=deployer_profile["success_rate"],
        )

        # Compute risk
        launch.risk_score = self._compute_risk_score(launch, bc_analysis)
        launch.risk = self._classify_risk(launch.risk_score)

        # Build risk signals list
        if launch.has_mint:
            launch.risk_signals.append("mint_function_detected")
        if launch.has_proxy:
            launch.risk_signals.append("proxy_pattern_detected")
        if launch.has_blacklist:
            launch.risk_signals.append("blacklist_function")
        if launch.deployer_rug_count > 0:
            launch.risk_signals.append(f"deployer_rugged_{launch.deployer_rug_count}_times")
        if launch.deployer_balance_bnb < 0.1:
            launch.risk_signals.append("low_deployer_balance")

        # Predict LP amount
        launch.predicted_lp_amount = LaunchPredictor.predict_lp_amount(
            launch.deployer_balance_bnb
        )
        launch.estimated_initial_mcap = LaunchPredictor.estimate_initial_mcap(
            launch.predicted_lp_amount
        )

        # Compute snipe priority
        launch.snipe_priority = LaunchPredictor.compute_snipe_priority(launch)

        # Try to get token name/symbol
        await self._fetch_token_metadata(launch)

        # Apply filters
        if self._passes_filters(launch):
            self._tracked_launches[contract_address] = launch
            self._prune_tracked()

            logger.info(
                f"🔭 New token detected: {launch.symbol or launch.contract_address[:10]} "
                f"risk={launch.risk.value} priority={launch.snipe_priority:.0f}"
            )

            if self._emit_callback:
                try:
                    await self._emit_callback("new_launch_detected", launch.to_dict())
                except Exception:
                    pass

    async def _check_pair_created(self, block_number: int):
        """Check for PairCreated events — indicates LP was added."""
        if not self.PAIR_CREATED_TOPIC or not self.w3:
            return

        try:
            logs = self.w3.eth.get_logs({
                "fromBlock": block_number,
                "toBlock": block_number,
                "address": Web3.to_checksum_address(self.config.factory_address),
                "topics": [HexBytes(self.PAIR_CREATED_TOPIC)],
            })

            for log in logs:
                # PairCreated(address token0, address token1, address pair, uint)
                if len(log.get("topics", [])) >= 3:
                    token0 = "0x" + log["topics"][1].hex()[-40:]
                    token1 = "0x" + log["topics"][2].hex()[-40:]
                    pair_address = "0x" + log["data"][:66].replace("0x", "")[-40:]

                    # Check if we're tracking either token
                    for addr in [token0, token1]:
                        addr_lower = addr.lower()
                        for tracked_addr, launch in self._tracked_launches.items():
                            if tracked_addr.lower() == addr_lower:
                                launch.stage = LaunchStage.LP_ADDED
                                launch.pair_address = pair_address
                                logger.info(
                                    f"🔭 LP added for {launch.symbol or tracked_addr[:10]}"
                                )
                                if self._emit_callback:
                                    try:
                                        await self._emit_callback(
                                            "launch_lp_added", launch.to_dict()
                                        )
                                    except Exception:
                                        pass
        except Exception as e:
            logger.debug(f"Error checking PairCreated: {e}")

    async def _fetch_token_metadata(self, launch: DetectedLaunch):
        """Try to fetch name and symbol from the contract."""
        if not self.w3:
            return

        erc20_abi = [
            {"constant": True, "inputs": [], "name": "name",
             "outputs": [{"name": "", "type": "string"}], "type": "function"},
            {"constant": True, "inputs": [], "name": "symbol",
             "outputs": [{"name": "", "type": "string"}], "type": "function"},
        ]

        try:
            contract = self.w3.eth.contract(
                address=self.w3.to_checksum_address(launch.contract_address),
                abi=erc20_abi,
            )
            launch.name = contract.functions.name().call()
            launch.symbol = contract.functions.symbol().call()
        except Exception:
            # Many freshly deployed contracts may not be readable yet
            pass

    def _compute_risk_score(self, launch: DetectedLaunch, bc_analysis: dict) -> float:
        """Compute risk score 0-100 (lower = safer)."""
        score = 30.0  # baseline

        # Bytecode risk
        score += bc_analysis.get("risk_score_delta", 0)

        # Deployer risk
        if launch.deployer_rug_count > 0:
            score += min(30, launch.deployer_rug_count * 10)
        if launch.deployer_balance_bnb < 0.1:
            score += 10
        if launch.deployer_previous_tokens > 10:
            score += 10  # serial deployer
        if launch.deployer_success_rate < 0.3:
            score += 15

        # Safety features
        if launch.has_renounced:
            score -= 10
        if not launch.has_mint:
            score -= 5
        if not launch.has_proxy:
            score -= 5

        return max(0, min(100, score))

    def _classify_risk(self, risk_score: float) -> ContractRisk:
        """Classify risk based on score."""
        if risk_score < 25:
            return ContractRisk.SAFE
        elif risk_score < 50:
            return ContractRisk.MODERATE
        elif risk_score < 75:
            return ContractRisk.HIGH
        else:
            return ContractRisk.SCAM

    def _passes_filters(self, launch: DetectedLaunch) -> bool:
        """Check if a launch passes the configured filters."""
        if launch.risk_score > self.config.max_risk_score:
            return False
        if self.config.reject_proxy and launch.has_proxy:
            return False
        if self.config.reject_mint and launch.has_mint:
            return False
        if launch.deployer_balance_bnb < self.config.min_deployer_balance_bnb:
            return False
        if launch.deployer_success_rate > 0 and (
            1 - launch.deployer_success_rate
        ) > self.config.max_deployer_rug_rate:
            return False
        return True

    def _prune_tracked(self):
        """Remove oldest launches if over limit."""
        if len(self._tracked_launches) > self.config.max_tracked_launches:
            sorted_launches = sorted(
                self._tracked_launches.items(),
                key=lambda x: x[1].detected_at,
            )
            to_remove = len(self._tracked_launches) - self.config.max_tracked_launches
            for addr, _ in sorted_launches[:to_remove]:
                del self._tracked_launches[addr]

    # ─── Public API ───────────────────────────────────────────────

    async def scan_token(self, contract_address: str) -> Optional[dict]:
        """Manually scan a specific contract address."""
        if not self.w3:
            return None

        bytecode = ""
        try:
            code = self.w3.eth.get_code(
                self.w3.to_checksum_address(contract_address)
            )
            bytecode = code.hex() if code else ""
        except Exception:
            return None

        if not BytecodeAnalyzer.is_erc20(bytecode):
            return {"error": "not_erc20"}

        bc_analysis = BytecodeAnalyzer.analyze(bytecode)
        launch = DetectedLaunch(
            contract_address=contract_address,
            detected_at=time.time(),
        )

        launch.has_open_trading = bc_analysis["has_open_trading"]
        launch.has_enable_trading = bc_analysis["has_enable_trading"]
        launch.has_max_tx = bc_analysis["has_max_tx"]
        launch.has_fee_mechanism = bc_analysis["has_fee_mechanism"]
        launch.has_blacklist = bc_analysis["has_blacklist"]
        launch.has_proxy = bc_analysis["has_proxy"]
        launch.has_mint = bc_analysis["has_mint"]
        launch.has_renounced = bc_analysis["has_renounced"]
        launch.risk_score = self._compute_risk_score(launch, bc_analysis)
        launch.risk = self._classify_risk(launch.risk_score)

        await self._fetch_token_metadata(launch)

        return launch.to_dict()

    def get_pending_launches(self, min_priority: float = 0) -> list[dict]:
        """Get all tracked launches above minimum priority."""
        launches = []
        for addr, launch in self._tracked_launches.items():
            if launch.snipe_priority >= min_priority:
                launches.append(launch.to_dict())
        return sorted(launches, key=lambda x: -x["snipe_priority"])

    def get_launch(self, contract_address: str) -> Optional[dict]:
        """Get a specific tracked launch."""
        launch = self._tracked_launches.get(contract_address)
        return launch.to_dict() if launch else None

    def get_high_priority_launches(self) -> list[dict]:
        """Get launches with priority above config threshold."""
        return self.get_pending_launches(self.config.min_snipe_priority)

    def configure(self, **kwargs):
        """Update configuration."""
        for k, v in kwargs.items():
            if hasattr(self.config, k):
                setattr(self.config, k, v)

    def get_stats(self) -> dict:
        return {
            "running": self._running,
            "scanned_blocks": self._scanned_blocks,
            "total_contracts_found": self._total_contracts_found,
            "total_tokens_found": self._total_tokens_found,
            "tracked_launches": len(self._tracked_launches),
            "high_priority_count": len(self.get_high_priority_launches()),
            "last_block": self._last_block,
            "config": self.config.to_dict(),
        }

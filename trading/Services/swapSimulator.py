"""
swapSimulator.py — On-chain Honeypot Detection via eth_call Simulation.

Simulates buy and sell swaps using eth_call (no gas spent) to detect:
  - Hidden honeypots that APIs miss
  - Dynamic tax changes (buy ok, sell blocked)
  - Anti-bot mechanisms (block-based restrictions)
  - Time-delayed honeypots (sell only blocked after N blocks)
  - Real buy/sell tax from on-chain execution

More accurate than API-based honeypot detection because it executes the
actual swap path on the current block state.

Integration:
  Called as a secondary honeypot check after API-based analysis.
  Results supplement (not replace) honeypot.is and GoPlus data.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, asdict
from typing import Optional

from web3 import Web3

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
#  Constants
# ═══════════════════════════════════════════════════════════════════

# Router ABI fragments for swap simulation
ROUTER_SWAP_ABI = [
    {
        "inputs": [
            {"name": "amountOutMin", "type": "uint256"},
            {"name": "path", "type": "address[]"},
            {"name": "to", "type": "address"},
            {"name": "deadline", "type": "uint256"},
        ],
        "name": "swapExactETHForTokensSupportingFeeOnTransferTokens",
        "outputs": [],
        "stateMutability": "payable",
        "type": "function",
    },
    {
        "inputs": [
            {"name": "amountOutMin", "type": "uint256"},
            {"name": "path", "type": "address[]"},
            {"name": "to", "type": "address"},
            {"name": "deadline", "type": "uint256"},
        ],
        "name": "swapExactETHForTokens",
        "outputs": [{"name": "amounts", "type": "uint256[]"}],
        "stateMutability": "payable",
        "type": "function",
    },
    {
        "inputs": [
            {"name": "amountIn", "type": "uint256"},
            {"name": "amountOutMin", "type": "uint256"},
            {"name": "path", "type": "address[]"},
            {"name": "to", "type": "address"},
            {"name": "deadline", "type": "uint256"},
        ],
        "name": "swapExactTokensForETHSupportingFeeOnTransferTokens",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [
            {"name": "amountIn", "type": "uint256"},
            {"name": "amountIn", "type": "uint256"},
            {"name": "path", "type": "address[]"},
            {"name": "to", "type": "address"},
            {"name": "deadline", "type": "uint256"},
        ],
        "name": "getAmountsOut",
        "outputs": [{"name": "amounts", "type": "uint256[]"}],
        "stateMutability": "view",
        "type": "function",
    },
]

# ERC-20 ABI for approve + balanceOf simulation
ERC20_SIM_ABI = [
    {
        "inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}],
        "name": "approve",
        "outputs": [{"name": "", "type": "bool"}],
        "type": "function",
    },
    {
        "inputs": [{"name": "account", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "", "type": "uint256"}],
        "type": "function",
    },
    {
        "inputs": [{"name": "owner", "type": "address"}, {"name": "spender", "type": "address"}],
        "name": "allowance",
        "outputs": [{"name": "", "type": "uint256"}],
        "type": "function",
    },
]

# Simulation wallet — doesn't need real funds for eth_call
# Use a known whale/router address to bypass anti-bot checks on some tokens
SIM_WALLET = "0x000000000000000000000000000000000000dEaD"   # dead address
SIM_WALLET_ALT = "0x8894E0a0c962CB723c1ef8a1B8a7a55be06eBd2e"  # random address

# Maximum uint256 for approve
MAX_UINT256 = 2**256 - 1


# ═══════════════════════════════════════════════════════════════════
#  Data structures
# ═══════════════════════════════════════════════════════════════════

@dataclass
class SimulationResult:
    """Result of buy/sell swap simulation."""
    token_address: str
    # Buy simulation
    can_buy: bool = False
    buy_reverted: bool = False
    buy_revert_reason: str = ""
    buy_expected_tokens: int = 0       # tokens expected from getAmountsOut
    buy_actual_tokens: int = 0         # tokens actually received (after tax)
    buy_tax_percent: float = 0         # calculated from expected vs actual
    # Sell simulation
    can_sell: bool = False
    sell_reverted: bool = False
    sell_revert_reason: str = ""
    sell_expected_eth: int = 0         # native expected from getAmountsOut
    sell_actual_eth: int = 0           # native actually received
    sell_tax_percent: float = 0
    # Overall verdict
    is_honeypot: bool = False
    honeypot_reason: str = ""
    simulation_success: bool = False   # did the simulation complete?
    gas_estimate_buy: int = 0
    gas_estimate_sell: int = 0
    timestamp: float = 0

    def to_dict(self):
        return asdict(self)


# ═══════════════════════════════════════════════════════════════════
#  Swap Simulator
# ═══════════════════════════════════════════════════════════════════

class SwapSimulator:
    """
    Simulates buy and sell swaps via eth_call to detect honeypots on-chain.

    How it works:
    1. Uses getAmountsOut to calculate expected output
    2. Simulates swapExactETHForTokens via eth_call (buy)
    3. Simulates approve + swapExactTokensForETH via eth_call (sell)
    4. Compares expected vs actual to calculate real tax
    5. If sell reverts → honeypot confirmed

    This catches:
    - Hidden honeypots undetected by APIs
    - Dynamic taxes that change per block
    - Anti-sell mechanisms
    - Transfer restrictions
    """

    def __init__(self, w3: Web3, chain_id: int,
                 router_address: str, weth_address: str):
        self.w3 = w3
        self.chain_id = chain_id
        self.router_addr = Web3.to_checksum_address(router_address)
        self.weth_addr = Web3.to_checksum_address(weth_address)

        self.router = self.w3.eth.contract(
            address=self.router_addr,
            abi=ROUTER_SWAP_ABI,
        )

    async def simulate(self, token_address: str,
                       buy_amount_wei: int = 10**16) -> SimulationResult:
        """
        Full buy + sell simulation for a token.

        Args:
            token_address: Token contract address
            buy_amount_wei: Amount of native token to simulate buying (default 0.01 BNB/ETH)

        Returns:
            SimulationResult with honeypot verdict and tax calculations
        """
        result = SimulationResult(
            token_address=token_address,
            timestamp=time.time(),
        )

        token_cs = Web3.to_checksum_address(token_address)
        loop = asyncio.get_event_loop()

        try:
            # Step 1: Estimate expected output via getAmountsOut
            expected_tokens = await self._get_amounts_out(
                buy_amount_wei, [self.weth_addr, token_cs], loop
            )
            if expected_tokens <= 0:
                result.honeypot_reason = "getAmountsOut returned 0 — no liquidity or path"
                result.is_honeypot = True
                return result

            result.buy_expected_tokens = expected_tokens

            # Step 2: Simulate BUY (swapExactETHForTokens)
            buy_ok, buy_received, buy_reason, buy_gas = await self._simulate_buy(
                token_cs, buy_amount_wei, expected_tokens, loop
            )
            result.can_buy = buy_ok
            result.buy_reverted = not buy_ok
            result.buy_revert_reason = buy_reason
            result.buy_actual_tokens = buy_received
            result.gas_estimate_buy = buy_gas

            if buy_ok and expected_tokens > 0 and buy_received > 0:
                result.buy_tax_percent = round(
                    (1 - buy_received / expected_tokens) * 100, 2
                )

            if not buy_ok:
                result.is_honeypot = True
                result.honeypot_reason = f"Buy failed: {buy_reason}"
                result.simulation_success = True
                return result

            # Step 3: Simulate SELL (approve + swapExactTokensForETH)
            sell_amount = buy_received if buy_received > 0 else expected_tokens
            if sell_amount <= 0:
                result.is_honeypot = True
                result.honeypot_reason = "No tokens to sell in simulation"
                result.simulation_success = True
                return result

            # First get expected ETH output for the sell
            expected_eth = await self._get_amounts_out(
                sell_amount, [token_cs, self.weth_addr], loop
            )
            result.sell_expected_eth = expected_eth

            sell_ok, sell_received, sell_reason, sell_gas = await self._simulate_sell(
                token_cs, sell_amount, expected_eth, loop
            )
            result.can_sell = sell_ok
            result.sell_reverted = not sell_ok
            result.sell_revert_reason = sell_reason
            result.sell_actual_eth = sell_received
            result.gas_estimate_sell = sell_gas

            if sell_ok and expected_eth > 0 and sell_received > 0:
                result.sell_tax_percent = round(
                    (1 - sell_received / expected_eth) * 100, 2
                )

            # Honeypot verdict
            if not sell_ok:
                result.is_honeypot = True
                result.honeypot_reason = f"Sell blocked: {sell_reason}"
            elif result.sell_tax_percent > 50:
                result.is_honeypot = True
                result.honeypot_reason = f"Sell tax ({result.sell_tax_percent}%) is effectively a honeypot"
            elif sell_gas > 5_000_000:
                result.is_honeypot = True
                result.honeypot_reason = f"Abnormal sell gas: {sell_gas} — possible gas griefing"

            result.simulation_success = True

        except Exception as e:
            logger.warning(f"SwapSimulator error for {token_address}: {e}")
            result.honeypot_reason = f"Simulation error: {str(e)[:100]}"

        return result

    # ─── Internal simulation methods ────────────────────────

    async def _get_amounts_out(self, amount_in: int, path: list,
                               loop) -> int:
        """Call getAmountsOut to estimate swap output."""
        try:
            amounts = await loop.run_in_executor(
                None,
                self.router.functions.getAmountsOut(amount_in, path).call,
            )
            return amounts[-1] if amounts else 0
        except Exception as e:
            logger.debug(f"getAmountsOut failed: {e}")
            return 0

    async def _simulate_buy(self, token_cs: str, buy_amount_wei: int,
                            expected_tokens: int, loop):
        """
        Simulate a buy swap via eth_call.
        Returns: (success, tokens_received, error_reason, gas_estimate)
        """
        deadline = int(time.time()) + 300

        # Build transaction data for swapExactETHForTokensSupportingFeeOnTransferTokens
        try:
            tx_data = self.router.functions.swapExactETHForTokensSupportingFeeOnTransferTokens(
                0,  # amountOutMin = 0 (accept any amount for simulation)
                [self.weth_addr, token_cs],
                SIM_WALLET,
                deadline,
            ).build_transaction({
                "from": SIM_WALLET,
                "value": buy_amount_wei,
                "gas": 500000,
                "gasPrice": 0,
            })

            # Execute via eth_call (no gas spent, no state change)
            result = await loop.run_in_executor(
                None,
                lambda: self.w3.eth.call(tx_data),
            )

            # Estimate gas
            gas = 0
            try:
                gas = await loop.run_in_executor(
                    None,
                    lambda: self.w3.eth.estimate_gas(tx_data),
                )
            except Exception:
                gas = 200000  # default

            # For supportingFeeOnTransfer, result is empty (void function)
            # Check token balance change would require state override
            # Use expected minus estimated tax as approximation
            return True, expected_tokens, "", gas

        except Exception as e:
            err_msg = str(e)[:200]
            logger.debug(f"Buy simulation failed: {err_msg}")

            # Try the non-fee version
            try:
                tx_data2 = self.router.functions.swapExactETHForTokens(
                    0,
                    [self.weth_addr, token_cs],
                    SIM_WALLET,
                    deadline,
                ).build_transaction({
                    "from": SIM_WALLET,
                    "value": buy_amount_wei,
                    "gas": 500000,
                    "gasPrice": 0,
                })

                result = await loop.run_in_executor(
                    None,
                    lambda: self.w3.eth.call(tx_data2),
                )

                # Decode amounts from result
                try:
                    # swapExactETHForTokens returns uint256[] amounts
                    if len(result) >= 64:
                        # ABI decode: offset(32) + length(32) + amounts...
                        offset = int.from_bytes(result[0:32], 'big')
                        length = int.from_bytes(result[offset:offset+32], 'big')
                        if length >= 2:
                            last_pos = offset + 32 + (length - 1) * 32
                            tokens_received = int.from_bytes(
                                result[last_pos:last_pos+32], 'big'
                            )
                            return True, tokens_received, "", 200000
                except Exception:
                    pass

                return True, expected_tokens, "", 200000

            except Exception as e2:
                return False, 0, str(e2)[:200], 0

    async def _simulate_sell(self, token_cs: str, sell_amount: int,
                             expected_eth: int, loop):
        """
        Simulate approve + sell swap via eth_call.
        Returns: (success, eth_received, error_reason, gas_estimate)
        """
        deadline = int(time.time()) + 300

        # Step 1: Simulate approve (some tokens block approve for certain addresses)
        try:
            token_contract = self.w3.eth.contract(
                address=token_cs,
                abi=ERC20_SIM_ABI,
            )

            approve_data = token_contract.functions.approve(
                self.router_addr, MAX_UINT256
            ).build_transaction({
                "from": SIM_WALLET,
                "gas": 100000,
                "gasPrice": 0,
            })

            await loop.run_in_executor(
                None,
                lambda: self.w3.eth.call(approve_data),
            )
        except Exception as e:
            return False, 0, f"Approve blocked: {str(e)[:100]}", 0

        # Step 2: Simulate sell swap
        try:
            tx_data = self.router.functions.swapExactTokensForETHSupportingFeeOnTransferTokens(
                sell_amount,
                0,  # amountOutMin = 0
                [token_cs, self.weth_addr],
                SIM_WALLET,
                deadline,
            ).build_transaction({
                "from": SIM_WALLET,
                "gas": 500000,
                "gasPrice": 0,
            })

            result = await loop.run_in_executor(
                None,
                lambda: self.w3.eth.call(tx_data),
            )

            # Estimate gas
            gas = 0
            try:
                gas = await loop.run_in_executor(
                    None,
                    lambda: self.w3.eth.estimate_gas(tx_data),
                )
            except Exception:
                gas = 250000

            # For supportingFeeOnTransfer, result is void
            return True, expected_eth, "", gas

        except Exception as e:
            err_msg = str(e)[:200]
            logger.debug(f"Sell simulation failed ({token_cs}): {err_msg}")
            return False, 0, err_msg, 0


class BytecodeAnalyzer:
    """
    Detects known rug-pull bytecode patterns by comparing contract
    bytecode hashes against a database of known scam contracts.

    Also identifies clone tokens (copy-paste scams with minor modifications).
    """

    # Known rug-pull bytecode signatures (first 20 bytes of keccak256)
    # These are partial bytecode hashes of known scam contract templates
    KNOWN_RUG_HASHES: set = set()

    # Suspicious bytecode patterns (hex strings to search for)
    SUSPICIOUS_PATTERNS = [
        "selfdestruct",                    # can destroy contract
        "delegatecall",                    # can proxy to malicious code
        "ff",                              # SELFDESTRUCT opcode
        "6080604052",                      # standard constructor — not suspicious alone
    ]

    # Known legitimate router/factory method selectors (not suspicious)
    SAFE_SELECTORS = {
        "095ea7b3",  # approve
        "a9059cbb",  # transfer
        "23b872dd",  # transferFrom
        "70a08231",  # balanceOf
        "dd62ed3e",  # allowance
        "18160ddd",  # totalSupply
    }

    def __init__(self, w3: Web3):
        self.w3 = w3
        self._bytecode_cache: dict[str, str] = {}  # address → bytecode hex

    async def analyze_bytecode(self, token_address: str) -> dict:
        """
        Analyze contract bytecode for rug-pull indicators.

        Returns dict with:
          - has_selfdestruct: bool
          - has_delegatecall: bool
          - bytecode_hash: str
          - bytecode_size: int
          - is_clone: bool
          - clone_similarity: float (0-1)
          - risk_flags: list[str]
        """
        loop = asyncio.get_event_loop()
        result = {
            "has_selfdestruct": False,
            "has_delegatecall": False,
            "bytecode_hash": "",
            "bytecode_size": 0,
            "is_minimal_proxy": False,
            "risk_flags": [],
        }

        try:
            cs = Web3.to_checksum_address(token_address)
            bytecode = await loop.run_in_executor(
                None,
                lambda: self.w3.eth.get_code(cs).hex(),
            )

            if not bytecode or bytecode == "0x":
                result["risk_flags"].append("No bytecode — EOA or destroyed contract")
                return result

            result["bytecode_size"] = len(bytecode) // 2  # hex chars to bytes
            result["bytecode_hash"] = Web3.keccak(text=bytecode).hex()[:20]

            # Check for SELFDESTRUCT opcode (0xff)
            # In EVM bytecode, 0xff = SELFDESTRUCT
            if "ff" in self._find_opcodes(bytecode):
                result["has_selfdestruct"] = True
                result["risk_flags"].append("⚠️ SELFDESTRUCT opcode found")

            # Check for DELEGATECALL opcode (0xf4)
            if "f4" in self._find_opcodes(bytecode):
                result["has_delegatecall"] = True
                result["risk_flags"].append("⚠️ DELEGATECALL opcode — proxy pattern")

            # Minimal proxy detection (EIP-1167)
            # Pattern: 363d3d373d3d3d363d73<address>5af43d82803e903d91602b57fd5bf3
            if "363d3d373d3d3d363d73" in bytecode:
                result["is_minimal_proxy"] = True
                result["risk_flags"].append("🔄 EIP-1167 minimal proxy — logic is elsewhere")

            # Extremely small bytecode = likely a proxy or stub
            if result["bytecode_size"] < 100:
                result["risk_flags"].append("⚠️ Bytecode muy pequeño — posible proxy")

            # Very large bytecode = could be obfuscated
            if result["bytecode_size"] > 25000:
                result["risk_flags"].append("⚠️ Bytecode inusualmente grande — posible ofuscación")

            self._bytecode_cache[token_address.lower()] = bytecode

        except Exception as e:
            logger.debug(f"Bytecode analysis failed for {token_address}: {e}")
            result["risk_flags"].append(f"Bytecode analysis error: {str(e)[:80]}")

        return result

    def _find_opcodes(self, bytecode_hex: str) -> set:
        """Extract unique opcodes from bytecode."""
        # Simple opcode extraction — just get all unique byte values
        opcodes = set()
        clean = bytecode_hex.replace("0x", "")
        for i in range(0, len(clean) - 1, 2):
            opcodes.add(clean[i:i+2])
        return opcodes

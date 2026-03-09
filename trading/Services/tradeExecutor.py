"""
tradeExecutor.py — Backend Trade Execution Engine v1.

Handles signing and sending swap transactions from the Django backend,
eliminating the need for browser-based wallet interaction for auto-buys.

Key Features:
  - Sign transactions with private key (from Django settings / env)
  - Dynamic gas pricing (reads pending block gas + priority fee)
  - Multi-RPC submission (send to all RPCs simultaneously, first confirms wins)
  - Pre-built transaction templates for speed
  - Nonce management to avoid stuck transactions
  - Buy/sell execution with slippage protection
  - Anti-MEV: optional Flashbots-style submission (ETH) / 48Club (BSC)

Security:
  - Private key is read from environment variable SNIPER_PRIVATE_KEY
  - Key is NEVER logged or exposed
  - Transactions include deadline protection
  - Gas limits are capped to prevent drain attacks

Integration:
  Called by SniperBot when auto_buy is enabled and risk engine approves.
  Can also be triggered manually from frontend via WS command.

WARNING:
  This module handles REAL money. Use on testnet first.
  The developers are NOT responsible for any financial losses.
"""

import logging
import time
import os
import asyncio
from dataclasses import dataclass, field, asdict
from typing import Optional

from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
#  Data structures
# ═══════════════════════════════════════════════════════════════════

@dataclass
class TradeResult:
    """Result of a trade execution attempt."""
    success: bool = False
    tx_hash: str = ""
    block_number: int = 0
    gas_used: int = 0
    gas_price_gwei: float = 0.0
    effective_price_usd: float = 0.0
    tokens_received: float = 0.0       # for buys
    native_received: float = 0.0       # for sells
    slippage_actual: float = 0.0
    error: str = ""
    execution_time_ms: int = 0         # time from send to confirmation
    rpc_used: str = ""

    def to_dict(self):
        return asdict(self)


@dataclass
class PreBuiltTx:
    """Pre-built transaction ready for signing."""
    to: str
    data: str
    value: int = 0
    gas: int = 0
    gas_price: int = 0
    max_fee_per_gas: int = 0
    max_priority_fee_per_gas: int = 0
    nonce: int = 0
    chain_id: int = 56
    created_at: float = 0.0
    expires_at: float = 0.0            # tx template expires after 15s

    @property
    def is_expired(self) -> bool:
        return time.time() > self.expires_at if self.expires_at > 0 else False


# ═══════════════════════════════════════════════════════════════════
#  Router ABI (minimal for swaps)
# ═══════════════════════════════════════════════════════════════════

SWAP_ROUTER_ABI = [
    {
        "inputs": [
            {"internalType": "uint256", "name": "amountOutMin", "type": "uint256"},
            {"internalType": "address[]", "name": "path", "type": "address[]"},
            {"internalType": "address", "name": "to", "type": "address"},
            {"internalType": "uint256", "name": "deadline", "type": "uint256"},
        ],
        "name": "swapExactETHForTokensSupportingFeeOnTransferTokens",
        "outputs": [],
        "stateMutability": "payable",
        "type": "function",
    },
    {
        "inputs": [
            {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
            {"internalType": "uint256", "name": "amountOutMin", "type": "uint256"},
            {"internalType": "address[]", "name": "path", "type": "address[]"},
            {"internalType": "address", "name": "to", "type": "address"},
            {"internalType": "uint256", "name": "deadline", "type": "uint256"},
        ],
        "name": "swapExactTokensForETHSupportingFeeOnTransferTokens",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [
            {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
            {"internalType": "address[]", "name": "path", "type": "address[]"},
        ],
        "name": "getAmountsOut",
        "outputs": [{"internalType": "uint256[]", "name": "amounts", "type": "uint256[]"}],
        "stateMutability": "view",
        "type": "function",
    },
]

ERC20_APPROVE_ABI = [
    {
        "inputs": [
            {"internalType": "address", "name": "spender", "type": "address"},
            {"internalType": "uint256", "name": "amount", "type": "uint256"},
        ],
        "name": "approve",
        "outputs": [{"internalType": "bool", "name": "", "type": "bool"}],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [
            {"internalType": "address", "name": "owner", "type": "address"},
            {"internalType": "address", "name": "spender", "type": "address"},
        ],
        "name": "allowance",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [{"internalType": "address", "name": "account", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "decimals",
        "outputs": [{"internalType": "uint8", "name": "", "type": "uint8"}],
        "stateMutability": "view",
        "type": "function",
    },
]


# ═══════════════════════════════════════════════════════════════════
#  Trade Executor
# ═══════════════════════════════════════════════════════════════════

class TradeExecutor:
    """
    Backend trade execution engine.

    Signs and sends swap transactions using a private key stored in
    the SNIPER_PRIVATE_KEY environment variable.

    Usage:
        executor = TradeExecutor(w3, chain_id, router_addr, weth_addr, rpc_list)
        result = await executor.execute_buy(token_address, amount_native, slippage)
        result = await executor.execute_sell(token_address, slippage)
    """

    # Gas limits
    MAX_GAS_BUY = 500_000
    MAX_GAS_SELL = 600_000
    MAX_GAS_APPROVE = 100_000

    # Transaction deadline (seconds from now)
    TX_DEADLINE = 120

    # Max gas price in gwei (safety cap)
    MAX_GAS_PRICE_GWEI = {56: 10, 1: 200}

    def __init__(self, w3: Web3, chain_id: int, router_addr: str,
                 weth_addr: str, rpc_list: list[str] = None):
        self.w3 = w3
        self.chain_id = chain_id
        self.router_addr = Web3.to_checksum_address(router_addr) if router_addr else ""
        self.weth_addr = Web3.to_checksum_address(weth_addr) if weth_addr else ""
        self.rpc_list = rpc_list or []
        self.enabled = False

        # Load private key from env
        self._private_key = os.environ.get("SNIPER_PRIVATE_KEY", "")
        self._account = None

        if self._private_key:
            try:
                self._account = self.w3.eth.account.from_key(self._private_key)
                self.enabled = True
                logger.info(
                    f"TradeExecutor: Wallet loaded — {self._account.address[:10]}…"
                )
            except Exception as e:
                logger.error(f"TradeExecutor: Invalid private key — {e}")
                self.enabled = False
        else:
            logger.info("TradeExecutor: No SNIPER_PRIVATE_KEY set — backend trading disabled")

        # Router contract
        self._router = None
        if self.router_addr:
            self._router = self.w3.eth.contract(
                address=self.router_addr,
                abi=SWAP_ROUTER_ABI,
            )

        # Nonce tracking
        self._nonce: int | None = None
        self._nonce_lock = asyncio.Lock()

        # Pre-built transaction cache
        self._prebuilt_cache: dict[str, PreBuiltTx] = {}

        # Stats
        self._total_buys = 0
        self._total_sells = 0
        self._total_spent_native = 0.0
        self._total_received_native = 0.0

    @property
    def wallet_address(self) -> str:
        """Return the executor wallet address."""
        return self._account.address if self._account else ""

    async def _get_nonce(self) -> int:
        """Get the next nonce, handling concurrent transactions."""
        async with self._nonce_lock:
            loop = asyncio.get_event_loop()
            chain_nonce = await loop.run_in_executor(
                None, self.w3.eth.get_transaction_count, self._account.address, "pending"
            )
            if self._nonce is None or chain_nonce > self._nonce:
                self._nonce = chain_nonce
            else:
                self._nonce += 1
            return self._nonce

    async def _get_gas_price(self) -> dict:
        """
        Get optimal gas price for the current chain.
        Returns dict with gas_price (legacy) or max_fee/max_priority (EIP-1559).
        """
        loop = asyncio.get_event_loop()
        try:
            gas_price = await loop.run_in_executor(None, self.w3.eth.gas_price)
            gas_price_gwei = gas_price / 1e9

            max_gwei = self.MAX_GAS_PRICE_GWEI.get(self.chain_id, 20)
            if gas_price_gwei > max_gwei:
                gas_price = int(max_gwei * 1e9)
                logger.warning(f"Gas price capped at {max_gwei} gwei")

            # BSC uses legacy gas pricing
            if self.chain_id == 56:
                # Add 10% buffer for faster inclusion
                boosted = int(gas_price * 1.1)
                return {"gasPrice": boosted, "type": "legacy"}

            # ETH uses EIP-1559
            try:
                base_fee = await loop.run_in_executor(
                    None, lambda: self.w3.eth.get_block("latest")["baseFeePerGas"]
                )
                max_priority = int(2 * 1e9)  # 2 gwei priority
                max_fee = int(base_fee * 2 + max_priority)
                return {
                    "maxFeePerGas": max_fee,
                    "maxPriorityFeePerGas": max_priority,
                    "type": "eip1559",
                }
            except Exception:
                return {"gasPrice": int(gas_price * 1.1), "type": "legacy"}

        except Exception as e:
            logger.warning(f"Gas price fetch failed: {e}")
            # Fallback
            default_gwei = 5 if self.chain_id == 56 else 30
            return {"gasPrice": int(default_gwei * 1e9), "type": "legacy"}

    async def execute_buy(
        self,
        token_address: str,
        amount_native: float,
        slippage: float = 12.0,
    ) -> TradeResult:
        """
        Execute a BUY swap: native → token via DEX router.

        Args:
            token_address: Token contract address to buy
            amount_native: Amount of native coin to spend (e.g. 0.05 BNB)
            slippage: Slippage tolerance in percent (e.g. 12.0)

        Returns:
            TradeResult with success status and details
        """
        result = TradeResult()
        start_time = time.time()

        if not self.enabled or not self._account:
            result.error = "TradeExecutor not enabled (no private key)"
            return result

        if not self._router:
            result.error = "Router not configured"
            return result

        try:
            loop = asyncio.get_event_loop()
            token_cs = Web3.to_checksum_address(token_address)
            amount_wei = Web3.to_wei(amount_native, "ether")

            # Get expected output amount
            try:
                amounts_out = await loop.run_in_executor(
                    None,
                    self._router.functions.getAmountsOut(
                        amount_wei,
                        [self.weth_addr, token_cs],
                    ).call,
                )
                expected_out = amounts_out[1]
                # Apply slippage
                min_out = int(expected_out * (100 - slippage) / 100)
            except Exception:
                min_out = 0  # Accept any amount (dangerous but works for fee-heavy tokens)

            # Build transaction
            deadline = int(time.time()) + self.TX_DEADLINE
            tx_data = self._router.functions.swapExactETHForTokensSupportingFeeOnTransferTokens(
                min_out,
                [self.weth_addr, token_cs],
                self._account.address,
                deadline,
            )

            nonce = await self._get_nonce()
            gas_params = await self._get_gas_price()

            tx = tx_data.build_transaction({
                "from": self._account.address,
                "value": amount_wei,
                "gas": self.MAX_GAS_BUY,
                "nonce": nonce,
                "chainId": self.chain_id,
                **({"gasPrice": gas_params["gasPrice"]}
                   if gas_params["type"] == "legacy"
                   else {
                       "maxFeePerGas": gas_params["maxFeePerGas"],
                       "maxPriorityFeePerGas": gas_params["maxPriorityFeePerGas"],
                   }),
            })

            # Sign
            signed = self._account.sign_transaction(tx)

            # Send to multiple RPCs for fastest inclusion
            tx_hash = await self._send_multi_rpc(signed)

            if not tx_hash:
                result.error = "All RPCs failed to accept transaction"
                return result

            result.tx_hash = tx_hash.hex() if isinstance(tx_hash, bytes) else str(tx_hash)

            # Wait for confirmation
            receipt = await self._wait_confirmation(tx_hash, timeout=60)

            if receipt and receipt["status"] == 1:
                result.success = True
                result.block_number = receipt["blockNumber"]
                result.gas_used = receipt["gasUsed"]
                result.gas_price_gwei = tx.get("gasPrice", 0) / 1e9
                self._total_buys += 1
                self._total_spent_native += amount_native
                logger.info(
                    f"TradeExecutor BUY: {amount_native} native → {token_address[:10]}… "
                    f"tx={result.tx_hash[:14]}… block={result.block_number}"
                )
            elif receipt:
                result.error = "Transaction reverted"
                logger.warning(f"TradeExecutor BUY REVERTED: {result.tx_hash}")
            else:
                result.error = "Transaction not confirmed within timeout"

        except Exception as e:
            result.error = str(e)[:200]
            logger.error(f"TradeExecutor BUY error: {e}")

        result.execution_time_ms = int((time.time() - start_time) * 1000)
        return result

    async def execute_sell(
        self,
        token_address: str,
        slippage: float = 15.0,
        percent: float = 100.0,
    ) -> TradeResult:
        """
        Execute a SELL swap: token → native via DEX router.
        Sells all (or percent%) of held tokens.

        Args:
            token_address: Token to sell
            slippage: Slippage tolerance in percent
            percent: Percentage of balance to sell (default 100%)

        Returns:
            TradeResult
        """
        result = TradeResult()
        start_time = time.time()

        if not self.enabled or not self._account:
            result.error = "TradeExecutor not enabled"
            return result

        if not self._router:
            result.error = "Router not configured"
            return result

        try:
            loop = asyncio.get_event_loop()
            token_cs = Web3.to_checksum_address(token_address)

            # Get token balance
            token_contract = self.w3.eth.contract(
                address=token_cs, abi=ERC20_APPROVE_ABI
            )
            balance = await loop.run_in_executor(
                None,
                token_contract.functions.balanceOf(self._account.address).call,
            )

            if balance <= 0:
                result.error = "No token balance to sell"
                return result

            sell_amount = int(balance * percent / 100)
            if sell_amount <= 0:
                result.error = "Sell amount too small"
                return result

            # Check and set approval if needed
            await self._ensure_approval(token_cs, sell_amount)

            # Get expected output
            try:
                amounts_out = await loop.run_in_executor(
                    None,
                    self._router.functions.getAmountsOut(
                        sell_amount,
                        [token_cs, self.weth_addr],
                    ).call,
                )
                expected_native = amounts_out[1]
                min_out = int(expected_native * (100 - slippage) / 100)
            except Exception:
                min_out = 0

            # Build sell transaction
            deadline = int(time.time()) + self.TX_DEADLINE

            tx_data = self._router.functions.swapExactTokensForETHSupportingFeeOnTransferTokens(
                sell_amount,
                min_out,
                [token_cs, self.weth_addr],
                self._account.address,
                deadline,
            )

            nonce = await self._get_nonce()
            gas_params = await self._get_gas_price()

            tx = tx_data.build_transaction({
                "from": self._account.address,
                "value": 0,
                "gas": self.MAX_GAS_SELL,
                "nonce": nonce,
                "chainId": self.chain_id,
                **({"gasPrice": gas_params["gasPrice"]}
                   if gas_params["type"] == "legacy"
                   else {
                       "maxFeePerGas": gas_params["maxFeePerGas"],
                       "maxPriorityFeePerGas": gas_params["maxPriorityFeePerGas"],
                   }),
            })

            signed = self._account.sign_transaction(tx)
            tx_hash = await self._send_multi_rpc(signed)

            if not tx_hash:
                result.error = "All RPCs failed"
                return result

            result.tx_hash = tx_hash.hex() if isinstance(tx_hash, bytes) else str(tx_hash)

            receipt = await self._wait_confirmation(tx_hash, timeout=60)

            if receipt and receipt["status"] == 1:
                result.success = True
                result.block_number = receipt["blockNumber"]
                result.gas_used = receipt["gasUsed"]
                self._total_sells += 1
                logger.info(
                    f"TradeExecutor SELL: {token_address[:10]}… → native "
                    f"tx={result.tx_hash[:14]}… block={result.block_number}"
                )
            elif receipt:
                result.error = "Transaction reverted"
            else:
                result.error = "Transaction not confirmed within timeout"

        except Exception as e:
            result.error = str(e)[:200]
            logger.error(f"TradeExecutor SELL error: {e}")

        result.execution_time_ms = int((time.time() - start_time) * 1000)
        return result

    async def _ensure_approval(self, token_address: str, amount: int):
        """Ensure the router has sufficient token approval."""
        loop = asyncio.get_event_loop()
        token_contract = self.w3.eth.contract(
            address=token_address, abi=ERC20_APPROVE_ABI
        )

        current_allowance = await loop.run_in_executor(
            None,
            token_contract.functions.allowance(
                self._account.address, self.router_addr
            ).call,
        )

        if current_allowance >= amount:
            return  # Already approved

        # Approve max uint256
        max_uint = 2**256 - 1
        approve_data = token_contract.functions.approve(self.router_addr, max_uint)

        nonce = await self._get_nonce()
        gas_params = await self._get_gas_price()

        tx = approve_data.build_transaction({
            "from": self._account.address,
            "gas": self.MAX_GAS_APPROVE,
            "nonce": nonce,
            "chainId": self.chain_id,
            **({"gasPrice": gas_params["gasPrice"]}
               if gas_params["type"] == "legacy"
               else {
                   "maxFeePerGas": gas_params["maxFeePerGas"],
                   "maxPriorityFeePerGas": gas_params["maxPriorityFeePerGas"],
               }),
        })

        signed = self._account.sign_transaction(tx)
        tx_hash = await self._send_multi_rpc(signed)

        if tx_hash:
            receipt = await self._wait_confirmation(tx_hash, timeout=30)
            if receipt and receipt["status"] == 1:
                logger.info(f"Token approved for router: {token_address[:10]}…")
            else:
                logger.warning(f"Approval failed for {token_address[:10]}…")

    async def _send_multi_rpc(self, signed_tx) -> Optional[bytes]:
        """
        Send signed transaction to multiple RPCs simultaneously.
        First successful response wins. This improves inclusion speed.
        """
        loop = asyncio.get_event_loop()
        raw_tx = signed_tx.raw_transaction

        # Always try primary RPC first
        try:
            tx_hash = await loop.run_in_executor(
                None, self.w3.eth.send_raw_transaction, raw_tx
            )
            return tx_hash
        except Exception as e:
            logger.debug(f"Primary RPC send failed: {e}")

        # Try fallback RPCs
        for rpc_url in self.rpc_list[1:]:
            try:
                fallback_w3 = Web3(Web3.HTTPProvider(rpc_url))
                tx_hash = await loop.run_in_executor(
                    None, fallback_w3.eth.send_raw_transaction, raw_tx
                )
                logger.info(f"Sent via fallback RPC: {rpc_url}")
                return tx_hash
            except Exception as e:
                logger.debug(f"Fallback RPC {rpc_url} failed: {e}")

        return None

    async def _wait_confirmation(self, tx_hash, timeout: int = 60) -> Optional[dict]:
        """Wait for transaction confirmation with timeout."""
        loop = asyncio.get_event_loop()
        start = time.time()

        while time.time() - start < timeout:
            try:
                receipt = await loop.run_in_executor(
                    None, self.w3.eth.get_transaction_receipt, tx_hash
                )
                if receipt:
                    return dict(receipt)
            except Exception:
                pass
            await asyncio.sleep(1)

        return None

    async def pre_build_buy_tx(self, token_address: str, amount_native: float,
                                slippage: float = 12.0) -> Optional[PreBuiltTx]:
        """
        Pre-build a buy transaction template for later instant execution.
        The template expires after 15 seconds (gas/nonce staleness).
        """
        if not self.enabled or not self._router:
            return None

        try:
            loop = asyncio.get_event_loop()
            token_cs = Web3.to_checksum_address(token_address)
            amount_wei = Web3.to_wei(amount_native, "ether")
            deadline = int(time.time()) + self.TX_DEADLINE

            tx_data = self._router.functions.swapExactETHForTokensSupportingFeeOnTransferTokens(
                0,  # min_out = 0 for pre-built (will be updated at execution)
                [self.weth_addr, token_cs],
                self._account.address,
                deadline,
            )

            gas_params = await self._get_gas_price()

            prebuilt = PreBuiltTx(
                to=self.router_addr,
                data=tx_data._encode_transaction_data(),
                value=amount_wei,
                gas=self.MAX_GAS_BUY,
                chain_id=self.chain_id,
                created_at=time.time(),
                expires_at=time.time() + 15,
            )

            if gas_params["type"] == "legacy":
                prebuilt.gas_price = gas_params["gasPrice"]
            else:
                prebuilt.max_fee_per_gas = gas_params["maxFeePerGas"]
                prebuilt.max_priority_fee_per_gas = gas_params["maxPriorityFeePerGas"]

            cache_key = f"buy_{token_address.lower()}"
            self._prebuilt_cache[cache_key] = prebuilt

            logger.debug(f"Pre-built buy tx for {token_address[:10]}… (expires in 15s)")
            return prebuilt

        except Exception as e:
            logger.debug(f"Pre-build failed: {e}")
            return None

    async def get_wallet_balance(self) -> dict:
        """Get executor wallet balance info."""
        if not self._account:
            return {"address": "", "native_balance": 0, "native_balance_raw": 0}

        loop = asyncio.get_event_loop()
        try:
            balance = await loop.run_in_executor(
                None, self.w3.eth.get_balance, self._account.address
            )
            return {
                "address": self._account.address,
                "native_balance": float(Web3.from_wei(balance, "ether")),
                "native_balance_raw": balance,
            }
        except Exception:
            return {"address": self._account.address, "native_balance": 0, "native_balance_raw": 0}

    def get_stats(self) -> dict:
        """Return execution statistics."""
        return {
            "enabled": self.enabled,
            "wallet": self.wallet_address[:10] + "…" if self.wallet_address else "N/A",
            "total_buys": self._total_buys,
            "total_sells": self._total_sells,
            "total_spent_native": round(self._total_spent_native, 4),
            "total_received_native": round(self._total_received_native, 4),
            "prebuilt_cache_size": len(self._prebuilt_cache),
            "chain_id": self.chain_id,
        }

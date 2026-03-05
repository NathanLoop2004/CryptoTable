/**
 * transactions.js — Send native currency, ERC-20 tokens, and DEX swaps.
 *
 * Exposes a global TxModule that dashboard.js wires up after
 * re-establishing the ethers signer.
 */
/* global ethers */
var TxModule = (function () {
    "use strict";

    /* ═══════════════════════════════════════════════════════════════════
       CONSTANTS
       ═══════════════════════════════════════════════════════════════════ */

    const ERC20_ABI = [
        "function name() view returns (string)",
        "function symbol() view returns (string)",
        "function decimals() view returns (uint8)",
        "function balanceOf(address) view returns (uint256)",
        "function transfer(address to, uint256 amount) returns (bool)",
        "function approve(address spender, uint256 amount) returns (bool)",
        "function allowance(address owner, address spender) view returns (uint256)",
    ];

    const ROUTER_ABI = [
        "function swapExactETHForTokens(uint amountOutMin, address[] calldata path, address to, uint deadline) payable returns (uint[] memory amounts)",
        "function swapExactTokensForETH(uint amountIn, uint amountOutMin, address[] calldata path, address to, uint deadline) returns (uint[] memory amounts)",
        "function swapExactTokensForTokens(uint amountIn, uint amountOutMin, address[] calldata path, address to, uint deadline) returns (uint[] memory amounts)",
        "function getAmountsOut(uint amountIn, address[] calldata path) view returns (uint[] memory amounts)",
        "function WETH() view returns (address)",
    ];

    // DEX routers & wrapped native per chain
    const DEX_CONFIG = {
        56: {
            router: "0x10ED43C718714eb63d5aA57B78B54704E256024E",   // PancakeSwap V2
            wrapped: "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c",  // WBNB
            name: "PancakeSwap",
            symbol: "BNB",
            explorer: "https://bscscan.com/tx/",
        },
        1: {
            router: "0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D",   // Uniswap V2
            wrapped: "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",  // WETH
            name: "Uniswap V2",
            symbol: "ETH",
            explorer: "https://etherscan.io/tx/",
        },
        137: {
            router: "0xa5E0829CaCEd8fFDD4De3c43696c57F7D7A678ff",   // QuickSwap
            wrapped: "0x0d500B1d8E8eF31E21C99d1Db9A6444d3ADf1270",  // WMATIC
            name: "QuickSwap",
            symbol: "MATIC",
            explorer: "https://polygonscan.com/tx/",
        },
        42161: {
            router: "0x1b02dA8Cb0d097eB8D57A175b88c7D8b47997506",   // SushiSwap Arbitrum
            wrapped: "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1",  // WETH Arb
            name: "SushiSwap",
            symbol: "ETH",
            explorer: "https://arbiscan.io/tx/",
        },
    };

    /* ═══════════════════════════════════════════════════════════════════
       STATE (set from outside via init)
       ═══════════════════════════════════════════════════════════════════ */

    let _signer   = null;
    let _provider  = null;
    let _address   = null;
    let _chainId   = 56;
    let _onLog     = () => {};         // callback for tx-feed lines

    /* ═══════════════════════════════════════════════════════════════════
       INIT — called once from dashboard.js after wallet reconnect
       ═══════════════════════════════════════════════════════════════════ */

    function init({ signer, provider, address, chainId, onLog }) {
        _signer   = signer;
        _provider  = provider;
        _address   = address;
        _chainId   = chainId;
        _onLog     = onLog || _onLog;
    }

    function getConfig() {
        return DEX_CONFIG[_chainId] || DEX_CONFIG[56];
    }

    /* ═══════════════════════════════════════════════════════════════════
       BALANCE
       ═══════════════════════════════════════════════════════════════════ */

    async function getNativeBalance() {
        const bal = await _provider.getBalance(_address);
        return ethers.formatEther(bal);
    }

    /* ═══════════════════════════════════════════════════════════════════
       SEND NATIVE
       ═══════════════════════════════════════════════════════════════════ */

    async function sendNative(to, amountStr) {
        if (!ethers.isAddress(to)) throw new Error("Invalid recipient address.");
        const value = ethers.parseEther(amountStr);
        if (value <= 0n) throw new Error("Amount must be > 0.");

        _onLog("Info", `Sending ${amountStr} native to ${to}…`);

        const tx = await _signer.sendTransaction({ to, value });
        _onLog("Pending", `TX hash: ${tx.hash}`);

        const receipt = await tx.wait();
        _onLog("Confirmed", `Block ${receipt.blockNumber} — ${_explorerLink(tx.hash)}`);
        return receipt;
    }

    /* ═══════════════════════════════════════════════════════════════════
       SEND ERC-20 TOKEN
       ═══════════════════════════════════════════════════════════════════ */

    async function getTokenInfo(tokenAddr) {
        const token = new ethers.Contract(tokenAddr, ERC20_ABI, _provider);
        const [name, symbol, decimals, balance] = await Promise.all([
            token.name(),
            token.symbol(),
            token.decimals(),
            token.balanceOf(_address),
        ]);
        return {
            name,
            symbol,
            decimals: Number(decimals),
            balance: ethers.formatUnits(balance, decimals),
        };
    }

    async function sendToken(tokenAddr, to, amountStr) {
        if (!ethers.isAddress(tokenAddr)) throw new Error("Invalid token address.");
        if (!ethers.isAddress(to)) throw new Error("Invalid recipient address.");

        const token = new ethers.Contract(tokenAddr, ERC20_ABI, _signer);
        const decimals = await token.decimals();
        const amount = ethers.parseUnits(amountStr, decimals);

        _onLog("Info", `Sending ${amountStr} tokens to ${to}…`);

        const tx = await token.transfer(to, amount);
        _onLog("Pending", `TX hash: ${tx.hash}`);

        const receipt = await tx.wait();
        _onLog("Confirmed", `Block ${receipt.blockNumber} — ${_explorerLink(tx.hash)}`);
        return receipt;
    }

    /* ═══════════════════════════════════════════════════════════════════
       SWAP (DEX)
       ═══════════════════════════════════════════════════════════════════ */

    /**
     * Get estimated output for a swap.
     * @param {string|null} tokenIn  - address (null = native)
     * @param {string}      tokenOut - address
     * @param {string}      amountIn - human-readable amount
     */
    async function getSwapEstimate(tokenIn, tokenOut, amountIn) {
        const cfg = getConfig();
        const router = new ethers.Contract(cfg.router, ROUTER_ABI, _provider);

        const isNativeIn = !tokenIn || tokenIn.trim() === "";
        const path = _buildPath(isNativeIn ? cfg.wrapped : tokenIn, tokenOut, cfg.wrapped);

        // Determine decimals of tokenIn
        let decimalsIn = 18;
        if (!isNativeIn) {
            const tin = new ethers.Contract(tokenIn, ERC20_ABI, _provider);
            decimalsIn = Number(await tin.decimals());
        }

        const parsedIn = ethers.parseUnits(amountIn, decimalsIn);
        const amounts = await router.getAmountsOut(parsedIn, path);
        const outRaw = amounts[amounts.length - 1];

        // Determine decimals of tokenOut
        const tout = new ethers.Contract(tokenOut, ERC20_ABI, _provider);
        const decimalsOut = Number(await tout.decimals());
        const symbolOut = await tout.symbol();

        return {
            amountOut: ethers.formatUnits(outRaw, decimalsOut),
            symbol: symbolOut,
            path: path,
            rawIn: parsedIn,
            rawOut: outRaw,
            decimalsIn,
        };
    }

    /**
     * Execute swap.
     * @param {string|null} tokenIn
     * @param {string}      tokenOut
     * @param {string}      amountIn   - human-readable
     * @param {number}      slippage   - e.g. 0.5 for 0.5%
     */
    async function swap(tokenIn, tokenOut, amountIn, slippage) {
        const cfg = getConfig();
        const router = new ethers.Contract(cfg.router, ROUTER_ABI, _signer);

        const isNativeIn = !tokenIn || tokenIn.trim() === "";
        const est = await getSwapEstimate(tokenIn, tokenOut, amountIn);

        // slippage tolerance
        const minOut = est.rawOut * BigInt(Math.floor((100 - slippage) * 10)) / 1000n;
        const deadline = Math.floor(Date.now() / 1000) + 60 * 20; // 20 min

        _onLog("Info", `Swapping ${amountIn} → ~${est.amountOut} ${est.symbol} via ${cfg.name}…`);

        let tx;
        if (isNativeIn) {
            // Native → Token  — use manual gasLimit to bypass estimateGas failures
            // and show a clearer revert from the node instead.
            const overrides = { value: est.rawIn };
            try {
                const gasEst = await router.swapExactETHForTokens.estimateGas(
                    minOut, est.path, _address, deadline, overrides
                );
                overrides.gasLimit = gasEst * 120n / 100n;  // +20 % buffer
            } catch (gasErr) {
                // If estimateGas fails, check balance first
                const bal = await _provider.getBalance(_address);
                if (bal < est.rawIn) {
                    const cfg = getConfig();
                    throw new Error(
                        `Insufficient ${cfg.symbol} balance. You need ${ethers.formatEther(est.rawIn)} ${cfg.symbol} ` +
                        `but only have ${ethers.formatEther(bal)} ${cfg.symbol}.`
                    );
                }
                // Otherwise, re-throw with context
                overrides.gasLimit = 350000n;  // reasonable fallback
            }
            tx = await router.swapExactETHForTokens(
                minOut,
                est.path,
                _address,
                deadline,
                overrides
            );
        } else {
            // Need approval first
            await _ensureApproval(tokenIn, cfg.router, est.rawIn);

            const isNativeOut = false; // tokenOut is always a contract here
            // Check if tokenOut is wrapped native (user wants native out)
            if (tokenOut.toLowerCase() === cfg.wrapped.toLowerCase()) {
                tx = await router.swapExactTokensForETH(
                    est.rawIn,
                    minOut,
                    est.path,
                    _address,
                    deadline
                );
            } else {
                tx = await router.swapExactTokensForTokens(
                    est.rawIn,
                    minOut,
                    est.path,
                    _address,
                    deadline
                );
            }
        }

        _onLog("Pending", `TX hash: ${tx.hash}`);
        const receipt = await tx.wait();
        _onLog("Confirmed", `Block ${receipt.blockNumber} — ${_explorerLink(tx.hash)}`);
        return receipt;
    }

    /* ═══════════════════════════════════════════════════════════════════
       INTERNAL HELPERS
       ═══════════════════════════════════════════════════════════════════ */

    async function _ensureApproval(tokenAddr, spender, amount) {
        const token = new ethers.Contract(tokenAddr, ERC20_ABI, _signer);
        const current = await token.allowance(_address, spender);
        if (current >= amount) return;

        _onLog("Info", `Approving token spend…`);
        const tx = await token.approve(spender, ethers.MaxUint256);
        await tx.wait();
        _onLog("Info", `Approval confirmed.`);
    }

    function _buildPath(tokenIn, tokenOut, wrapped) {
        const inLow  = tokenIn.toLowerCase();
        const outLow = tokenOut.toLowerCase();
        const wLow   = wrapped.toLowerCase();
        // Direct pair or route through wrapped native
        if (inLow === wLow || outLow === wLow) {
            return [tokenIn, tokenOut];
        }
        return [tokenIn, wrapped, tokenOut];
    }

    function _explorerLink(hash) {
        const cfg = getConfig();
        return cfg.explorer ? `${cfg.explorer}${hash}` : hash;
    }

    /* ═══════════════════════════════════════════════════════════════════
       PUBLIC API
       ═══════════════════════════════════════════════════════════════════ */

    return {
        init,
        getConfig,
        getNativeBalance,
        sendNative,
        getTokenInfo,
        sendToken,
        getSwapEstimate,
        swap,
    };
})();

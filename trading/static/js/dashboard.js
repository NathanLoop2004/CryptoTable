/**
 * dashboard.js — Dashboard page
 * Manages Binance WebSocket streams, wallet WS events, and sidebar navigation.
 */
(function () {
    "use strict";

    /* ── Guard: redirect if not logged in ──────────────────────────────── */
    let wallet;
    try {
        wallet = JSON.parse(sessionStorage.getItem("tw_wallet"));
    } catch (_) { /* ignore */ }

    if (!wallet || !wallet.authenticated) {
        window.location.href = "/";
        return;
    }

    /* ── Chain names ───────────────────────────────────────────────────── */
    const CHAINS = {
        1: "Ethereum Mainnet",
        56: "BNB Smart Chain",
        137: "Polygon",
        42161: "Arbitrum One",
        10: "Optimism",
        43114: "Avalanche C-Chain",
    };

    /* ── Chain parameters for wallet_addEthereumChain ──────────────────── */
    const CHAIN_PARAMS = {
        56: {
            chainId: "0x38",
            chainName: "BNB Smart Chain",
            nativeCurrency: { name: "BNB", symbol: "BNB", decimals: 18 },
            rpcUrls: ["https://bsc-dataseed.binance.org/"],
            blockExplorerUrls: ["https://bscscan.com"],
        },
        1: {
            chainId: "0x1",
            chainName: "Ethereum Mainnet",
            nativeCurrency: { name: "Ether", symbol: "ETH", decimals: 18 },
            rpcUrls: ["https://eth.llamarpc.com"],
            blockExplorerUrls: ["https://etherscan.io"],
        },
        137: {
            chainId: "0x89",
            chainName: "Polygon",
            nativeCurrency: { name: "MATIC", symbol: "MATIC", decimals: 18 },
            rpcUrls: ["https://polygon-rpc.com/"],
            blockExplorerUrls: ["https://polygonscan.com"],
        },
        42161: {
            chainId: "0xa4b1",
            chainName: "Arbitrum One",
            nativeCurrency: { name: "Ether", symbol: "ETH", decimals: 18 },
            rpcUrls: ["https://arb1.arbitrum.io/rpc"],
            blockExplorerUrls: ["https://arbiscan.io"],
        },
    };

    /* ── DOM refs ──────────────────────────────────────────────────────── */
    // Top nav
    const walletShortAddr   = document.getElementById("wallet-short-addr");
    const btnDisconnect     = document.getElementById("btn-disconnect");

    // Market section
    const streamSymbol      = document.getElementById("stream-symbol");
    const symbolDropdown    = document.getElementById("symbol-dropdown");
    const streamType        = document.getElementById("stream-type");
    const btnSubscribe      = document.getElementById("btn-subscribe");
    const btnUnsubscribe    = document.getElementById("btn-unsubscribe");

    // ── Binance symbol catalogue ──────────────────────────────────────
    let allSymbols = [];       // [{ symbol: "btcusdt", label: "BTC/USDT", base: "BTC", quote: "USDT" }, …]
    let symbolsLoaded = false;
    const activeStreamsDiv   = document.getElementById("active-streams");
    const priceCardsDiv     = document.getElementById("price-cards");
    const liveFeedDiv       = document.getElementById("live-feed");
    const btnClearFeed      = document.getElementById("btn-clear-feed");
    const wsBinanceDot      = document.getElementById("ws-binance-dot");
    const wsBinanceLabel    = document.getElementById("ws-binance-label");

    // Wallet section
    const infoAddress       = document.getElementById("info-address");
    const infoProvider      = document.getElementById("info-provider");
    const infoChain         = document.getElementById("info-chain");
    const infoChainId       = document.getElementById("info-chain-id");
    const infoBlock         = document.getElementById("info-block");
    const infoGas           = document.getElementById("info-gas");
    const infoNativeBal     = document.getElementById("info-native-bal");
    const infoNativeUsd     = document.getElementById("info-native-usd");
    const infoTokensList    = document.getElementById("info-tokens-list");
    const infoStatus        = document.getElementById("info-status");
    const infoConnectedAt   = document.getElementById("info-connected-at");
    const btnRefreshWallet  = document.getElementById("btn-refresh-wallet");
    const wsWalletDot       = document.getElementById("ws-wallet-dot");
    const wsWalletLabel     = document.getElementById("ws-wallet-label");
    const walletFeedDiv     = document.getElementById("wallet-feed");
    const btnClearWalletFeed = document.getElementById("btn-clear-wallet-feed");

    /* ── State ─────────────────────────────────────────────────────────── */
    let binanceWs = null;
    let walletWs  = null;
    const activeStreams = new Set();
    const priceData = {};   // symbol -> { price, prevPrice }
    const priceHistory = {}; // symbol -> [ {time, value} ]
    const MAX_FEED_LINES = 200;
    const MAX_HISTORY = 3000;

    /* ── Chart state ───────────────────────────────────────────────────── */
    let chart = null;
    let candleSeries = null;
    let volumeSeries = null;
    let selectedSymbol = null;
    let chartInterval = "1m";       // Binance kline interval
    let chartType = "candle";       // "candle" or "line"
    let chartOpenPrice = 0;         // first candle open for color

    /* ── Populate wallet info ──────────────────────────────────────────── */
    const NATIVE_SYMBOLS = { 1: "ETH", 56: "BNB", 137: "MATIC", 42161: "ETH", 10: "ETH", 43114: "AVAX" };
    const nativeSym = NATIVE_SYMBOLS[wallet.chain_id] || "ETH";

    const shortAddr = wallet.address.slice(0, 6) + "…" + wallet.address.slice(-4);
    walletShortAddr.textContent = shortAddr;
    infoAddress.textContent     = wallet.address;
    infoProvider.textContent    = wallet.provider || "—";
    infoChain.textContent       = CHAINS[wallet.chain_id] || `Chain ${wallet.chain_id}`;
    infoChainId.textContent     = wallet.chain_id || "—";
    infoStatus.textContent      = "Active";
    infoConnectedAt.textContent = new Date().toLocaleString();

    /* ── Chain badge in topbar ──────────────────────────────────────────── */
    const chainBadgeEl = document.getElementById("chain-badge");
    const chainNameEl  = document.getElementById("chain-name");
    const btnSwitchChain = document.getElementById("btn-switch-chain");
    let currentChainId = wallet.chain_id || 56;

    function updateChainBadge(chainId) {
        currentChainId = chainId;
        if (chainNameEl)  chainNameEl.textContent  = CHAINS[chainId] || `Chain ${chainId}`;
        if (chainBadgeEl) {
            chainBadgeEl.classList.toggle("chain-wrong", chainId !== wallet.chain_id);
        }
    }
    updateChainBadge(wallet.chain_id);

    /**
     * Prompt the wallet to switch to the expected chain (wallet.chain_id).
     * If the chain isn't added, request wallet_addEthereumChain.
     * Returns true on success.
     */
    async function switchToChain(targetChainId) {
        const raw = window.trustwallet || window.ethereum;
        if (!raw) { console.warn("No wallet provider for chain switch"); return false; }

        const hexId = "0x" + targetChainId.toString(16);
        try {
            await raw.request({ method: "wallet_switchEthereumChain", params: [{ chainId: hexId }] });
            console.log(`Switched to chain ${targetChainId}`);
            updateChainBadge(targetChainId);
            return true;
        } catch (switchErr) {
            // 4902 = chain not added
            if (switchErr.code === 4902 || (switchErr.data && switchErr.data.originalError && switchErr.data.originalError.code === 4902)) {
                const params = CHAIN_PARAMS[targetChainId];
                if (!params) {
                    console.error(`No CHAIN_PARAMS for chain ${targetChainId}`);
                    return false;
                }
                try {
                    await raw.request({ method: "wallet_addEthereumChain", params: [params] });
                    console.log(`Added and switched to chain ${targetChainId}`);
                    updateChainBadge(targetChainId);
                    return true;
                } catch (addErr) {
                    console.error("User rejected adding chain:", addErr);
                    return false;
                }
            }
            // User rejected switch
            console.error("Chain switch failed:", switchErr);
            return false;
        }
    }

    // Listen for chain changes from the wallet itself
    (function listenChainChanged() {
        const raw = window.trustwallet || window.ethereum;
        if (!raw || !raw.on) return;
        raw.on("chainChanged", (hexChainId) => {
            const newChain = parseInt(hexChainId, 16);
            console.log(`Wallet chain changed to ${newChain} (${CHAINS[newChain] || 'unknown'})`);
            currentChainId = newChain;
            updateChainBadge(newChain);
            if (infoChain) infoChain.textContent    = CHAINS[newChain] || `Chain ${newChain}`;
            if (infoChainId) infoChainId.textContent = newChain;

            // Rebuild provider from scratch (ethers caches old network)
            (async () => {
                try {
                    await rebuildProvider();
                    if (typeof TxModule !== "undefined") {
                        TxModule.init({
                            signer:   ethersSigner,
                            provider: ethersProvider,
                            address:  wallet.address,
                            chainId:  newChain,
                            onLog:    txLog,
                        });
                    }
                    refreshBalance();
                    refreshWalletInfo();
                } catch (e) { console.warn("Re-init after chain change failed:", e); }
            })();
        });
    })();

    // Switch chain button handler
    if (btnSwitchChain) {
        btnSwitchChain.addEventListener("click", async () => {
            btnSwitchChain.disabled = true;
            btnSwitchChain.textContent = "Switching…";
            const ok = await switchToChain(wallet.chain_id);
            btnSwitchChain.disabled = false;
            btnSwitchChain.textContent = "Switch";
            if (!ok) alert(`Could not switch to ${CHAINS[wallet.chain_id] || wallet.chain_id}. Please switch manually in your wallet.`);
        });
    }

    // Copy address on click
    if (infoAddress) {
        infoAddress.addEventListener("click", () => {
            navigator.clipboard.writeText(wallet.address).then(() => {
                infoAddress.classList.add("copied");
                const orig = infoAddress.textContent;
                infoAddress.textContent = "Copied!";
                setTimeout(() => { infoAddress.textContent = orig; infoAddress.classList.remove("copied"); }, 1200);
            });
        });
    }

    /* ── Wallet data helpers ── */
    // Well-known token lists per chain (top tokens to probe)
    const KNOWN_TOKENS = {
        56: [ // BSC
            { symbol: "USDT",  address: "0x55d398326f99059fF775485246999027B3197955", decimals: 18 },
            { symbol: "USDC",  address: "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d", decimals: 18 },
            { symbol: "BUSD",  address: "0xe9e7CEA3DedcA5984780Bafc599bD69ADd087D56", decimals: 18 },
            { symbol: "WBNB",  address: "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c", decimals: 18 },
            { symbol: "CAKE",  address: "0x0E09FaBB73Bd3Ade0a17ECC321fD13a19e81cE82", decimals: 18 },
            { symbol: "DAI",   address: "0x1AF3F329e8BE154074D8769D1FFa4eE058B1DBc3", decimals: 18 },
        ],
        1: [ // Ethereum
            { symbol: "USDT",  address: "0xdAC17F958D2ee523a2206206994597C13D831ec7", decimals: 6 },
            { symbol: "USDC",  address: "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48", decimals: 6 },
            { symbol: "DAI",   address: "0x6B175474E89094C44Da98b954EedeAC495271d0F", decimals: 18 },
            { symbol: "WETH",  address: "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2", decimals: 18 },
            { symbol: "LINK",  address: "0x514910771AF9Ca656af840dff83E8264EcF986CA", decimals: 18 },
            { symbol: "UNI",   address: "0x1f9840a85d5aF5bf1D1762F925BDADdC4201F984", decimals: 18 },
        ],
        137: [ // Polygon
            { symbol: "USDT",  address: "0xc2132D05D31c914a87C6611C10748AEb04B58e8F", decimals: 6 },
            { symbol: "USDC",  address: "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174", decimals: 6 },
            { symbol: "WMATIC",address: "0x0d500B1d8E8eF31E21C99d1Db9A6444d3ADf1270", decimals: 18 },
            { symbol: "WETH",  address: "0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619", decimals: 18 },
        ],
    };

    const ERC20_BALANCE_ABI = ["function balanceOf(address) view returns (uint256)"];

    /* ── Token contract addresses per chain (Binance symbol → address) ─── */
    const TOKEN_CONTRACTS = {
        56: { // BSC
            "BTC":   "0x7130d2A12B9BCbFAe4f2634d864A1Ee1Ce3Ead9c",  // BTCB
            "ETH":   "0x2170Ed0880ac9A755fd29B2688956BD959F933F8",  // ETH BEP-20
            "BNB":   "",  // native
            "USDT":  "0x55d398326f99059fF775485246999027B3197955",
            "USDC":  "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d",
            "BUSD":  "0xe9e7CEA3DedcA5984780Bafc599bD69ADd087D56",
            "DAI":   "0x1AF3F329e8BE154074D8769D1FFa4eE058B1DBc3",
            "CAKE":  "0x0E09FaBB73Bd3Ade0a17ECC321fD13a19e81cE82",
            "XRP":   "0x1D2F0da169ceB9fC7B3144628dB156f3F6c60dBE",
            "ADA":   "0x3EE2200Efb3400fAbB9AacF31297cBdD1d435D47",
            "DOGE":  "0xbA2aE424d960c26247Dd6c32edC70B295c744C43",
            "DOT":   "0x7083609fce4d1d8Dc0C979AAb8c869Ea2C873402",
            "LINK":  "0xF8A0BF9cF54Bb92F17374d9e9A321E6a111a51bD",
            "UNI":   "0xBf5140A22578168FD562DCcF235E5D43A02ce9B1",
            "MATIC": "0xCC42724C6683B7E57334c4E856f4c9965ED682bD",
            "SOL":   "0x570A5D26f7765Ecb712C0924E4De545B89fD43dF",
            "AVAX":  "0x1CE0c2827e2eF14D5C4f29a091d735A204794041",
            "SHIB":  "0x2859e4544C4bB03966803b044A93563Bd2D0DD4D",
            "LTC":   "0x4338665CBB7B2485A8855A139b75D5e34AB0DB94",
            "ATOM":  "0x0Eb3a705fc54725037CC9e008bDede697f62F335",
            "FIL":   "0x0D8Ce2A99Bb6e3B7Db580eD848240e4a0F9aE153",
            "AAVE":  "0xfb6115445Bff7b52FeB98650C87f44907E58f802",
        },
        1: { // Ethereum
            "ETH":   "",  // native
            "USDT":  "0xdAC17F958D2ee523a2206206994597C13D831ec7",
            "USDC":  "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
            "DAI":   "0x6B175474E89094C44Da98b954EedeAC495271d0F",
            "WBTC":  "0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599",
            "BTC":   "0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599",  // alias → WBTC
            "LINK":  "0x514910771AF9Ca656af840dff83E8264EcF986CA",
            "UNI":   "0x1f9840a85d5aF5bf1D1762F925BDADdC4201F984",
            "AAVE":  "0x7Fc66500c84A76Ad7e9c93437bFc5Ac33E2DDaE9",
            "SHIB":  "0x95aD61b0a150d79219dCF64E1E6Cc01f0B64C4cE",
            "MATIC": "0x7D1AfA7B718fb893dB30A3aBc0Cfc608AaCfeBB0",
            "LDO":   "0x5A98FcBEA516Cf06857215779Fd812CA3beF1B32",
        },
        137: { // Polygon
            "MATIC": "",  // native
            "USDT":  "0xc2132D05D31c914a87C6611C10748AEb04B58e8F",
            "USDC":  "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174",
            "WETH":  "0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619",
            "ETH":   "0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619",
            "BTC":   "0x1BFD67037B42Cf73acF2047067bd4F2C47D9BfD6",  // WBTC
            "LINK":  "0x53E0bca35eC356BD5ddDFebbD1Fc0fD03FaBad39",
            "AAVE":  "0xD6DF932A45C0f255f85145f286eA0b292B21C90B",
        },
        42161: { // Arbitrum
            "ETH":   "",  // native
            "USDT":  "0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9",
            "USDC":  "0xFF970A61A04b1cA14834A43f5dE4533eBDDB5CC8",
            "BTC":   "0x2f2a2543B76A4166549F7aaB2e75Bef0aefC5B0f",  // WBTC
            "LINK":  "0xf97f4df75117a78c1A5a0DBb814Af92458539FB4",
            "ARB":   "0x912CE59144191C1204E64559FE8253a0e49E6548",
        },
    };

    /**
     * Resolve a Binance symbol (e.g. "BTC") to a contract address for the current chain.
     * Returns "" for native, a hex address for tokens, or null if not found.
     */
    function resolveTokenAddress(symbol) {
        if (!symbol) return null;
        const chain = wallet.chain_id || 56;
        const map = TOKEN_CONTRACTS[chain];
        if (!map) return null;
        const upper = symbol.toUpperCase();
        // Check direct match
        if (upper in map) return map[upper];
        // Check if it's the native symbol for the chain
        const cfg = TxModule ? TxModule.getConfig() : null;
        if (cfg && upper === cfg.symbol.toUpperCase()) return "";
        return null;
    }

    /** Fetch and display full wallet info (balance, gas, block, tokens) */
    async function refreshWalletInfo() {
        if (!ethersProvider) return;
        try {
            // Native balance
            const rawBal = await ethersProvider.getBalance(wallet.address);
            const bal = parseFloat(ethers.formatEther(rawBal));
            infoNativeBal.textContent = `${bal.toFixed(6)} ${nativeSym}`;

            // USD estimate from Binance ticker (best effort)
            try {
                const pair = nativeSym === "ETH" ? "ETHUSDT" : nativeSym + "USDT";
                const res = await fetch(`https://api.binance.com/api/v3/ticker/price?symbol=${pair}`);
                const data = await res.json();
                const usd = bal * parseFloat(data.price);
                infoNativeUsd.textContent = `≈ $${usd.toLocaleString(undefined, {minimumFractionDigits:2, maximumFractionDigits:2})}`;
            } catch(_) {
                infoNativeUsd.textContent = "—";
            }

            // Block number
            const block = await ethersProvider.getBlockNumber();
            infoBlock.textContent = block.toLocaleString();

            // Gas price
            const feeData = await ethersProvider.getFeeData();
            if (feeData.gasPrice) {
                const gwei = parseFloat(ethers.formatUnits(feeData.gasPrice, "gwei"));
                infoGas.textContent = `${gwei.toFixed(2)} Gwei`;
            }

            // Token balances
            const tokens = KNOWN_TOKENS[wallet.chain_id] || [];
            const found = [];
            await Promise.all(tokens.map(async (tk) => {
                try {
                    const contract = new ethers.Contract(tk.address, ERC20_BALANCE_ABI, ethersProvider);
                    const raw = await contract.balanceOf(wallet.address);
                    const balance = parseFloat(ethers.formatUnits(raw, tk.decimals));
                    if (balance > 0.0001) {
                        found.push({ ...tk, balance });
                    }
                } catch(_) { /* ignore failed calls */ }
            }));

            // Render tokens list
            if (found.length > 0) {
                infoTokensList.innerHTML = "";
                found.sort((a, b) => b.balance - a.balance);
                for (const tk of found) {
                    const row = document.createElement("div");
                    row.className = "wi-token-row";
                    const iconBase = tk.symbol.replace(/^W/, "").toLowerCase();
                    row.innerHTML = `
                        <img class="wi-token-icon" src="https://assets.coincap.io/assets/icons/${iconBase}@2x.png"
                             onerror="this.style.display='none'" alt="">
                        <span class="wi-token-sym">${tk.symbol}</span>
                        <span class="wi-token-bal">${tk.balance < 1 ? tk.balance.toFixed(6) : tk.balance.toFixed(4)}</span>
                    `;
                    infoTokensList.appendChild(row);
                }
            } else {
                infoTokensList.innerHTML = '<span class="wi-muted">No tokens found</span>';
            }

        } catch(err) {
            console.warn("Wallet info refresh failed:", err);
        }
    }

    // Refresh button
    if (btnRefreshWallet) {
        btnRefreshWallet.addEventListener("click", () => refreshWalletInfo());
    }

    // Auto-refresh every 30s
    setInterval(() => {
        if (ethersProvider) refreshWalletInfo();
    }, 30000);

    /* ── Bottom tab switching ──────────────────────────────────────────── */
    document.querySelectorAll(".btab").forEach(btn => {
        btn.addEventListener("click", () => {
            const key = btn.dataset.btab;
            document.querySelectorAll(".btab").forEach(b => b.classList.toggle("active", b === btn));
            document.querySelectorAll(".btab-panel").forEach(p =>
                p.classList.toggle("active", p.id === `btab-${key}`)
            );
        });
    });
    document.querySelectorAll(".btab2").forEach(btn => {
        btn.addEventListener("click", () => {
            const key = btn.dataset.btab2;
            document.querySelectorAll(".btab2").forEach(b => b.classList.toggle("active", b === btn));
            document.querySelectorAll(".btab2-panel").forEach(p =>
                p.classList.toggle("active", p.id === `btab2-${key}`)
            );
        });
    });

    /* ═══════════════════════════════════════════════════════════════════
       BINANCE WEBSOCKET
       ═══════════════════════════════════════════════════════════════════ */

    function connectBinance() {
        const wsProto = location.protocol === "https:" ? "wss:" : "ws:";
        binanceWs = new WebSocket(`${wsProto}//${location.host}/ws/binance/`);

        binanceWs.onopen = () => {
            wsBinanceDot.classList.add("connected");
            wsBinanceLabel.textContent = "Binance: Connected";
            addFeedLine(liveFeedDiv, "System", "Connected to Binance stream.");
        };

        binanceWs.onclose = () => {
            wsBinanceDot.classList.remove("connected");
            wsBinanceLabel.textContent = "Binance: Disconnected";
            addFeedLine(liveFeedDiv, "System", "Binance stream disconnected.");
            // Auto-reconnect after 3 s
            setTimeout(connectBinance, 3000);
        };

        binanceWs.onerror = () => {
            addFeedLine(liveFeedDiv, "Error", "WebSocket error.");
        };

        binanceWs.onmessage = (evt) => {
            try {
                const msg = JSON.parse(evt.data);
                handleBinanceMessage(msg);
            } catch (_) { /* ignore */ }
        };
    }

    function handleBinanceMessage(msg) {
        /* Server relays Binance's combined-stream format:
           { "stream": "btcusdt@trade", "data": { ... } }
           Or control messages:
           { "subscribed": [...] }
           { "unsubscribed": [...], "active_streams": [...] }
        */

        if (msg.subscribed) {
            syncActiveStreams(msg.subscribed);
            addFeedLine(liveFeedDiv, "Info", `Subscribed: ${msg.subscribed.join(", ")}`);
            return;
        }
        if (msg.unsubscribed !== undefined) {
            syncActiveStreams(msg.active_streams || []);
            addFeedLine(liveFeedDiv, "Info", `Unsubscribed: ${msg.unsubscribed.join(", ")}`);
            return;
        }
        if (msg.error || msg.info) {
            addFeedLine(liveFeedDiv, "Info", msg.error || msg.info);
            return;
        }

        // Market data
        const stream = msg.stream || "";
        const data   = msg.data || msg;

        // Determine symbol for price card
        const sym = (data.s || stream.split("@")[0] || "").toUpperCase();

        if (stream.includes("@trade")) {
            const price = parseFloat(data.p);
            const qty   = parseFloat(data.q);
            updatePriceCard(sym, price);
            // Market trades panel
            const trSym = (data.s || "").toUpperCase();
            if (trSym === selectedSymbol) {
                addMarketTrade(price, qty, data.T || Date.now(), data.m);
                // Update pair bar live price
                if (pairBarPriceEl) {
                    pairBarPriceEl.textContent = price.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 8 });
                }
                // Update spread price
                if (obSpreadPrice) {
                    obSpreadPrice.textContent = price.toFixed(getPricePrecision());
                }
                // Track live market price & auto-fill trade form
                liveMarketPrice = price;
                if (syncPriceAuto) {
                    syncLivePrice();
                }
            }
            addFeedLine(
                liveFeedDiv,
                stream,
                `${sym} Trade  ${price.toFixed(4)}  qty ${data.q}`
            );
        } else if (stream.includes("@kline")) {
            const k = data.k || {};
            const close = parseFloat(k.c);
            updatePriceCard(sym, close);
            // Feed real-time kline to chart
            handleKlineUpdate(data);
            addFeedLine(
                liveFeedDiv,
                stream,
                `${sym} Kline  O ${k.o}  H ${k.h}  L ${k.l}  C ${k.c}`
            );
        } else if (stream.includes("ticker")) {
            const last = parseFloat(data.c);
            updatePriceCard(sym, last);
            // Update pair bar 24h stats if this is the selected pair
            if (sym === selectedSymbol) {
                updatePairBarStats(data);
            }
            addFeedLine(
                liveFeedDiv,
                stream,
                `${sym} Ticker  ${last.toFixed(4)}  chg ${data.P}%`
            );
        } else if (stream.includes("depth")) {
            const bids = data.b || [];
            const asks = data.a || [];
            // Update order book if this is the selected pair
            if (sym === selectedSymbol && (bids.length || asks.length)) {
                renderOrderBook(asks, bids);
            }
            addFeedLine(liveFeedDiv, stream, `${sym} Depth  bids ${bids.length}  asks ${asks.length}`);
        } else {
            addFeedLine(liveFeedDiv, stream, JSON.stringify(data).slice(0, 120));
        }
    }

    /* ── Coin icon helper ──────────────────────────────────────────────── */

    const ICON_CDN = "https://assets.coincap.io/assets/icons";

    /**
     * Extract the base asset from a Binance-style symbol like "BTCUSDT".
     * Tries known quote assets in order of length (longest first).
     */
    const KNOWN_QUOTES = ["USDT","BUSD","USDC","TUSD","FDUSD","BTC","ETH","BNB","EUR","GBP","TRY","BRL","ARS","DAI","VAI","XRP","DOGE","DOT","AUD","BIDR","JPY","RUB","UAH","NGN","PLN","RON","ZAR","IDR","ARS"];

    function extractBase(symbol) {
        const s = symbol.toUpperCase();
        for (const q of KNOWN_QUOTES) {
            if (s.endsWith(q) && s.length > q.length) return s.slice(0, -q.length);
        }
        return s.slice(0, 3); // fallback: first 3 chars
    }

    function coinIconUrl(base) {
        return `${ICON_CDN}/${base.toLowerCase()}@2x.png`;
    }

    /** Create an <img> element with fallback to a letter avatar */
    function coinIcon(base, size = 24) {
        const img = document.createElement("img");
        img.className = "coin-icon";
        img.width = size;
        img.height = size;
        img.alt = base;
        img.src = coinIconUrl(base);
        img.loading = "lazy";
        img.onerror = function () {
            // Replace with letter avatar on error
            this.onerror = null;
            this.style.display = "none";
            const span = document.createElement("span");
            span.className = "coin-icon-fallback";
            span.style.width = size + "px";
            span.style.height = size + "px";
            span.style.fontSize = (size * 0.5) + "px";
            span.textContent = base.slice(0, 2);
            this.parentNode.insertBefore(span, this.nextSibling);
        };
        return img;
    }

    /* ── Price cards ───────────────────────────────────────────────────── */

    function updatePriceCard(symbol, price) {
        if (!symbol) return;
        const prev = priceData[symbol];
        const direction = prev ? (price >= prev.price ? "up" : "down") : "up";
        priceData[symbol] = { price: price, prevPrice: prev ? prev.price : price };

        // Store history for chart
        if (!priceHistory[symbol]) priceHistory[symbol] = [];
        const now = Date.now() / 1000; // lightweight-charts uses seconds
        priceHistory[symbol].push({ time: now, value: price });
        if (priceHistory[symbol].length > MAX_HISTORY) {
            priceHistory[symbol] = priceHistory[symbol].slice(-MAX_HISTORY);
        }

        const base = extractBase(symbol);

        let card = document.getElementById(`pc-${symbol}`);
        if (!card) {
            card = document.createElement("div");
            card.className = "price-card";
            card.id = `pc-${symbol}`;
            card.dataset.symbol = symbol;

            const header = document.createElement("div");
            header.className = "card-header-row";
            header.appendChild(coinIcon(base, 28));
            const info = document.createElement("div");
            info.className = "card-header-info";
            info.innerHTML = `<span class="symbol-name">${base}</span><span class="symbol-pair">${symbol}</span>`;
            header.appendChild(info);

            card.appendChild(header);
            card.insertAdjacentHTML("beforeend", `
                <div class="price"></div>
                <div class="meta"></div>`);
            priceCardsDiv.appendChild(card);

            // Click to open chart
            card.addEventListener("click", () => openChart(symbol));
        }

        const priceEl = card.querySelector(".price");
        priceEl.textContent = price.toLocaleString(undefined, {
            minimumFractionDigits: 2,
            maximumFractionDigits: 8,
        });
        priceEl.className = `price ${direction}`;

        card.querySelector(".meta").textContent = `Last update: ${new Date().toLocaleTimeString()}`;

        // Update chart in real-time if this symbol is selected
        // (kline WS updates are handled separately in handleKlineUpdate)
        if (symbol === selectedSymbol && candleSeries && chartType === "line") {
            // For line mode, update from trade data
            candleSeries.update({ time: Math.floor(now), value: price });
        }
    }

    /* ── Chart ─────────────────────────────────────────────────────────── */

    const BINANCE_API = "https://api.binance.com/api/v3";

    const chartWrapper   = document.getElementById("chart-wrapper");
    const chartContainer = document.getElementById("chart-container");
    const chartTitle     = document.getElementById("chart-title");
    const chartPriceEl   = document.getElementById("chart-price");
    const chartChangeEl  = document.getElementById("chart-change");
    const chartVolLabel  = document.getElementById("chart-vol-label");
    const chartOhlcEl    = document.getElementById("chart-ohlc");
    const btnCloseChart  = document.getElementById("btn-close-chart");
    const btnChartCandle = document.getElementById("btn-chart-candle");
    const btnChartLine   = document.getElementById("btn-chart-line");
    const chartTfBtns    = document.querySelectorAll(".chart-tf");

    // Pair bar elements
    const pairIcon        = document.getElementById("pair-icon");
    const pairNameEl      = document.getElementById("pair-name");
    const pairFullnameEl  = document.getElementById("pair-fullname");
    const pairBarPriceEl  = document.getElementById("pair-bar-price");
    const statChangeEl    = document.getElementById("stat-change");
    const statHighEl      = document.getElementById("stat-high");
    const statLowEl       = document.getElementById("stat-low");
    const statVolEl       = document.getElementById("stat-vol");

    // Order book
    const obAsks         = document.getElementById("ob-asks");
    const obBids         = document.getElementById("ob-bids");
    const obSpreadPrice  = document.getElementById("ob-spread-price");

    // Market trades
    const marketTradesDiv = document.getElementById("market-trades");

    // Trade form
    const tradePriceEl   = document.getElementById("trade-price");
    const tradeAmountEl  = document.getElementById("trade-amount");
    const tradeTotalEl   = document.getElementById("trade-total");
    const btnTradeSubmit = document.getElementById("btn-trade-submit");
    const btnSideBuy     = document.getElementById("btn-side-buy");
    const btnSideSell    = document.getElementById("btn-side-sell");
    const tradeQuoteLabel= document.getElementById("trade-quote-label");
    const tradeBaseLabel = document.getElementById("trade-base-label");
    let tradeSide = "buy";

    // USD helpers
    const priceUsdHint   = document.getElementById("price-usd-hint");
    const amountUsdHint  = document.getElementById("amount-usd-hint");
    const totalUsdHint   = document.getElementById("total-usd-hint");
    const nativeCostRow  = document.getElementById("native-cost-row");
    const nativeCostVal  = document.getElementById("native-cost-value");
    const btnSyncPrice   = document.getElementById("btn-sync-price");

    let liveMarketPrice  = 0;   // Last USDT price from Binance WS for selectedSymbol
    let nativeUsdRate    = 0;   // e.g. BNB/USDT rate
    let syncPriceAuto    = true; // auto-update Price field from WS

    /** Fetch native token USD rate from Binance */
    async function fetchNativeUsdRate() {
        try {
            const cfg = (typeof TxModule !== "undefined" && TxModule.getConfig) ? TxModule.getConfig() : null;
            const sym = cfg ? cfg.symbol : "BNB";
            const pair = sym === "ETH" ? "ETHUSDT" : sym + "USDT";
            const res = await fetch(`${BINANCE_API}/ticker/price?symbol=${pair}`);
            if (res.ok) {
                const d = await res.json();
                nativeUsdRate = parseFloat(d.price) || 0;
            }
        } catch (_) { /* ignore */ }
    }

    /** Format a number as USD */
    function fmtUsd(v) {
        if (v == null || !isFinite(v)) return "";
        return `≈ $${v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })} USD`;
    }

    /** Update all USD hints in the trade form */
    function updateUsdHints() {
        const price  = parseFloat(tradePriceEl?.value) || 0;
        const amount = parseFloat(tradeAmountEl?.value) || 0;
        const total  = price * amount;

        // For pairs like XXXUSDT, price IS in USD; for XXXBTC etc. we need conversion
        const quote = (selectedSymbol || "").replace(extractBase(selectedSymbol || "BTCUSDT"), "").toUpperCase();
        const isUsdQuote = ["USDT","USDC","BUSD","DAI","TUSD","FDUSD"].includes(quote);

        // Price → show "≈ $X USD" (only if quote is NOT already usd)
        if (priceUsdHint) {
            if (isUsdQuote) {
                priceUsdHint.textContent = price > 0 ? fmtUsd(price) : "";
            } else {
                priceUsdHint.textContent = ""; // Would need cross-rate — skip for now
            }
        }

        // Amount → show "≈ $X USD" based on price
        if (amountUsdHint) {
            if (isUsdQuote && total > 0) {
                amountUsdHint.textContent = fmtUsd(total);
            } else {
                amountUsdHint.textContent = "";
            }
        }

        // Total → same as amount×price, show in USD + native cost
        if (totalUsdHint) {
            if (isUsdQuote && total > 0) {
                totalUsdHint.textContent = fmtUsd(total);
            } else {
                totalUsdHint.textContent = "";
            }
        }

        // Native cost: how many BNB/ETH/MATIC this trade costs (swap + gas)
        if (nativeCostRow && nativeCostVal) {
            if (total > 0 && nativeUsdRate > 0 && isUsdQuote) {
                const nativeCost = total / nativeUsdRate;
                const gasBuf     = 0.005;
                const totalNeed  = nativeCost + gasBuf;
                const cfg = (typeof TxModule !== "undefined" && TxModule.getConfig) ? TxModule.getConfig() : null;
                const sym = cfg ? cfg.symbol : "—";
                nativeCostVal.textContent = `≈ ${nativeCost.toFixed(6)} ${sym}  +  ~${gasBuf} ${sym} gas  =  ~${totalNeed.toFixed(6)} ${sym}`;
                nativeCostRow.classList.remove("hidden");
            } else {
                nativeCostRow.classList.add("hidden");
            }
        }
    }

    /** Sync price field with live Binance market price */
    function syncLivePrice() {
        if (liveMarketPrice > 0 && tradePriceEl) {
            tradePriceEl.value = liveMarketPrice;
            calcTradeTotal();
            updateUsdHints();
        }
    }

    // Btn "↻ Market" → force fill current price
    if (btnSyncPrice) {
        btnSyncPrice.addEventListener("click", () => {
            syncPriceAuto = true;
            syncLivePrice();
        });
    }
    // If user manually edits price → stop auto-sync
    if (tradePriceEl) {
        tradePriceEl.addEventListener("focus", () => { syncPriceAuto = false; });
    }

    /** Open chart for a symbol */
    function openChart(symbol) {
        selectedSymbol = symbol;
        liveMarketPrice = 0;
        syncPriceAuto = true;
        // Clear price field for new pair
        if (tradePriceEl) tradePriceEl.value = "";
        if (tradeAmountEl) tradeAmountEl.value = "";
        if (tradeTotalEl) tradeTotalEl.value = "";
        if (priceUsdHint) priceUsdHint.textContent = "";
        if (amountUsdHint) amountUsdHint.textContent = "";
        if (totalUsdHint) totalUsdHint.textContent = "";
        if (nativeCostRow) nativeCostRow.classList.add("hidden");
        hideTradeStatus();

        document.querySelectorAll(".price-card").forEach(c =>
            c.classList.toggle("selected", c.dataset.symbol === symbol)
        );

        // Update pair bar identity
        const base = extractBase(symbol);
        const quote = symbol.replace(base, "");

        if (pairIcon) {
            pairIcon.src = coinIconUrl(base);
            pairIcon.alt = base;
            pairIcon.style.display = "";
            pairIcon.onerror = function () {
                this.style.display = "none";
            };
        }
        if (pairNameEl)     pairNameEl.textContent = `${base}/${quote}`;
        if (pairFullnameEl) pairFullnameEl.textContent = symbol;

        // Update chart hidden title for compat
        chartTitle.innerHTML = "";
        chartTitle.appendChild(coinIcon(base, 22));
        const titleText = document.createElement("span");
        titleText.textContent = ` ${symbol}`;
        chartTitle.appendChild(titleText);

        // Update trade form labels
        if (tradeQuoteLabel) tradeQuoteLabel.textContent = quote || "USDT";
        if (tradeBaseLabel)  tradeBaseLabel.textContent = base;

        buildChart();
        fetchAndRenderKlines(symbol, chartInterval);

        // Subscribe to ticker + depth + trade streams for the pair
        if (binanceWs && binanceWs.readyState === WebSocket.OPEN) {
            const pair = symbol.toLowerCase();
            binanceWs.send(JSON.stringify({
                action: "subscribe",
                streams: [`${pair}@ticker`, `${pair}@depth@100ms`, `${pair}@trade`]
            }));
        }

        // Fetch initial 24h ticker
        fetch24hTicker(symbol);
        // Fetch initial order book snapshot
        fetchOrderBookSnapshot(symbol);
    }

    /** Build or rebuild the lightweight-charts instance */
    function buildChart() {
        if (chart) { chart.remove(); chart = null; candleSeries = null; volumeSeries = null; }

        chart = LightweightCharts.createChart(chartContainer, {
            width: chartContainer.clientWidth,
            height: chartContainer.clientHeight || 400,
            layout: { background: { color: "#181A20" }, textColor: "#848E9C" },
            grid: {
                vertLines: { color: "rgba(43,49,57,0.5)" },
                horzLines: { color: "rgba(43,49,57,0.5)" },
            },
            crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
            rightPriceScale: { borderColor: "#2B3139" },
            timeScale: { borderColor: "#2B3139", timeVisible: true, secondsVisible: chartInterval === "1m" },
        });

        // Volume histogram (always shown)
        volumeSeries = chart.addHistogramSeries({
            priceFormat: { type: "volume" },
            priceScaleId: "vol",
            color: "rgba(240,185,11,0.25)",
        });
        chart.priceScale("vol").applyOptions({
            scaleMargins: { top: 0.85, bottom: 0 },
        });

        if (chartType === "candle") {
            candleSeries = chart.addCandlestickSeries({
                upColor: "#0ECB81",
                downColor: "#F6465D",
                borderUpColor: "#0ECB81",
                borderDownColor: "#F6465D",
                wickUpColor: "#0ECB81",
                wickDownColor: "#F6465D",
            });
        } else {
            candleSeries = chart.addBaselineSeries({
                baseValue: { type: "price", price: 0 },
                topLineColor: "#0ECB81",
                topFillColor1: "rgba(14,203,129,0.18)",
                topFillColor2: "rgba(14,203,129,0.02)",
                bottomLineColor: "#F6465D",
                bottomFillColor1: "rgba(246,70,93,0.02)",
                bottomFillColor2: "rgba(246,70,93,0.18)",
                lineWidth: 2,
            });
        }

        // Crosshair move → show OHLC
        chart.subscribeCrosshairMove(param => {
            if (!param || !param.time) {
                chartOhlcEl.textContent = "O — H — L — C —";
                return;
            }
            const d = param.seriesData.get(candleSeries);
            if (!d) return;
            if (d.open !== undefined) {
                chartOhlcEl.textContent = `O ${d.open.toFixed(2)}  H ${d.high.toFixed(2)}  L ${d.low.toFixed(2)}  C ${d.close.toFixed(2)}`;
            } else if (d.value !== undefined) {
                chartOhlcEl.textContent = `Price ${d.value.toFixed(4)}`;
            }
        });

        // Responsive
        const ro = new ResizeObserver(() => {
            if (chart) chart.applyOptions({
                width: chartContainer.clientWidth,
                height: chartContainer.clientHeight || 400,
            });
        });
        ro.observe(chartContainer);
    }

    /**
     * Fetch historical klines from Binance REST API and render them.
     * https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1m&limit=500
     */
    async function fetchAndRenderKlines(symbol, interval) {
        if (!candleSeries) return;

        chartPriceEl.textContent = "Loading…";
        chartChangeEl.textContent = "";

        try {
            const pair = symbol.replace("/", "");  // "BTCUSDT"
            const url = `${BINANCE_API}/klines?symbol=${pair}&interval=${interval}&limit=500`;
            const res = await fetch(url);
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            const raw = await res.json();

            /*  Binance kline array:
                [0] openTime, [1] open, [2] high, [3] low, [4] close,
                [5] volume, [6] closeTime, ...
            */
            const candles = [];
            const volumes = [];

            for (const k of raw) {
                const time = Math.floor(k[0] / 1000); // ms → s
                const o = parseFloat(k[1]);
                const h = parseFloat(k[2]);
                const l = parseFloat(k[3]);
                const c = parseFloat(k[4]);
                const v = parseFloat(k[5]);

                if (chartType === "candle") {
                    candles.push({ time, open: o, high: h, low: l, close: c });
                } else {
                    candles.push({ time, value: c });
                }
                volumes.push({
                    time,
                    value: v,
                    color: c >= o ? "rgba(14,203,129,0.35)" : "rgba(246,70,93,0.35)",
                });
            }

            if (chartType === "line" && candles.length > 0) {
                chartOpenPrice = candles[0].value;
                candleSeries.applyOptions({ baseValue: { type: "price", price: chartOpenPrice } });
            } else if (candles.length > 0) {
                chartOpenPrice = candles[0].open;
            }

            candleSeries.setData(candles);
            volumeSeries.setData(volumes);
            chart.timeScale().fitContent();

            // Update header info
            if (candles.length > 0) {
                const last = candles[candles.length - 1];
                const lastPrice = chartType === "candle" ? last.close : last.value;
                updateChartHeader(lastPrice);
            }

            // Subscribe to real-time kline WS stream
            subscribeChartKline(pair.toLowerCase(), interval);

        } catch (err) {
            console.error("Kline fetch error:", err);
            chartPriceEl.textContent = "Error loading data";
        }
    }

    /** Subscribe Binance WS to the matching kline stream for live candle updates */
    function subscribeChartKline(pair, interval) {
        if (binanceWs && binanceWs.readyState === WebSocket.OPEN) {
            const stream = `${pair}@kline_${interval}`;
            // Subscribe (server handles duplicates)
            binanceWs.send(JSON.stringify({ action: "subscribe", streams: [stream] }));
        }
    }

    /** Handle real-time kline update from WebSocket */
    function handleKlineUpdate(data) {
        const k = data.k;
        if (!k || !candleSeries) return;

        const sym = (data.s || "").toUpperCase();
        if (sym !== selectedSymbol) return;

        // Check interval matches
        if (k.i !== chartInterval) return;

        const time = Math.floor(k.t / 1000);
        const o = parseFloat(k.o);
        const h = parseFloat(k.h);
        const l = parseFloat(k.l);
        const c = parseFloat(k.c);
        const v = parseFloat(k.v);

        if (chartType === "candle") {
            candleSeries.update({ time, open: o, high: h, low: l, close: c });
        } else {
            candleSeries.update({ time, value: c });
        }

        volumeSeries.update({
            time,
            value: v,
            color: c >= o ? "rgba(14,203,129,0.35)" : "rgba(246,70,93,0.35)",
        });

        updateChartHeader(c);
    }

    /** Update the chart header price + change % */
    function updateChartHeader(currentPrice) {
        chartPriceEl.textContent = currentPrice.toLocaleString(undefined, {
            minimumFractionDigits: 2,
            maximumFractionDigits: 8,
        });

        if (chartOpenPrice > 0) {
            const changePct = ((currentPrice - chartOpenPrice) / chartOpenPrice * 100).toFixed(2);
            const isUp = currentPrice >= chartOpenPrice;
            chartChangeEl.textContent = `${isUp ? "+" : ""}${changePct}%`;
            chartChangeEl.className = `chart-change ${isUp ? "up" : "down"}`;
            chartPriceEl.className = `chart-live-price ${isUp ? "up" : "down"}`;

            // Also update pair bar price
            if (pairBarPriceEl) {
                pairBarPriceEl.textContent = currentPrice.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 8 });
                pairBarPriceEl.className = "pair-price" + (isUp ? "" : " down");
            }
        }
    }

    // Timeframe buttons
    chartTfBtns.forEach(btn => {
        btn.addEventListener("click", () => {
            chartTfBtns.forEach(b => b.classList.toggle("active", b === btn));
            chartInterval = btn.dataset.interval;

            // Adjust seconds visibility
            if (chart) {
                chart.applyOptions({
                    timeScale: { secondsVisible: chartInterval === "1m" },
                });
            }

            if (selectedSymbol) fetchAndRenderKlines(selectedSymbol, chartInterval);
        });
    });

    // Chart type toggle
    if (btnChartCandle) {
        btnChartCandle.addEventListener("click", () => {
            chartType = "candle";
            btnChartCandle.classList.add("active");
            btnChartLine.classList.remove("active");
            buildChart();
            if (selectedSymbol) fetchAndRenderKlines(selectedSymbol, chartInterval);
        });
    }
    if (btnChartLine) {
        btnChartLine.addEventListener("click", () => {
            chartType = "line";
            btnChartLine.classList.add("active");
            btnChartCandle.classList.remove("active");
            buildChart();
            if (selectedSymbol) fetchAndRenderKlines(selectedSymbol, chartInterval);
        });
    }

    // Close chart (disabled in new layout — chart is always visible)
    // Keep ref for backward compat
    if (btnCloseChart) {
        btnCloseChart.addEventListener("click", () => {
            // No-op in exchange layout
        });
    }

    /* ── Buy / Sell side tabs ──────────────────────────────────────────── */
    const tradeEstimateRow = document.getElementById("trade-estimate-row");
    const tradeEstimateEl  = document.getElementById("trade-estimate");
    const tradeStatusEl    = document.getElementById("trade-status");
    const tradeSlipCustom  = document.getElementById("trade-slippage-custom");
    const tradeModal       = document.getElementById("trade-modal");
    const tmTitle          = document.getElementById("tm-title");
    const tmSide           = document.getElementById("tm-side");
    const tmPair           = document.getElementById("tm-pair");
    const tmAmountIn       = document.getElementById("tm-amount-in");
    const tmAmountOut      = document.getElementById("tm-amount-out");
    const tmSlippage       = document.getElementById("tm-slippage");
    const tmDex            = document.getElementById("tm-dex");
    const tmConfirm        = document.getElementById("tm-confirm");
    const tmCancel         = document.getElementById("tm-cancel");
    const tmClose          = document.getElementById("tm-close");
    let tradeSlippage = 1; // default 1%
    let _pendingTrade = null; // pending trade data for confirmation

    if (btnSideBuy) {
        btnSideBuy.addEventListener("click", () => {
            tradeSide = "buy";
            btnSideBuy.classList.add("active");
            btnSideSell.classList.remove("active");
            btnTradeSubmit.textContent = "Buy";
            btnTradeSubmit.className = "btn btn-trade btn-buy-green";
            updateTradeEstimate();
        });
    }
    if (btnSideSell) {
        btnSideSell.addEventListener("click", () => {
            tradeSide = "sell";
            btnSideSell.classList.add("active");
            btnSideBuy.classList.remove("active");
            btnTradeSubmit.textContent = "Sell";
            btnTradeSubmit.className = "btn btn-trade btn-sell-red";
            updateTradeEstimate();
        });
    }

    /* ── Slippage selector ─────────────────────────────────────────────── */
    document.querySelectorAll(".tf-slip").forEach(btn => {
        btn.addEventListener("click", () => {
            tradeSlippage = parseFloat(btn.dataset.slip);
            document.querySelectorAll(".tf-slip").forEach(b => b.classList.remove("active"));
            btn.classList.add("active");
            if (tradeSlipCustom) tradeSlipCustom.value = "";
        });
    });
    if (tradeSlipCustom) {
        tradeSlipCustom.addEventListener("input", () => {
            const v = parseFloat(tradeSlipCustom.value);
            if (v > 0) {
                tradeSlippage = Math.min(v, 50);
                document.querySelectorAll(".tf-slip").forEach(b => b.classList.remove("active"));
            }
        });
    }

    // Trade form: auto-calculate total = price × amount
    function calcTradeTotal() {
        const p = parseFloat(tradePriceEl?.value) || 0;
        const a = parseFloat(tradeAmountEl?.value) || 0;
        if (tradeTotalEl) tradeTotalEl.value = (p * a).toFixed(6);
        updateUsdHints();
    }
    if (tradePriceEl)  tradePriceEl.addEventListener("input", () => { calcTradeTotal(); debouncedEstimate(); });
    if (tradeAmountEl) tradeAmountEl.addEventListener("input", () => { calcTradeTotal(); debouncedEstimate(); });

    /* ── Percentage buttons (real balance) ─────────────────────────────── */
    document.querySelectorAll(".tf-pct").forEach(btn => {
        btn.addEventListener("click", async () => {
            const pct = parseInt(btn.dataset.pct) / 100;
            // Highlight
            document.querySelectorAll(".tf-pct").forEach(b => b.classList.remove("active"));
            btn.classList.add("active");
            setTimeout(() => btn.classList.remove("active"), 600);

            if (!TxModule || !ethersProvider) return;
            const base = extractBase(selectedSymbol || "BTCUSDT");
            const quote = (selectedSymbol || "BTCUSDT").replace(base, "");

            try {
                if (tradeSide === "buy") {
                    // Buy: we spend native (or quote token). Use native balance.
                    const bal = await TxModule.getNativeBalance();
                    const avail = parseFloat(bal) * pct;
                    const price = parseFloat(tradePriceEl?.value) || 0;
                    if (price > 0) {
                        tradeAmountEl.value = (avail / price).toFixed(8);
                    } else {
                        // No price set → fill total as native amount
                        tradeAmountEl.value = avail.toFixed(8);
                    }
                } else {
                    // Sell: we spend the base token
                    const addr = resolveTokenAddress(base);
                    if (addr === null) return;
                    if (addr === "") {
                        // Selling native
                        const bal = await TxModule.getNativeBalance();
                        tradeAmountEl.value = (parseFloat(bal) * pct).toFixed(8);
                    } else {
                        // Selling ERC-20 token
                        const info = await TxModule.getTokenInfo(addr);
                        tradeAmountEl.value = (parseFloat(info.balance) * pct).toFixed(8);
                    }
                }
                calcTradeTotal();
                debouncedEstimate();
            } catch (e) {
                console.warn("Pct balance error:", e);
            }
        });
    });

    /* ── Swap estimate ─────────────────────────────────────────────────── */
    let _estimateTimer = null;
    function debouncedEstimate() {
        clearTimeout(_estimateTimer);
        _estimateTimer = setTimeout(updateTradeEstimate, 500);
    }

    /**
     * Convert a USD-denominated total to native token amount.
     * e.g. $7.14 USD → 0.0119 BNB (if BNB=$600)
     */
    function usdToNative(usdAmount) {
        if (nativeUsdRate > 0) {
            return (usdAmount / nativeUsdRate).toFixed(8);
        }
        return null; // rate not available
    }

    async function updateTradeEstimate() {
        if (!TxModule || !selectedSymbol) return;
        const amount = parseFloat(tradeAmountEl?.value);
        if (!amount || amount <= 0) {
            if (tradeEstimateRow) tradeEstimateRow.classList.add("hidden");
            return;
        }

        const base = extractBase(selectedSymbol);
        const quote = selectedSymbol.replace(base, "");
        const cfg = TxModule.getConfig();

        let tokenIn, tokenOut, amtStr;
        if (tradeSide === "buy") {
            // Buy base with native: native → base token
            tokenIn  = "";  // native
            tokenOut = resolveTokenAddress(base);
            if (tokenOut === null || tokenOut === "") {
                if (tradeEstimateRow) tradeEstimateRow.classList.add("hidden");
                return;
            }
            // Convert USD cost → native amount (e.g. $7.14 → 0.012 BNB)
            const price = parseFloat(tradePriceEl?.value) || 0;
            const totalUsd = amount * price;
            if (totalUsd > 0 && nativeUsdRate > 0) {
                amtStr = usdToNative(totalUsd);
            } else {
                // Fallback: use amount directly (user may be specifying native)
                amtStr = amount.toString();
            }
        } else {
            // Sell base for native: base token → wrapped native
            tokenIn = resolveTokenAddress(base);
            if (tokenIn === null) {
                if (tradeEstimateRow) tradeEstimateRow.classList.add("hidden");
                return;
            }
            tokenOut = cfg.wrapped; // get native back
            amtStr = amount.toString();
            if (tokenIn === "") {
                // Selling native for... can't sell native as token
                if (tradeEstimateRow) tradeEstimateRow.classList.add("hidden");
                return;
            }
        }

        try {
            const est = await TxModule.getSwapEstimate(tokenIn, tokenOut, amtStr);
            if (tradeEstimateRow) tradeEstimateRow.classList.remove("hidden");
            if (tradeEstimateEl) {
                tradeEstimateEl.textContent = `≈ ${parseFloat(est.amountOut).toFixed(6)} ${est.symbol}`;
            }
        } catch (e) {
            if (tradeEstimateRow) tradeEstimateRow.classList.remove("hidden");
            if (tradeEstimateEl) tradeEstimateEl.textContent = "—";
            console.warn("Estimate error:", e);
        }
    }

    /* ── Trade status helpers ──────────────────────────────────────────── */
    function showTradeStatus(msg, type) {
        if (!tradeStatusEl) return;
        tradeStatusEl.textContent = msg;
        tradeStatusEl.style.whiteSpace = msg.includes('\n') ? 'pre-line' : '';
        tradeStatusEl.className = `tf-trade-status status-${type}`;
        tradeStatusEl.classList.remove("hidden");
    }
    function hideTradeStatus() {
        if (tradeStatusEl) tradeStatusEl.classList.add("hidden");
    }

    /* ── Trade Submit → Confirmation Modal ─────────────────────────────── */
    if (btnTradeSubmit) {
        btnTradeSubmit.addEventListener("click", async () => {
            hideTradeStatus();

            if (!TxModule || !ethersSigner) {
                showTradeStatus("Connect wallet first", "error");
                return;
            }

            const amount = parseFloat(tradeAmountEl?.value);
            if (!amount || amount <= 0) {
                showTradeStatus("Enter a valid amount", "error");
                return;
            }

            const base = extractBase(selectedSymbol || "BTCUSDT");
            const cfg = TxModule.getConfig();

            // Validate & auto-switch chain connection
            try {
                const network = await ethersProvider.getNetwork();
                const connectedChain = Number(network.chainId);
                if (connectedChain !== wallet.chain_id) {
                    showTradeStatus(`Switching to ${CHAINS[wallet.chain_id] || 'chain ' + wallet.chain_id}…`, "pending");
                    const switched = await switchToChain(wallet.chain_id);
                    if (!switched) {
                        showTradeStatus(`Please switch your wallet to ${CHAINS[wallet.chain_id] || 'chain ' + wallet.chain_id} and try again.`, "error");
                        return;
                    }
                    // Rebuild provider from scratch (ethers caches old network)
                    await rebuildProvider();
                    TxModule.init({
                        signer: ethersSigner, provider: ethersProvider,
                        address: wallet.address, chainId: wallet.chain_id, onLog: txLog,
                    });
                    hideTradeStatus();
                }
            } catch (e) {
                showTradeStatus("Cannot reach blockchain. Check your wallet connection.", "error");
                return;
            }

            let tokenIn, tokenOut, amtStr, amtLabel;
            if (tradeSide === "buy") {
                tokenIn  = "";
                tokenOut = resolveTokenAddress(base);
                if (tokenOut === null) {
                    showTradeStatus(`Token ${base} not available on chain ${wallet.chain_id}`, "error");
                    return;
                }
                if (tokenOut === "") {
                    showTradeStatus(`Cannot buy native token ${base} with native`, "error");
                    return;
                }
                // Convert USD cost → native amount
                const price = parseFloat(tradePriceEl?.value) || 0;
                const totalUsd = amount * price;
                if (totalUsd > 0 && nativeUsdRate > 0) {
                    amtStr = usdToNative(totalUsd);
                } else if (nativeUsdRate <= 0) {
                    showTradeStatus("Native token USD rate not available. Try again in a moment.", "error");
                    return;
                } else {
                    amtStr = amount.toString();
                }
                amtLabel = `${amtStr} ${cfg.symbol} (${fmtUsd(totalUsd)})`;
            } else {
                tokenIn = resolveTokenAddress(base);
                if (tokenIn === null) {
                    showTradeStatus(`Token ${base} not available on this chain`, "error");
                    return;
                }
                if (tokenIn === "") {
                    showTradeStatus(`Cannot sell native ${base} via DEX swap in this mode`, "error");
                    return;
                }
                tokenOut = cfg.wrapped;
                amtStr = amount.toString();
                amtLabel = `${amtStr} ${base}`;
            }

            // Get estimate before showing modal
            showTradeStatus("Fetching estimate...", "pending");
            btnTradeSubmit.disabled = true;
            try {
                // Pre-check: verify balance covers the swap amount + gas
                if (tradeSide === "buy") {
                    try {
                        const nativeBal = parseFloat(await TxModule.getNativeBalance());
                        const swapAmt   = parseFloat(amtStr);
                        const gasBuffer = 0.005; // ~0.005 BNB for gas
                        if (swapAmt + gasBuffer > nativeBal) {
                            const swapUsd = swapAmt * nativeUsdRate;
                            const gasUsd  = gasBuffer * nativeUsdRate;
                            const totalNeeded = swapAmt + gasBuffer;
                            showTradeStatus(
                                `Insufficient ${cfg.symbol}. You need:\n` +
                                `• Swap: ${swapAmt.toFixed(6)} ${cfg.symbol} (${fmtUsd(swapUsd)}) — cost of the token\n` +
                                `• Gas fee: ~${gasBuffer} ${cfg.symbol} (${fmtUsd(gasUsd)}) — network fee\n` +
                                `• Total: ~${totalNeeded.toFixed(6)} ${cfg.symbol} (${fmtUsd(swapUsd + gasUsd)})\n` +
                                `• Your balance: ${nativeBal.toFixed(6)} ${cfg.symbol} (${fmtUsd(nativeBal * nativeUsdRate)})`,
                                "error"
                            );
                            btnTradeSubmit.disabled = false;
                            return;
                        }
                    } catch (_) { /* non-blocking — swap will fail anyway */ }
                }

                const est = await TxModule.getSwapEstimate(tokenIn, tokenOut, amtStr);
                hideTradeStatus();

                // Populate modal
                const tmUsdValue = document.getElementById("tm-usd-value");
                tmTitle.textContent = `Confirm ${tradeSide === "buy" ? "Buy" : "Sell"}`;
                tmSide.textContent = tradeSide === "buy" ? "Buy" : "Sell";
                tmSide.style.color = tradeSide === "buy" ? "var(--green)" : "var(--red)";
                tmPair.textContent = selectedSymbol;
                tmAmountIn.textContent = amtLabel;
                tmAmountOut.textContent = `≈ ${parseFloat(est.amountOut).toFixed(6)} ${est.symbol}`;
                tmSlippage.textContent = `${tradeSlippage}%`;
                tmDex.textContent = cfg.name;

                // Calculate USD value for the modal
                const tradePrice = parseFloat(tradePriceEl?.value) || 0;
                const tradeTotal = tradePrice * amount;
                const quoteSym = (selectedSymbol || "").replace(base, "").toUpperCase();
                const isUsdQ = ["USDT","USDC","BUSD","DAI","TUSD","FDUSD"].includes(quoteSym);
                if (tmUsdValue) {
                    if (isUsdQ && tradeTotal > 0) {
                        tmUsdValue.textContent = fmtUsd(tradeTotal);
                    } else if (nativeUsdRate > 0 && parseFloat(amtStr) > 0) {
                        tmUsdValue.textContent = fmtUsd(parseFloat(amtStr) * nativeUsdRate);
                    } else {
                        tmUsdValue.textContent = "—";
                    }
                }

                tmConfirm.className = `btn btn-trade-modal btn-confirm${tradeSide === "sell" ? " sell" : ""}`;
                tmConfirm.textContent = tradeSide === "buy" ? "Confirm Buy" : "Confirm Sell";

                // Store pending trade
                _pendingTrade = { tokenIn, tokenOut, amtStr, slippage: tradeSlippage, est };

                // Show modal
                tradeModal.classList.remove("hidden");
            } catch (e) {
                const msg = e.message || String(e);
                if (msg.includes("CALL_EXCEPTION") || msg.includes("data=null")) {
                    showTradeStatus(
                        `DEX call failed. Ensure your wallet is on the correct network (Chain ID: ${wallet.chain_id}). ` +
                        `The DEX router may not exist on your current chain.`,
                        "error"
                    );
                } else {
                    showTradeStatus(`Estimate failed: ${msg}`, "error");
                }
            } finally {
                btnTradeSubmit.disabled = false;
            }
        });
    }

    /* ── Modal actions ─────────────────────────────────────────────────── */
    function closeTradeModal() {
        if (tradeModal) tradeModal.classList.add("hidden");
        _pendingTrade = null;
    }
    if (tmCancel) tmCancel.addEventListener("click", closeTradeModal);
    if (tmClose)  tmClose.addEventListener("click", closeTradeModal);
    if (tradeModal) {
        tradeModal.addEventListener("click", (e) => {
            if (e.target === tradeModal) closeTradeModal();
        });
    }

    if (tmConfirm) {
        tmConfirm.addEventListener("click", async () => {
            if (!_pendingTrade) return;
            const { tokenIn, tokenOut, amtStr, slippage } = _pendingTrade;
            closeTradeModal();

            showTradeStatus("Sending transaction...", "pending");
            btnTradeSubmit.disabled = true;
            btnTradeSubmit.textContent = "Processing...";

            try {
                const receipt = await TxModule.swap(tokenIn, tokenOut, amtStr, slippage);
                showTradeStatus(`Trade confirmed! Block #${receipt.blockNumber}`, "success");
                // Refresh balance
                refreshBalance();
                // Clear form
                if (tradeAmountEl) tradeAmountEl.value = "";
                if (tradeTotalEl)  tradeTotalEl.value = "";
                if (tradeEstimateRow) tradeEstimateRow.classList.add("hidden");
            } catch (e) {
                let msg = e.reason || e.shortMessage || e.message || String(e);
                // User-friendly messages for common errors
                if (e.code === "CALL_EXCEPTION" || msg.includes("CALL_EXCEPTION")) {
                    if (msg.includes("estimateGas")) {
                        msg = `Transaction would revert. Most likely your ${TxModule.getConfig().symbol} balance is too low to cover the swap amount + gas fee. ` +
                              `Check your balance and try a smaller amount.`;
                    } else {
                        msg = `Smart contract call reverted. The swap may have failed due to insufficient liquidity, high slippage, or wrong token pair.`;
                    }
                } else if (e.code === "INSUFFICIENT_FUNDS" || msg.includes("insufficient funds")) {
                    msg = `Insufficient ${TxModule.getConfig().symbol} to cover this trade + gas. Add funds to your wallet.`;
                } else if (e.code === "ACTION_REJECTED" || e.code === 4001) {
                    msg = "Transaction rejected in wallet.";
                }
                showTradeStatus(`Trade failed: ${msg}`, "error");
            } finally {
                btnTradeSubmit.disabled = false;
                btnTradeSubmit.textContent = tradeSide === "buy" ? "Buy" : "Sell";
            }
        });
    }

    /* ── 24h Ticker fetch (REST) ───────────────────────────────────────── */
    async function fetch24hTicker(symbol) {
        try {
            const res = await fetch(`${BINANCE_API}/ticker/24hr?symbol=${symbol}`);
            if (!res.ok) return;
            const d = await res.json();
            updatePairBarStats(d);
        } catch (e) {
            console.warn("24h ticker fetch failed:", e);
        }
    }

    function updatePairBarStats(d) {
        const lastPrice = parseFloat(d.lastPrice || d.c || 0);
        const change    = parseFloat(d.priceChangePercent || d.P || 0);
        const high      = parseFloat(d.highPrice || d.h || 0);
        const low       = parseFloat(d.lowPrice || d.l || 0);
        const vol       = parseFloat(d.volume || d.v || 0);

        if (pairBarPriceEl) {
            pairBarPriceEl.textContent = lastPrice.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 8 });
            pairBarPriceEl.className = "pair-price" + (change >= 0 ? "" : " down");
        }
        if (statChangeEl) {
            statChangeEl.textContent = `${change >= 0 ? "+" : ""}${change.toFixed(2)}%`;
            statChangeEl.className = "pstat-value " + (change >= 0 ? "up" : "down");
        }
        if (statHighEl)  statHighEl.textContent = high.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 8 });
        if (statLowEl)   statLowEl.textContent = low.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 8 });
        if (statVolEl)   statVolEl.textContent = vol.toLocaleString(undefined, { maximumFractionDigits: 2 });

        // Update trade price field & live market price
        liveMarketPrice = lastPrice;
        if (tradePriceEl && (!tradePriceEl.value || syncPriceAuto)) {
            tradePriceEl.value = lastPrice;
            syncPriceAuto = true;
            calcTradeTotal();
            updateUsdHints();
        }
    }

    /* ── Order Book ────────────────────────────────────────────────────── */

    async function fetchOrderBookSnapshot(symbol) {
        try {
            const res = await fetch(`${BINANCE_API}/depth?symbol=${symbol}&limit=15`);
            if (!res.ok) return;
            const data = await res.json();
            renderOrderBook(data.asks || [], data.bids || []);
        } catch (e) {
            console.warn("Order book fetch failed:", e);
        }
    }

    function renderOrderBook(asks, bids) {
        if (!obAsks || !obBids) return;

        // asks: [ [price, qty], … ] — show lowest at bottom (column-reverse CSS)
        const maxAskTotal = asks.reduce((s, a) => s + parseFloat(a[1]), 0) || 1;
        const maxBidTotal = bids.reduce((s, b) => s + parseFloat(b[1]), 0) || 1;

        obAsks.innerHTML = "";
        const displayAsks = asks.slice(0, 15);
        let askCum = 0;
        for (const [p, q] of displayAsks) {
            const price = parseFloat(p);
            const qty   = parseFloat(q);
            askCum += qty;
            const pct = (askCum / maxAskTotal * 100).toFixed(1);
            const row = document.createElement("div");
            row.className = "ob-row ask";
            row.innerHTML = `<span class="ob-price">${price.toFixed(getPricePrecision())}</span><span>${qty.toFixed(getQtyPrecision())}</span><span>${askCum.toFixed(getQtyPrecision())}</span><div class="ob-depth" style="width:${pct}%"></div>`;
            obAsks.appendChild(row);
        }

        obBids.innerHTML = "";
        let bidCum = 0;
        for (const [p, q] of bids.slice(0, 15)) {
            const price = parseFloat(p);
            const qty   = parseFloat(q);
            bidCum += qty;
            const pct = (bidCum / maxBidTotal * 100).toFixed(1);
            const row = document.createElement("div");
            row.className = "ob-row bid";
            row.innerHTML = `<span class="ob-price">${price.toFixed(getPricePrecision())}</span><span>${qty.toFixed(getQtyPrecision())}</span><span>${bidCum.toFixed(getQtyPrecision())}</span><div class="ob-depth" style="width:${pct}%"></div>`;
            obBids.appendChild(row);
        }

        // Spread
        if (asks.length && bids.length && obSpreadPrice) {
            const bestAsk = parseFloat(asks[0][0]);
            const bestBid = parseFloat(bids[0][0]);
            obSpreadPrice.textContent = bestBid.toFixed(getPricePrecision());
            obSpreadPrice.className = "ob-spread-price" + (bestBid >= (lastSpreadPrice || 0) ? "" : " down");
            lastSpreadPrice = bestBid;
        }

        // Click on OB row → fill trade price
        obAsks.querySelectorAll(".ob-row").forEach(r => {
            r.addEventListener("click", () => {
                if (tradePriceEl) { tradePriceEl.value = r.querySelector(".ob-price").textContent; calcTradeTotal(); }
            });
        });
        obBids.querySelectorAll(".ob-row").forEach(r => {
            r.addEventListener("click", () => {
                if (tradePriceEl) { tradePriceEl.value = r.querySelector(".ob-price").textContent; calcTradeTotal(); }
            });
        });
    }

    let lastSpreadPrice = 0;

    function getPricePrecision() {
        // Auto-detect from current price
        if (pairBarPriceEl && pairBarPriceEl.textContent && pairBarPriceEl.textContent !== "—") {
            const p = pairBarPriceEl.textContent.replace(/,/g, "");
            const dec = p.split(".")[1];
            return dec ? Math.min(dec.length, 8) : 2;
        }
        return 2;
    }
    function getQtyPrecision() {
        return 5;
    }

    /* ── Market Trades ─────────────────────────────────────────────────── */
    const MAX_MARKET_TRADES = 50;
    let lastTradePrice = 0;

    function addMarketTrade(price, qty, time, isBuyerMaker) {
        if (!marketTradesDiv) return;
        const row = document.createElement("div");
        const direction = price >= lastTradePrice ? "up" : "down";
        row.className = `mt-row ${direction}`;
        const t = new Date(time).toLocaleTimeString();
        row.innerHTML = `<span>${price.toFixed(getPricePrecision())}</span><span>${qty.toFixed(getQtyPrecision())}</span><span>${t}</span>`;
        // Insert at top
        marketTradesDiv.insertBefore(row, marketTradesDiv.firstChild);
        while (marketTradesDiv.children.length > MAX_MARKET_TRADES) {
            marketTradesDiv.removeChild(marketTradesDiv.lastChild);
        }
        lastTradePrice = price;
    }

    /* ── Active stream badges ──────────────────────────────────────────── */

    function syncActiveStreams(list) {
        activeStreams.clear();
        list.forEach((s) => activeStreams.add(s));
        renderStreamBadges();
    }

    function renderStreamBadges() {
        activeStreamsDiv.innerHTML = "";
        activeStreams.forEach((s) => {
            const badge = document.createElement("span");
            badge.className = "stream-badge";
            badge.innerHTML = `${s} <span class="remove-stream" data-stream="${s}">&times;</span>`;
            activeStreamsDiv.appendChild(badge);
        });
    }

    activeStreamsDiv.addEventListener("click", (e) => {
        const rm = e.target.closest(".remove-stream");
        if (rm && binanceWs && binanceWs.readyState === WebSocket.OPEN) {
            binanceWs.send(
                JSON.stringify({ action: "unsubscribe", streams: [rm.dataset.stream] })
            );
        }
    });

    /* ── Load all Binance trading symbols ─────────────────────────────── */

    async function loadBinanceSymbols() {
        if (symbolsLoaded) return;
        try {
            const res = await fetch("https://api.binance.com/api/v3/exchangeInfo");
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            const info = await res.json();
            allSymbols = info.symbols
                .filter(s => s.status === "TRADING")
                .map(s => ({
                    symbol: s.symbol.toLowerCase(),                // e.g. "btcusdt"
                    label:  `${s.baseAsset}/${s.quoteAsset}`,     // e.g. "BTC/USDT"
                    base:   s.baseAsset.toUpperCase(),
                    quote:  s.quoteAsset.toUpperCase(),
                }))
                .sort((a, b) => a.label.localeCompare(b.label));
            symbolsLoaded = true;
            console.log(`Loaded ${allSymbols.length} Binance symbols.`);
        } catch (err) {
            console.error("Failed to load Binance symbols:", err);
        }
    }

    // Start loading immediately
    loadBinanceSymbols();

    /* ── Symbol search / autocomplete ──────────────────────────────────── */

    let ddHighlight = -1;

    function renderDropdown(matches) {
        symbolDropdown.innerHTML = "";
        if (matches.length === 0) {
            symbolDropdown.classList.add("hidden");
            return;
        }
        matches.slice(0, 50).forEach((m, i) => {
            const div = document.createElement("div");
            div.className = "symbol-dd-item";
            div.dataset.value = m.symbol;
            div.innerHTML = `<img class="coin-icon" width="20" height="20" src="${coinIconUrl(m.base)}" alt="${m.base}" onerror="this.style.display='none';var s=document.createElement('span');s.className='coin-icon-fallback';s.style.width='20px';s.style.height='20px';s.style.fontSize='10px';s.textContent='${m.base.slice(0,2)}';this.parentNode.insertBefore(s,this.nextSibling);"><span class="dd-label">${m.label}</span><span class="dd-quote">${m.quote}</span>`;
            div.addEventListener("mousedown", (e) => {
                e.preventDefault(); // keep focus
                selectSymbol(m);
            });
            symbolDropdown.appendChild(div);
        });
        ddHighlight = -1;
        symbolDropdown.classList.remove("hidden");
    }

    function selectSymbol(m) {
        streamSymbol.value = m.label;
        streamSymbol.dataset.selected = m.symbol; // lowercase pair for Binance
        symbolDropdown.classList.add("hidden");
        // Auto-open chart for selected pair
        openChart(m.symbol.toUpperCase());
    }

    streamSymbol.addEventListener("focus", async () => {
        if (!symbolsLoaded) await loadBinanceSymbols();
        const q = streamSymbol.value.trim().toUpperCase().replace("/", "");
        if (q.length === 0) {
            // Show popular by default
            const popular = ["btcusdt","ethusdt","bnbusdt","solusdt","xrpusdt","dogeusdt","adausdt","maticusdt","avaxusdt","dotusdt"];
            renderDropdown(allSymbols.filter(s => popular.includes(s.symbol)));
        } else {
            renderDropdown(filterSymbols(q));
        }
    });

    streamSymbol.addEventListener("input", () => {
        const q = streamSymbol.value.trim().toUpperCase().replace("/", "");
        streamSymbol.dataset.selected = ""; // clear selection while typing
        if (q.length === 0) {
            symbolDropdown.classList.add("hidden");
            return;
        }
        renderDropdown(filterSymbols(q));
    });

    streamSymbol.addEventListener("blur", () => {
        // Small delay so click on dropdown fires first
        setTimeout(() => symbolDropdown.classList.add("hidden"), 180);
    });

    // Keyboard navigation inside dropdown
    streamSymbol.addEventListener("keydown", (e) => {
        const items = symbolDropdown.querySelectorAll(".symbol-dd-item");
        if (!items.length) return;

        if (e.key === "ArrowDown") {
            e.preventDefault();
            ddHighlight = Math.min(ddHighlight + 1, items.length - 1);
            highlightItem(items);
        } else if (e.key === "ArrowUp") {
            e.preventDefault();
            ddHighlight = Math.max(ddHighlight - 1, 0);
            highlightItem(items);
        } else if (e.key === "Enter") {
            e.preventDefault();
            if (ddHighlight >= 0 && items[ddHighlight]) {
                const val = items[ddHighlight].dataset.value;
                const m = allSymbols.find(s => s.symbol === val);
                if (m) selectSymbol(m);
            }
        } else if (e.key === "Escape") {
            symbolDropdown.classList.add("hidden");
        }
    });

    function highlightItem(items) {
        items.forEach((el, i) => el.classList.toggle("highlighted", i === ddHighlight));
        if (items[ddHighlight]) items[ddHighlight].scrollIntoView({ block: "nearest" });
    }

    function filterSymbols(query) {
        // Exact start match first, then contains
        const starts = [];
        const contains = [];
        for (const s of allSymbols) {
            const flat = s.symbol.toUpperCase(); // "BTCUSDT"
            const base = s.base;                  // "BTC"
            if (base === query || flat.startsWith(query)) {
                starts.push(s);
            } else if (flat.includes(query) || s.label.toUpperCase().includes(query)) {
                contains.push(s);
            }
        }
        return [...starts, ...contains];
    }

    /* ── Subscribe / Unsubscribe buttons ───────────────────────────────── */

    function getSelectedSymbol() {
        // Use dataset.selected if user picked from dropdown, otherwise try raw input
        let sym = (streamSymbol.dataset.selected || "").toLowerCase();
        if (!sym) {
            sym = streamSymbol.value.trim().replace("/", "").toLowerCase();
        }
        return sym;
    }

    function buildStreamName() {
        const sym  = getSelectedSymbol();
        const type = streamType.value;
        if (!sym) return null;
        return `${sym}@${type}`;
    }

    btnSubscribe.addEventListener("click", () => {
        const stream = buildStreamName();
        if (!stream) { alert("Select a trading pair first."); return; }
        if (binanceWs && binanceWs.readyState === WebSocket.OPEN) {
            binanceWs.send(
                JSON.stringify({ action: "subscribe", streams: [stream] })
            );
        }
    });

    btnUnsubscribe.addEventListener("click", () => {
        const stream = buildStreamName();
        if (!stream) return;
        if (binanceWs && binanceWs.readyState === WebSocket.OPEN) {
            binanceWs.send(
                JSON.stringify({ action: "unsubscribe", streams: [stream] })
            );
        }
    });

    btnClearFeed.addEventListener("click", () => {
        liveFeedDiv.innerHTML = "";
    });

    /* ═══════════════════════════════════════════════════════════════════
       WALLET RECONNECT (ethers.js)  —  needed for Transactions tab
       ═══════════════════════════════════════════════════════════════════ */

    let ethersProvider = null;
    let ethersSigner   = null;
    let _rawWalletProvider = null;   // keep reference to underlying EIP-1193 provider

    /**
     * Resolve the raw EIP-1193 wallet provider (Trust Wallet / MetaMask / generic).
     */
    function getRawProvider() {
        if (_rawWalletProvider) return _rawWalletProvider;
        let raw = null;
        if (wallet.provider === "Trust Wallet" || wallet.provider === "trust_wallet") {
            raw = window.trustwallet ||
                  (window.ethereum && window.ethereum.isTrust && window.ethereum) || null;
        }
        if (!raw && (wallet.provider === "MetaMask" || wallet.provider === "metamask")) {
            if (window.ethereum && window.ethereum.providers)
                raw = window.ethereum.providers.find(p => p.isMetaMask) || null;
            if (!raw && window.ethereum && window.ethereum.isMetaMask)
                raw = window.ethereum;
        }
        if (!raw && window.ethereum) raw = window.ethereum;
        _rawWalletProvider = raw;
        return raw;
    }

    /**
     * Create a **fresh** BrowserProvider + Signer from the raw wallet.
     * Must be called after every chain switch because ethers.js caches
     * the network internally and throws NETWORK_ERROR on mismatch.
     */
    async function rebuildProvider() {
        const raw = getRawProvider();
        if (!raw) return false;
        ethersProvider = new ethers.BrowserProvider(raw);
        await ethersProvider.send("eth_requestAccounts", []);
        ethersSigner   = await ethersProvider.getSigner();
        return true;
    }

    /**
     * Re-establish the wallet provider using the browser extension
     * (Trust Wallet or MetaMask).  The wallet.provider field stored at
     * login time tells us which one was used originally.
     */
    async function reconnectWallet() {
        try {
            const raw = getRawProvider();
            if (!raw) {
                console.warn("No injected wallet provider found — transactions disabled.");
                return false;
            }

            ethersProvider = new ethers.BrowserProvider(raw);
            await ethersProvider.send("eth_requestAccounts", []);
            ethersSigner = await ethersProvider.getSigner();
            const addr = await ethersSigner.getAddress();

            console.log("Wallet reconnected:", addr);
            return true;
        } catch (err) {
            console.error("Wallet reconnect failed:", err);
            return false;
        }
    }

    /* ═══════════════════════════════════════════════════════════════════
       TRANSACTIONS UI WIRING
       ═══════════════════════════════════════════════════════════════════ */

    // DOM refs — Transactions section
    const txTabs          = document.querySelectorAll(".tx-tab");
    const txPanels        = document.querySelectorAll(".tx-panel");
    const nativeBalanceEl = document.getElementById("native-balance");
    const snSymbolEl      = document.getElementById("sn-symbol");
    const txFeedDiv       = document.getElementById("tx-feed");
    const btnClearTxFeed  = document.getElementById("btn-clear-tx-feed");

    // Tab switching
    txTabs.forEach(tab => {
        tab.addEventListener("click", () => {
            txTabs.forEach(t => t.classList.toggle("active", t === tab));
            txPanels.forEach(p =>
                p.classList.toggle("active", p.id === `tab-${tab.dataset.tab}`)
            );
        });
    });

    if (btnClearTxFeed) {
        btnClearTxFeed.addEventListener("click", () => { txFeedDiv.innerHTML = ""; });
    }

    function txLog(label, text) {
        addFeedLine(txFeedDiv, label, text);
    }

    /** Refresh the native balance display */
    async function refreshBalance() {
        if (!TxModule) return;
        try {
            const bal = await TxModule.getNativeBalance();
            const cfg = TxModule.getConfig();
            nativeBalanceEl.textContent = `${parseFloat(bal).toFixed(6)} ${cfg.symbol}`;
            snSymbolEl.textContent = cfg.symbol;
        } catch (_) { /* ignore */ }
    }

    function showTxResult(id, msg, isError) {
        const el = document.getElementById(id);
        if (!el) return;
        el.textContent = msg;
        el.className = "tx-result " + (isError ? "error" : "success");
        el.classList.remove("hidden");
    }

    /* ── Send native ─────────────────────────── */
    const btnSendNative = document.getElementById("btn-send-native");
    if (btnSendNative) {
        btnSendNative.addEventListener("click", async () => {
            const to     = document.getElementById("sn-to").value.trim();
            const amount = document.getElementById("sn-amount").value.trim();
            if (!to || !amount) return showTxResult("sn-result", "Fill all fields.", true);
            btnSendNative.classList.add("loading");
            try {
                await TxModule.sendNative(to, amount);
                showTxResult("sn-result", "Transaction confirmed!", false);
                refreshBalance();
            } catch (err) {
                showTxResult("sn-result", err.message || "Transaction failed.", true);
            } finally {
                btnSendNative.classList.remove("loading");
            }
        });
    }

    /* ── Send token ──────────────────────────── */
    const stTokenInput = document.getElementById("st-token");
    const stTokenInfo  = document.getElementById("st-token-info");
    let _tokenInfoTimer = null;

    if (stTokenInput) {
        stTokenInput.addEventListener("input", () => {
            clearTimeout(_tokenInfoTimer);
            _tokenInfoTimer = setTimeout(async () => {
                const addr = stTokenInput.value.trim();
                if (!ethers.isAddress(addr)) {
                    stTokenInfo.textContent = "Enter a valid contract address…";
                    return;
                }
                try {
                    const info = await TxModule.getTokenInfo(addr);
                    stTokenInfo.textContent = `${info.name} (${info.symbol}) — Balance: ${info.balance}`;
                } catch (e) {
                    stTokenInfo.textContent = "Could not load token info.";
                }
            }, 600);
        });
    }

    const btnSendToken = document.getElementById("btn-send-token");
    if (btnSendToken) {
        btnSendToken.addEventListener("click", async () => {
            const token  = document.getElementById("st-token").value.trim();
            const to     = document.getElementById("st-to").value.trim();
            const amount = document.getElementById("st-amount").value.trim();
            if (!token || !to || !amount) return showTxResult("st-result", "Fill all fields.", true);
            btnSendToken.classList.add("loading");
            try {
                await TxModule.sendToken(token, to, amount);
                showTxResult("st-result", "Token transfer confirmed!", false);
                refreshBalance();
            } catch (err) {
                showTxResult("st-result", err.message || "Transfer failed.", true);
            } finally {
                btnSendToken.classList.remove("loading");
            }
        });
    }

    /* ── Swap ────────────────────────────────── */
    const swEstimateEl = document.getElementById("sw-estimate");
    let _swapTimer = null;

    // Live estimate on amount change
    const swAmountInput = document.getElementById("sw-amount");
    if (swAmountInput) {
        swAmountInput.addEventListener("input", () => {
            clearTimeout(_swapTimer);
            _swapTimer = setTimeout(async () => {
                const tokenIn  = document.getElementById("sw-from").value.trim() || null;
                const tokenOut = document.getElementById("sw-to").value.trim();
                const amount   = swAmountInput.value.trim();
                if (!tokenOut || !amount || parseFloat(amount) <= 0) {
                    swEstimateEl.textContent = "Estimated output: —";
                    return;
                }
                try {
                    const est = await TxModule.getSwapEstimate(tokenIn, tokenOut, amount);
                    swEstimateEl.textContent = `Estimated output: ~${parseFloat(est.amountOut).toFixed(6)} ${est.symbol}`;
                } catch (e) {
                    swEstimateEl.textContent = "Could not get estimate.";
                }
            }, 800);
        });
    }

    const btnSwap = document.getElementById("btn-swap");
    if (btnSwap) {
        btnSwap.addEventListener("click", async () => {
            const tokenIn  = document.getElementById("sw-from").value.trim() || null;
            const tokenOut = document.getElementById("sw-to").value.trim();
            const amount   = document.getElementById("sw-amount").value.trim();
            const slippage = parseFloat(document.getElementById("sw-slippage").value) || 0.5;
            if (!tokenOut || !amount) return showTxResult("sw-result", "Fill all fields.", true);
            btnSwap.classList.add("loading");
            try {
                await TxModule.swap(tokenIn, tokenOut, amount, slippage);
                showTxResult("sw-result", "Swap confirmed!", false);
                refreshBalance();
            } catch (err) {
                showTxResult("sw-result", err.message || "Swap failed.", true);
            } finally {
                btnSwap.classList.remove("loading");
            }
        });
    }

    function connectWalletWs() {
        const wsProto = location.protocol === "https:" ? "wss:" : "ws:";
        walletWs = new WebSocket(`${wsProto}//${location.host}/ws/wallet/`);

        walletWs.onopen = () => {
            wsWalletDot.classList.add("connected");
            wsWalletLabel.textContent = "Wallet WS: Connected";
            addFeedLine(walletFeedDiv, "System", "Wallet WebSocket connected.");

            // Send connect_wallet action
            walletWs.send(
                JSON.stringify({
                    action: "connect_wallet",
                    address: wallet.address,
                    provider: wallet.provider,
                    chain_id: wallet.chain_id,
                })
            );
        };

        walletWs.onclose = () => {
            wsWalletDot.classList.remove("connected");
            wsWalletLabel.textContent = "Wallet WS: Disconnected";
            // Auto-reconnect after 3 s
            setTimeout(connectWalletWs, 3000);
        };

        walletWs.onerror = () => {
            addFeedLine(walletFeedDiv, "Error", "Wallet WS error.");
        };

        walletWs.onmessage = (evt) => {
            try {
                const msg = JSON.parse(evt.data);
                handleWalletMessage(msg);
            } catch (_) { /* ignore */ }
        };
    }

    function handleWalletMessage(msg) {
        const event = msg.event || "message";
        addFeedLine(walletFeedDiv, event, JSON.stringify(msg));

        if (event === "wallet_connected") {
            infoStatus.textContent    = "Active";
            infoConnectedAt.textContent = new Date().toLocaleString();
        } else if (event === "wallet_disconnected") {
            infoStatus.textContent = "Disconnected";
        }
    }

    btnClearWalletFeed.addEventListener("click", () => {
        walletFeedDiv.innerHTML = "";
    });

    /* ── Disconnect ────────────────────────────────────────────────────── */

    btnDisconnect.addEventListener("click", async () => {
        try {
            await fetch("/api/wallet/disconnect/", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ address: wallet.address }),
            });
        } catch (_) { /* ignore network errors */ }

        if (walletWs && walletWs.readyState === WebSocket.OPEN) {
            walletWs.send(
                JSON.stringify({ action: "disconnect_wallet", address: wallet.address })
            );
            walletWs.close();
        }
        if (binanceWs) binanceWs.close();

        sessionStorage.removeItem("tw_wallet");
        window.location.href = "/";
    });

    /* ═══════════════════════════════════════════════════════════════════
       FEED HELPER
       ═══════════════════════════════════════════════════════════════════ */

    function addFeedLine(container, label, text) {
        const line = document.createElement("div");
        line.className = "feed-line";
        const now = new Date().toLocaleTimeString();
        line.innerHTML =
            `<span class="time">${now}</span> ` +
            `<span class="stream">[${label}]</span> ` +
            `${escapeHTML(text)}`;

        container.appendChild(line);
        // Trim old entries
        while (container.children.length > MAX_FEED_LINES) {
            container.removeChild(container.firstChild);
        }
        container.scrollTop = container.scrollHeight;
    }

    function escapeHTML(str) {
        const div = document.createElement("div");
        div.textContent = str;
        return div.innerHTML;
    }

    /* ═══════════════════════════════════════════════════════════════════
       INIT
       ═══════════════════════════════════════════════════════════════════ */

    connectBinance();
    connectWalletWs();

    // Fetch native-to-USD rate for trade form USD hints
    fetchNativeUsdRate();
    setInterval(fetchNativeUsdRate, 60000); // refresh every 60s

    // Reconnect wallet & init transaction module
    (async () => {
        const ok = await reconnectWallet();
        if (!ok) return;

        // Detect actual chain; auto-switch if mismatched
        try {
            const network = await ethersProvider.getNetwork();
            const actualChain = Number(network.chainId);
            updateChainBadge(actualChain);

            if (actualChain !== wallet.chain_id) {
                console.log(`Wrong chain: wallet is on ${actualChain}, expected ${wallet.chain_id}. Attempting auto-switch…`);
                const switched = await switchToChain(wallet.chain_id);
                if (switched) {
                    // Rebuild provider from scratch after chain switch
                    await rebuildProvider();
                } else {
                    console.warn(`Could not auto-switch to chain ${wallet.chain_id}. Trading may fail.`);
                }
            }
        } catch (e) {
            console.warn("Chain detection failed:", e);
        }

        if (typeof TxModule !== "undefined") {
            TxModule.init({
                signer:   ethersSigner,
                provider: ethersProvider,
                address:  wallet.address,
                chainId:  wallet.chain_id,
                onLog:    txLog,
            });
            refreshBalance();
        }
        refreshWalletInfo();
    })();

    // Auto-load default pair (BTCUSDT)
    setTimeout(() => {
        openChart("BTCUSDT");
        streamSymbol.value = "BTC/USDT";
        streamSymbol.dataset.selected = "btcusdt";
    }, 500);

    /* ================================================================
       Panel Resizer – Draggable dividers
       ================================================================ */
    (function initResizers() {
        const STORAGE_KEY = 'tw-panel-sizes';
        let saved = {};
        try { saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}'); } catch(e){}

        const tradeGrid  = document.querySelector('.trade-grid');
        const bottomGrid = document.querySelector('.bottom-grid');

        /* ── Restore saved sizes ── */
        if (saved.tgCols && tradeGrid)  tradeGrid.style.gridTemplateColumns  = saved.tgCols;
        if (saved.bgCols && bottomGrid) bottomGrid.style.gridTemplateColumns = saved.bgCols;
        if (saved.tgFlex && tradeGrid)  tradeGrid.style.flex  = saved.tgFlex;
        if (saved.bgH && bottomGrid)    bottomGrid.style.height = saved.bgH;

        function persist() {
            saved.tgCols = tradeGrid  ? tradeGrid.style.gridTemplateColumns  : '';
            saved.bgCols = bottomGrid ? bottomGrid.style.gridTemplateColumns : '';
            saved.tgFlex = tradeGrid  ? tradeGrid.style.flex : '';
            saved.bgH    = bottomGrid ? bottomGrid.style.height : '';
            localStorage.setItem(STORAGE_KEY, JSON.stringify(saved));
        }

        /* ── Column resizer (horizontal drag) ── */
        function initColResizer(resizerId, grid) {
            const handle = document.getElementById(resizerId);
            if (!handle || !grid) return;

            handle.addEventListener('mousedown', function(e) {
                e.preventDefault();
                const cols      = Array.from(grid.children);
                const handleIdx = cols.indexOf(handle);
                const leftEl    = cols[handleIdx - 1];
                const rightEl   = cols[handleIdx + 1];
                if (!leftEl || !rightEl) return;

                const startX       = e.clientX;
                const leftW        = leftEl.getBoundingClientRect().width;
                const rightW       = rightEl.getBoundingClientRect().width;
                const gridRect     = grid.getBoundingClientRect();

                handle.classList.add('active');
                document.body.classList.add('resizing-col');

                function onMove(ev) {
                    const dx       = ev.clientX - startX;
                    const newLeft  = Math.max(80, leftW + dx);
                    const newRight = Math.max(80, rightW - dx);

                    // Rebuild grid-template-columns from current children widths
                    const colDefs = [];
                    for (let i = 0; i < cols.length; i++) {
                        if (cols[i].classList.contains('resizer')) {
                            colDefs.push('4px');
                        } else if (cols[i] === leftEl) {
                            colDefs.push(newLeft + 'px');
                        } else if (cols[i] === rightEl) {
                            colDefs.push(newRight + 'px');
                        } else {
                            // Keep other columns at their current sizes
                            colDefs.push(cols[i].getBoundingClientRect().width + 'px');
                        }
                    }
                    grid.style.gridTemplateColumns = colDefs.join(' ');
                }

                function onUp() {
                    handle.classList.remove('active');
                    document.body.classList.remove('resizing-col');
                    document.removeEventListener('mousemove', onMove);
                    document.removeEventListener('mouseup', onUp);

                    // Convert first column back to 1fr for flexibility
                    const firstPanel = cols.find(c => !c.classList.contains('resizer'));
                    if (firstPanel) {
                        const parts = grid.style.gridTemplateColumns.split(' ');
                        const fpIdx = cols.indexOf(firstPanel);
                        parts[fpIdx] = '1fr';
                        grid.style.gridTemplateColumns = parts.join(' ');
                    }
                    persist();
                }

                document.addEventListener('mousemove', onMove);
                document.addEventListener('mouseup', onUp);
            });
        }

        /* ── Row resizer (vertical drag between trade-grid and bottom-grid) ── */
        function initRowResizer() {
            const handle = document.getElementById('resizer-v');
            if (!handle || !tradeGrid || !bottomGrid) return;

            handle.addEventListener('mousedown', function(e) {
                e.preventDefault();
                const startY    = e.clientY;
                const tgRect    = tradeGrid.getBoundingClientRect();
                const bgRect    = bottomGrid.getBoundingClientRect();
                const startTgH  = tgRect.height;
                const startBgH  = bgRect.height;

                handle.classList.add('active');
                document.body.classList.add('resizing-row');

                function onMove(ev) {
                    const dy     = ev.clientY - startY;
                    const newTgH = Math.max(120, startTgH + dy);
                    const newBgH = Math.max(80, startBgH - dy);

                    tradeGrid.style.flex = '0 0 ' + newTgH + 'px';
                    bottomGrid.style.height = newBgH + 'px';
                }

                function onUp() {
                    handle.classList.remove('active');
                    document.body.classList.remove('resizing-row');
                    document.removeEventListener('mousemove', onMove);
                    document.removeEventListener('mouseup', onUp);
                    persist();
                }

                document.addEventListener('mousemove', onMove);
                document.addEventListener('mouseup', onUp);
            });
        }

        /* ── Wire up all resizers ── */
        initColResizer('resizer-tg1', tradeGrid);
        initColResizer('resizer-tg2', tradeGrid);
        initColResizer('resizer-bg1', bottomGrid);
        initRowResizer();
    })();
})();

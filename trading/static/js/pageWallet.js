/**
 * pageWallet.js — Full-page wallet info controller.
 * Shows account info, network, balances, token holdings, and wallet events.
 */
(function () {
    "use strict";

    /* ── Guard ── */
    let wallet;
    try { wallet = JSON.parse(sessionStorage.getItem("tw_wallet")); } catch (_) {}
    if (!wallet || !wallet.authenticated) { window.location.href = "/"; return; }

    /* ── Chain names ── */
    const CHAINS = {
        1: "Ethereum Mainnet", 56: "BNB Smart Chain", 137: "Polygon",
        42161: "Arbitrum One", 10: "Optimism", 43114: "Avalanche C-Chain",
    };
    const NATIVE_SYMBOLS = { 1: "ETH", 56: "BNB", 137: "MATIC", 42161: "ETH", 10: "ETH", 43114: "AVAX" };
    const nativeSym = NATIVE_SYMBOLS[wallet.chain_id] || "ETH";

    /* ── DOM ── */
    const walletShortAddr   = document.getElementById("wallet-short-addr");
    const infoAddress       = document.getElementById("info-address");
    const infoProvider      = document.getElementById("info-provider");
    const infoStatus        = document.getElementById("info-status");
    const infoConnectedAt   = document.getElementById("info-connected-at");
    const infoChain         = document.getElementById("info-chain");
    const infoChainId       = document.getElementById("info-chain-id");
    const infoBlock         = document.getElementById("info-block");
    const infoGas           = document.getElementById("info-gas");
    const infoNativeBal     = document.getElementById("info-native-bal");
    const infoNativeUsd     = document.getElementById("info-native-usd");
    const infoTokensList    = document.getElementById("info-tokens-list");
    const btnRefreshWallet  = document.getElementById("btn-refresh-wallet");
    const walletFeedDiv     = document.getElementById("wallet-feed");
    const btnClearFeed      = document.getElementById("btn-clear-wallet-feed");

    /* ── Populate static info ── */
    const shortAddr = wallet.address.slice(0, 6) + "…" + wallet.address.slice(-4);
    walletShortAddr.textContent = shortAddr;
    infoAddress.textContent     = wallet.address;
    infoProvider.textContent    = wallet.provider || "—";
    infoChain.textContent       = CHAINS[wallet.chain_id] || `Chain ${wallet.chain_id}`;
    infoChainId.textContent     = wallet.chain_id || "—";
    infoStatus.textContent      = "Active";
    infoConnectedAt.textContent = new Date().toLocaleString();

    /* ── Copy address ── */
    infoAddress.addEventListener("click", () => {
        navigator.clipboard.writeText(wallet.address).then(() => {
            infoAddress.classList.add("copied");
            const orig = infoAddress.textContent;
            infoAddress.textContent = "Copied!";
            setTimeout(() => { infoAddress.textContent = orig; infoAddress.classList.remove("copied"); }, 1200);
        });
    });

    /* ── Disconnect ── */
    document.getElementById("btn-disconnect").addEventListener("click", () => {
        sessionStorage.removeItem("tw_wallet");
        window.location.href = "/";
    });

    /* ── Feed helper ── */
    function addFeedLine(container, label, text) {
        const line = document.createElement("div");
        line.className = "feed-line";
        const time = new Date().toLocaleTimeString();
        line.innerHTML = `<span class="fl-time">${time}</span><span class="fl-label">${label}</span><span class="fl-text">${text}</span>`;
        container.prepend(line);
        while (container.children.length > 200) container.lastChild.remove();
    }

    if (btnClearFeed) btnClearFeed.addEventListener("click", () => { walletFeedDiv.innerHTML = ""; });

    /* ── Known tokens per chain ── */
    const KNOWN_TOKENS = {
        56: [
            { symbol: "USDT",  address: "0x55d398326f99059fF775485246999027B3197955", decimals: 18 },
            { symbol: "USDC",  address: "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d", decimals: 18 },
            { symbol: "BUSD",  address: "0xe9e7CEA3DedcA5984780Bafc599bD69ADd087D56", decimals: 18 },
            { symbol: "WBNB",  address: "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c", decimals: 18 },
            { symbol: "CAKE",  address: "0x0E09FaBB73Bd3Ade0a17ECC321fD13a19e81cE82", decimals: 18 },
            { symbol: "DAI",   address: "0x1AF3F329e8BE154074D8769D1FFa4eE058B1DBc3", decimals: 18 },
        ],
        1: [
            { symbol: "USDT",  address: "0xdAC17F958D2ee523a2206206994597C13D831ec7", decimals: 6 },
            { symbol: "USDC",  address: "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48", decimals: 6 },
            { symbol: "DAI",   address: "0x6B175474E89094C44Da98b954EedeAC495271d0F", decimals: 18 },
            { symbol: "WETH",  address: "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2", decimals: 18 },
            { symbol: "LINK",  address: "0x514910771AF9Ca656af840dff83E8264EcF986CA", decimals: 18 },
            { symbol: "UNI",   address: "0x1f9840a85d5aF5bf1D1762F925BDADdC4201F984", decimals: 18 },
        ],
        137: [
            { symbol: "USDT",  address: "0xc2132D05D31c914a87C6611C10748AEb04B58e8F", decimals: 6 },
            { symbol: "USDC",  address: "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174", decimals: 6 },
            { symbol: "WMATIC",address: "0x0d500B1d8E8eF31E21C99d1Db9A6444d3ADf1270", decimals: 18 },
            { symbol: "WETH",  address: "0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619", decimals: 18 },
        ],
    };

    const ERC20_BALANCE_ABI = ["function balanceOf(address) view returns (uint256)"];

    /* ── Wallet reconnect ── */
    let ethersProvider = null;

    async function reconnectWallet() {
        try {
            let rawProvider = null;
            if (wallet.provider === "Trust Wallet" || wallet.provider === "trust_wallet")
                rawProvider = window.trustwallet ||
                    (window.ethereum && window.ethereum.isTrust && window.ethereum) || null;
            if (!rawProvider && (wallet.provider === "MetaMask" || wallet.provider === "metamask")) {
                if (window.ethereum && window.ethereum.providers)
                    rawProvider = window.ethereum.providers.find(p => p.isMetaMask) || null;
                if (!rawProvider && window.ethereum && window.ethereum.isMetaMask)
                    rawProvider = window.ethereum;
            }
            if (!rawProvider && window.ethereum) rawProvider = window.ethereum;
            if (!rawProvider) return false;

            ethersProvider = new ethers.BrowserProvider(rawProvider);
            await ethersProvider.send("eth_requestAccounts", []);
            return true;
        } catch (err) { console.error("Reconnect failed:", err); return false; }
    }

    /* ── Wallet WS ── */
    function connectWalletWs() {
        const wsProto = location.protocol === "https:" ? "wss:" : "ws:";
        const ws = new WebSocket(`${wsProto}//${location.host}/ws/wallet/`);
        ws.onopen = () => {
            addFeedLine(walletFeedDiv, "System", "Wallet WebSocket connected.");
            ws.send(JSON.stringify({
                action: "connect_wallet",
                address: wallet.address,
                provider: wallet.provider,
                chain_id: wallet.chain_id,
            }));
        };
        ws.onclose = () => {
            addFeedLine(walletFeedDiv, "System", "Wallet WS disconnected. Reconnecting…");
            setTimeout(connectWalletWs, 3000);
        };
        ws.onmessage = (evt) => {
            try {
                const msg = JSON.parse(evt.data);
                addFeedLine(walletFeedDiv, msg.event || "message", JSON.stringify(msg));
            } catch (_) {}
        };
    }

    /* ── Refresh all wallet data ── */
    async function refreshWalletInfo() {
        if (!ethersProvider) return;
        try {
            // Native balance
            const rawBal = await ethersProvider.getBalance(wallet.address);
            const bal    = parseFloat(ethers.formatEther(rawBal));
            infoNativeBal.textContent = `${bal.toFixed(6)} ${nativeSym}`;

            // USD estimate
            try {
                const pair = nativeSym === "ETH" ? "ETHUSDT" : nativeSym + "USDT";
                const res  = await fetch(`https://api.binance.com/api/v3/ticker/price?symbol=${pair}`);
                const data = await res.json();
                const usd  = bal * parseFloat(data.price);
                infoNativeUsd.textContent = `≈ $${usd.toLocaleString(undefined, {minimumFractionDigits:2, maximumFractionDigits:2})}`;
            } catch (_) { infoNativeUsd.textContent = "—"; }

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
            const found  = [];
            await Promise.all(tokens.map(async (tk) => {
                try {
                    const contract = new ethers.Contract(tk.address, ERC20_BALANCE_ABI, ethersProvider);
                    const raw = await contract.balanceOf(wallet.address);
                    const balance = parseFloat(ethers.formatUnits(raw, tk.decimals));
                    if (balance > 0.0001) found.push({ ...tk, balance });
                } catch (_) {}
            }));

            // Render tokens
            if (found.length > 0) {
                infoTokensList.innerHTML = "";
                found.sort((a, b) => b.balance - a.balance);
                for (const tk of found) {
                    const row = document.createElement("div");
                    row.className = "token-table-row";
                    const iconBase = tk.symbol.replace(/^W/, "").toLowerCase();
                    row.innerHTML = `
                        <span class="tt-token">
                            <img class="tt-icon" src="https://assets.coincap.io/assets/icons/${iconBase}@2x.png"
                                 onerror="this.style.display='none'" alt="">
                            <strong>${tk.symbol}</strong>
                        </span>
                        <span class="tt-balance mono">${tk.balance < 1 ? tk.balance.toFixed(6) : tk.balance.toFixed(4)}</span>
                        <span class="tt-contract mono">${tk.address.slice(0,6)}…${tk.address.slice(-4)}</span>
                    `;
                    infoTokensList.appendChild(row);
                }
            } else {
                infoTokensList.innerHTML = '<span class="wi-muted">No tokens found</span>';
            }
        } catch (err) { console.warn("Wallet info refresh failed:", err); }
    }

    if (btnRefreshWallet) btnRefreshWallet.addEventListener("click", refreshWalletInfo);

    // Auto-refresh every 30s
    setInterval(() => { if (ethersProvider) refreshWalletInfo(); }, 30000);

    /* ═══════════════════════════════════════════════════════════════════
       BUY CRYPTO / DEPOSIT SECTION
       ═══════════════════════════════════════════════════════════════════ */

    const NETWORK_MAP = {
        56:    { token: "BNB",   network: "bsc",      fullName: "BNB Smart Chain (BEP-20)", binSlug: "BNB",   chgSlug: "bnb",   cnTo: "bnb",   warnLabel: "BNB/BEP-20" },
        1:     { token: "ETH",   network: "ethereum", fullName: "Ethereum (ERC-20)",       binSlug: "ETH",   chgSlug: "eth",   cnTo: "eth",   warnLabel: "ETH/ERC-20" },
        137:   { token: "MATIC", network: "polygon",  fullName: "Polygon",                 binSlug: "MATIC", chgSlug: "matic", cnTo: "matic", warnLabel: "MATIC/Polygon" },
        42161: { token: "ETH",   network: "arbitrum", fullName: "Arbitrum One",             binSlug: "ETH",   chgSlug: "eth",   cnTo: "eth",   warnLabel: "ETH/Arbitrum" },
    };

    const netInfo = NETWORK_MAP[wallet.chain_id] || NETWORK_MAP[56];
    let nativePrice = 0; // USD price of native token

    // Populate static labels
    const buyTokenName     = document.getElementById("buy-token-name");
    const depositTokenName = document.getElementById("deposit-token-name");
    const depositNetName   = document.getElementById("deposit-network-name");
    const depositWarnToken = document.getElementById("deposit-warn-token");
    const buyEstAmount     = document.getElementById("buy-est-amount");
    const buyEstSymbol     = document.getElementById("buy-est-symbol");
    const buyUsdInput      = document.getElementById("buy-usd-amount");
    const btnBinance        = document.getElementById("btn-binance");
    const btnChangelly      = document.getElementById("btn-changelly");
    const btnChangenow     = document.getElementById("btn-changenow");
    const depositQr        = document.getElementById("deposit-qr");
    const depositAddr      = document.getElementById("deposit-address");
    const btnCopyDeposit   = document.getElementById("btn-copy-deposit");

    if (buyTokenName)     buyTokenName.textContent     = netInfo.token;
    if (depositTokenName) depositTokenName.textContent = netInfo.token;
    if (depositNetName)   depositNetName.textContent   = netInfo.fullName;
    if (depositWarnToken) depositWarnToken.textContent = netInfo.warnLabel;
    if (buyEstSymbol)     buyEstSymbol.textContent     = netInfo.token;

    // QR code & address
    if (depositQr) {
        depositQr.src = `https://api.qrserver.com/v1/create-qr-code/?size=180x180&color=F0B90B&bgcolor=181A20&data=${encodeURIComponent(wallet.address)}`;
    }
    if (depositAddr) depositAddr.value = wallet.address;

    // Copy deposit address
    if (btnCopyDeposit && depositAddr) {
        btnCopyDeposit.addEventListener("click", () => {
            navigator.clipboard.writeText(wallet.address).then(() => {
                btnCopyDeposit.textContent = "Copied!";
                setTimeout(() => { btnCopyDeposit.textContent = "Copy"; }, 1500);
            });
        });
    }

    // Fetch native token price
    async function fetchNativePrice() {
        try {
            const sym = netInfo.token === "MATIC" ? "MATICUSDT" : netInfo.token + "USDT";
            const res = await fetch(`https://api.binance.com/api/v3/ticker/price?symbol=${sym}`);
            const d = await res.json();
            nativePrice = parseFloat(d.price) || 0;
            updateBuyEstimate();
        } catch (_) {}
    }

    // Update estimated amount
    function updateBuyEstimate() {
        if (!buyEstAmount || !buyUsdInput) return;
        const usd = parseFloat(buyUsdInput.value) || 0;
        if (nativePrice > 0 && usd > 0) {
            buyEstAmount.textContent = (usd / nativePrice).toFixed(6);
        } else {
            buyEstAmount.textContent = "—";
        }
    }

    // Input listener
    if (buyUsdInput) {
        buyUsdInput.addEventListener("input", updateBuyEstimate);
    }

    // Quick amount buttons
    document.querySelectorAll(".buy-quick").forEach(btn => {
        btn.addEventListener("click", () => {
            if (buyUsdInput) {
                buyUsdInput.value = btn.dataset.amt;
                updateBuyEstimate();
                updateProviderLinks();
            }
        });
    });

    // Update provider links
    function updateProviderLinks() {
        const usd = parseFloat(buyUsdInput?.value) || 15;
        const addr = wallet.address;

        if (btnBinance) {
            btnBinance.href = `https://www.binance.com/en/crypto/buy/${netInfo.binSlug}`;
        }
        if (btnChangelly) {
            btnChangelly.href = `https://changelly.com/buy/${netInfo.chgSlug}?amount=${usd}&currency=usd`;
        }
        if (btnChangenow) {
            btnChangenow.href = `https://changenow.io/exchange?from=usd&to=${netInfo.cnTo}&amount=${usd}`;
        }
    }

    if (buyUsdInput) {
        buyUsdInput.addEventListener("input", updateProviderLinks);
    }

    // Init buy section (provider links updated after wallet reconnect below)

    /* ═══════════════════════════════════════════════════════════════════ */

    /* ── Init ── */
    (async () => {
        const ok = await reconnectWallet();
        if (ok) {
            refreshWalletInfo();
            addFeedLine(walletFeedDiv, "System", "Provider connected.");
            fetchNativePrice();
            updateProviderLinks();
        } else {
            addFeedLine(walletFeedDiv, "Warn", "No wallet provider found.");
        }
    })();

    connectWalletWs();
})();

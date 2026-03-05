/**
 * pageTransactions.js — Full-page transactions controller.
 * Handles wallet reconnect, TxModule init, and transaction UI.
 */
(function () {
    "use strict";

    /* ── Guard ── */
    let wallet;
    try { wallet = JSON.parse(sessionStorage.getItem("tw_wallet")); } catch (_) {}
    if (!wallet || !wallet.authenticated) { window.location.href = "/"; return; }

    /* ── Chain names ── */
    const CHAINS = {
        1: "Ethereum", 56: "BNB Smart Chain", 137: "Polygon",
        42161: "Arbitrum One", 10: "Optimism", 43114: "Avalanche",
    };

    /* ── DOM ── */
    const walletShortAddr  = document.getElementById("wallet-short-addr");
    const nativeBalanceEl  = document.getElementById("native-balance");
    const snSymbolEl       = document.getElementById("sn-symbol");
    const txFeedDiv        = document.getElementById("tx-feed");
    const btnClearTxFeed   = document.getElementById("btn-clear-tx-feed");

    /* ── Populate header ── */
    walletShortAddr.textContent = wallet.address.slice(0, 6) + "…" + wallet.address.slice(-4);

    /* ── Disconnect ── */
    document.getElementById("btn-disconnect").addEventListener("click", () => {
        sessionStorage.removeItem("tw_wallet");
        window.location.href = "/";
    });

    /* ── Tab switching ── */
    document.querySelectorAll(".pg-tab").forEach(btn => {
        btn.addEventListener("click", () => {
            const key = btn.dataset.tab;
            document.querySelectorAll(".pg-tab").forEach(b => b.classList.toggle("active", b === btn));
            document.querySelectorAll(".pg-panel").forEach(p =>
                p.classList.toggle("active", p.id === `tab-${key}`)
            );
        });
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

    function txLog(label, text) { addFeedLine(txFeedDiv, label, text); }

    if (btnClearTxFeed) btnClearTxFeed.addEventListener("click", () => { txFeedDiv.innerHTML = ""; });

    /* ── Wallet reconnect ── */
    let ethersProvider = null;
    let ethersSigner = null;

    async function reconnectWallet() {
        try {
            let rawProvider = null;
            if (wallet.provider === "Trust Wallet" || wallet.provider === "trust_wallet") {
                rawProvider = window.trustwallet ||
                    (window.ethereum && window.ethereum.isTrust && window.ethereum) || null;
            }
            if (!rawProvider && (wallet.provider === "MetaMask" || wallet.provider === "metamask")) {
                if (window.ethereum && window.ethereum.providers)
                    rawProvider = window.ethereum.providers.find(p => p.isMetaMask) || null;
                if (!rawProvider && window.ethereum && window.ethereum.isMetaMask)
                    rawProvider = window.ethereum;
            }
            if (!rawProvider && window.ethereum) rawProvider = window.ethereum;
            if (!rawProvider) { console.warn("No provider"); return false; }

            ethersProvider = new ethers.BrowserProvider(rawProvider);
            await ethersProvider.send("eth_requestAccounts", []);
            ethersSigner = await ethersProvider.getSigner();
            console.log("Wallet reconnected:", await ethersSigner.getAddress());
            return true;
        } catch (err) { console.error("Reconnect failed:", err); return false; }
    }

    /* ── Balance ── */
    async function refreshBalance() {
        if (!TxModule) return;
        try {
            const bal = await TxModule.getNativeBalance();
            const cfg = TxModule.getConfig();
            nativeBalanceEl.textContent = `${parseFloat(bal).toFixed(6)} ${cfg.symbol}`;
            if (snSymbolEl) snSymbolEl.textContent = cfg.symbol;
        } catch (_) {}
    }

    function showTxResult(id, msg, isError) {
        const el = document.getElementById(id);
        if (!el) return;
        el.textContent = msg;
        el.className = "tx-result " + (isError ? "error" : "success");
        el.classList.remove("hidden");
    }

    /* ── Send native ── */
    const btnSendNative = document.getElementById("btn-send-native");
    if (btnSendNative) {
        btnSendNative.addEventListener("click", async () => {
            const to = document.getElementById("sn-to").value.trim();
            const amount = document.getElementById("sn-amount").value.trim();
            if (!to || !amount) return showTxResult("sn-result", "Fill all fields.", true);
            btnSendNative.classList.add("loading");
            try {
                await TxModule.sendNative(to, amount);
                showTxResult("sn-result", "Transaction confirmed!", false);
                refreshBalance();
            } catch (err) {
                showTxResult("sn-result", err.message || "Transaction failed.", true);
            } finally { btnSendNative.classList.remove("loading"); }
        });
    }

    /* ── Send token ── */
    const stTokenInput = document.getElementById("st-token");
    if (stTokenInput) {
        stTokenInput.addEventListener("change", async () => {
            const addr = stTokenInput.value.trim();
            const info = document.getElementById("st-token-info");
            if (!addr || !TxModule) { info.textContent = "Enter contract to load…"; return; }
            try {
                const meta = await TxModule.getTokenMeta(addr);
                info.textContent = `${meta.name} (${meta.symbol}) — ${meta.decimals} decimals`;
            } catch (err) { info.textContent = "Could not load token info."; }
        });
    }
    const btnSendToken = document.getElementById("btn-send-token");
    if (btnSendToken) {
        btnSendToken.addEventListener("click", async () => {
            const token = document.getElementById("st-token").value.trim();
            const to = document.getElementById("st-to").value.trim();
            const amount = document.getElementById("st-amount").value.trim();
            if (!token || !to || !amount) return showTxResult("st-result", "Fill all fields.", true);
            btnSendToken.classList.add("loading");
            try {
                await TxModule.sendToken(token, to, amount);
                showTxResult("st-result", "Token sent!", false);
                refreshBalance();
            } catch (err) {
                showTxResult("st-result", err.message || "Failed.", true);
            } finally { btnSendToken.classList.remove("loading"); }
        });
    }

    /* ── Swap ── */
    const swAmountInput = document.getElementById("sw-amount");
    const swFromInput = document.getElementById("sw-from");
    const swToInput = document.getElementById("sw-to");
    let swapDebounce = null;
    function estimateSwap() {
        clearTimeout(swapDebounce);
        swapDebounce = setTimeout(async () => {
            const est = document.getElementById("sw-estimate");
            if (!TxModule) return;
            const fromAddr = swFromInput?.value.trim() || "";
            const toAddr = swToInput?.value.trim() || "";
            const amount = swAmountInput?.value.trim() || "";
            if (!toAddr || !amount) { est.textContent = "Est. output: —"; return; }
            try {
                const out = await TxModule.estimateSwap(fromAddr, toAddr, amount);
                est.textContent = `Est. output: ${out}`;
            } catch (_) { est.textContent = "Est. output: unavailable"; }
        }, 600);
    }
    [swAmountInput, swFromInput, swToInput].forEach(el => {
        if (el) el.addEventListener("input", estimateSwap);
    });

    const btnSwap = document.getElementById("btn-swap");
    if (btnSwap) {
        btnSwap.addEventListener("click", async () => {
            const fromAddr = swFromInput?.value.trim() || "";
            const toAddr = swToInput?.value.trim() || "";
            const amount = swAmountInput?.value.trim() || "";
            const slippage = parseFloat(document.getElementById("sw-slippage")?.value || "0.5");
            if (!toAddr || !amount) return showTxResult("sw-result", "Fill required fields.", true);
            btnSwap.classList.add("loading");
            try {
                await TxModule.swap(fromAddr, toAddr, amount, slippage);
                showTxResult("sw-result", "Swap confirmed!", false);
                refreshBalance();
            } catch (err) {
                showTxResult("sw-result", err.message || "Swap failed.", true);
            } finally { btnSwap.classList.remove("loading"); }
        });
    }

    /* ── Init ── */
    (async () => {
        const ok = await reconnectWallet();
        if (ok && typeof TxModule !== "undefined") {
            TxModule.init({
                signer: ethersSigner,
                provider: ethersProvider,
                address: wallet.address,
                chainId: wallet.chain_id,
                onLog: txLog,
            });
            refreshBalance();
        }
    })();
})();

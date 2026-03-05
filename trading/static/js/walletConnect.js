/**
 * walletConnect.js — Login page: connect wallet (Trust Wallet / MetaMask)
 * Flow: user clicks Connect ➜ get nonce from API ➜ sign message ➜ verify ➜ redirect
 */
(function () {
    "use strict";

    /* ── Only run the full login logic on the login page ────────────── */
    const isLoginPage = window.location.pathname === "/" || window.location.pathname === "/login/";

    // On non-login pages, just expose a minimal wallet-state helper and exit
    if (!isLoginPage) {
        // Still allow other scripts to read wallet state from sessionStorage
        try {
            const saved = JSON.parse(sessionStorage.getItem("tw_wallet"));
            if (saved) window.__walletState = saved;
        } catch (_) {}
        return;
    }

    /* ── DOM refs ──────────────────────────────────────────────────────── */
    const btnConnect    = document.getElementById("btn-connect");
    const btnMetaMask   = document.getElementById("btn-metamask");
    const statusDiv     = document.getElementById("status-msg");
    const chainSelector = document.getElementById("chain-selector");
    const chainSelect   = document.getElementById("chain-select");

    const API_BASE = "/api/wallet";

    /* ── Helpers ───────────────────────────────────────────────────────── */

    function showStatus(msg, type) {
        statusDiv.textContent = msg;
        statusDiv.className = "status-msg " + type;
        statusDiv.classList.remove("hidden");
    }

    function hideStatus() {
        statusDiv.classList.add("hidden");
    }

    function setLoading(btn, loading) {
        btn.classList.toggle("loading", loading);
        btn.disabled = loading;
    }

    /** POST JSON helper */
    async function postJSON(url, body) {
        const res = await fetch(url, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
        });
        const data = await res.json();
        if (!res.ok || !data.status) {
            throw new Error(data.message || "Request failed");
        }
        return data.data;
    }

    /* ── Auth flow ─────────────────────────────────────────────────────── */

    /**
     * Complete wallet auth flow:
     * 1. Request nonce from backend
     * 2. Sign message with wallet
     * 3. Verify signature on backend
     * 4. Store session & redirect to dashboard
     */
    async function authFlow(signer, address, providerName) {
        showStatus("Requesting nonce…", "info");

        // Step 1: get nonce
        const nonceData = await postJSON(`${API_BASE}/nonce/`, {
            address: address,
            provider: providerName,
        });

        showStatus("Please sign the message in your wallet…", "info");

        // Step 2: sign
        const signature = await signer.signMessage(nonceData.message);

        showStatus("Verifying signature…", "info");

        // Step 3: verify
        const chainId = parseInt(chainSelect.value, 10) || 56;
        const verifyData = await postJSON(`${API_BASE}/verify/`, {
            address: address,
            signature: signature,
            provider: providerName,
            chain_id: chainId,
        });

        // Step 4: save & redirect
        sessionStorage.setItem(
            "tw_wallet",
            JSON.stringify({
                address: verifyData.address,
                provider: verifyData.provider,
                chain_id: verifyData.chain_id,
                authenticated: true,
            })
        );

        showStatus("Connected! Redirecting…", "success");
        setTimeout(() => {
            window.location.href = "/dashboard/";
        }, 600);
    }

    /* ── Trust Wallet (injected provider) ─────────────────────────────── */

    /**
     * Detect Trust Wallet's injected provider.
     * Trust Wallet extension: window.trustwallet
     * Trust Wallet in-app browser: window.ethereum with isTrust flag
     * Falls back to window.ethereum if only one provider is present.
     */
    function getTrustWalletProvider() {
        // Trust Wallet extension injects a dedicated object
        if (window.trustwallet) return window.trustwallet;

        // In-app browser or extension that sets isTrust on ethereum
        if (window.ethereum && window.ethereum.isTrust) return window.ethereum;

        // EIP-5749 multi-provider: check providers array
        if (window.ethereum && window.ethereum.providers) {
            const tw = window.ethereum.providers.find(p => p.isTrust);
            if (tw) return tw;
        }

        return null;
    }

    async function connectTrustWallet() {
        setLoading(btnConnect, true);
        hideStatus();

        try {
            const twProvider = getTrustWalletProvider();

            if (!twProvider) {
                throw new Error(
                    "Trust Wallet not detected. Install the Trust Wallet browser extension or open this page in the Trust Wallet app."
                );
            }

            const provider = new ethers.BrowserProvider(twProvider);
            const accounts = await provider.send("eth_requestAccounts", []);
            if (!accounts.length) throw new Error("No accounts returned.");

            const signer = await provider.getSigner();
            const address = await signer.getAddress();

            // Show chain selector
            chainSelector.classList.remove("hidden");

            await authFlow(signer, address, "trust_wallet");
        } catch (err) {
            console.error("Trust Wallet error:", err);
            showStatus(err.message || "Trust Wallet connection failed.", "error");
        } finally {
            setLoading(btnConnect, false);
        }
    }

    /* ── MetaMask / injected provider ──────────────────────────────────── */

    function getMetaMaskProvider() {
        // Multi-provider scenario (MetaMask + other extensions)
        if (window.ethereum && window.ethereum.providers) {
            const mm = window.ethereum.providers.find(p => p.isMetaMask);
            if (mm) return mm;
        }
        // Standard single MetaMask
        if (window.ethereum && window.ethereum.isMetaMask) return window.ethereum;
        // Fallback: any injected provider
        if (window.ethereum) return window.ethereum;
        return null;
    }

    async function connectMetaMask() {
        setLoading(btnMetaMask, true);
        hideStatus();

        try {
            const mmProvider = getMetaMaskProvider();

            if (!mmProvider) {
                throw new Error(
                    "MetaMask not detected. Install the MetaMask browser extension first."
                );
            }

            const provider = new ethers.BrowserProvider(mmProvider);
            const accounts = await provider.send("eth_requestAccounts", []);
            if (!accounts.length) throw new Error("No accounts returned.");

            const signer = await provider.getSigner();
            const address = await signer.getAddress();

            // Show chain selector
            chainSelector.classList.remove("hidden");

            await authFlow(signer, address, "metamask");
        } catch (err) {
            console.error("MetaMask error:", err);
            showStatus(err.message || "MetaMask connection failed.", "error");
        } finally {
            setLoading(btnMetaMask, false);
        }
    }

    /* ── Event listeners ───────────────────────────────────────────────── */

    if (btnConnect)  btnConnect.addEventListener("click", connectTrustWallet);
    if (btnMetaMask) btnMetaMask.addEventListener("click", connectMetaMask);

    // If already logged in, redirect to dashboard
    try {
        const saved = JSON.parse(sessionStorage.getItem("tw_wallet"));
        if (saved && saved.authenticated) {
            window.location.href = "/dashboard/";
        }
    } catch (_) { /* ignore */ }
})();

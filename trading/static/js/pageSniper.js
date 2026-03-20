/**
 * pageSniper.js — Sniper Bot frontend controller.
 *
 * Connects to ws://.../ws/sniper/ and controls the backend SniperBot.
 * Displays real-time pipeline, detected tokens, active positions, live feed.
 */
(function () {
    "use strict";

    /* ═══════════════════════════════════════════════════════════════
     *  Wallet + TxModule (ethers.js)
     * ═══════════════════════════════════════════════════════════════ */
    const wallet = window.__walletState || { address: "", chain_id: 56 };
    let _rawWalletProvider = null;
    let ethersProvider     = null;
    let ethersSigner       = null;
    let _txReady           = false;   // true once TxModule.init() succeeds

    /** Detect injected wallet (Trust Wallet / MetaMask / generic). */
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

    /** Rebuild ethers provider + signer and initialize TxModule. */
    async function reconnectWallet() {
        try {
            const raw = getRawProvider();
            if (!raw) {
                console.warn("Sniper: No injected wallet — auto-buy disabled.");
                return false;
            }
            ethersProvider = new ethers.BrowserProvider(raw);
            await ethersProvider.send("eth_requestAccounts", []);
            ethersSigner = await ethersProvider.getSigner();
            const addr   = await ethersSigner.getAddress();

            wallet.address  = addr;
            const cid       = await raw.request({ method: "eth_chainId" });
            wallet.chain_id = parseInt(cid, 16);
            window.__walletState = wallet;

            // Initialize TxModule for on-chain swaps
            if (typeof TxModule !== "undefined") {
                TxModule.init({
                    signer:   ethersSigner,
                    provider: ethersProvider,
                    address:  wallet.address,
                    chainId:  wallet.chain_id,
                    onLog:    (msg) => addFeed(`🔗 ${msg}`, "system"),
                });
                _txReady = true;
                console.log("Sniper: TxModule ready —", addr);
            } else {
                console.warn("Sniper: TxModule not found — swaps disabled.");
            }
            return true;
        } catch (err) {
            console.error("Sniper: wallet reconnect failed:", err);
            return false;
        }
    }

    function shortAddr(a) {
        return a ? a.slice(0, 6) + "…" + a.slice(-4) : "Not connected";
    }

    /** Ensure TxModule is ready; try a one-time reconnect if not. */
    async function ensureTxReady() {
        if (_txReady) return true;
        const ok = await reconnectWallet();
        return ok && _txReady;
    }

    /* ═══════════════════════════════════════════════════════════════
     *  DOM refs
     * ═══════════════════════════════════════════════════════════════ */
    const $  = (id) => document.getElementById(id);

    // Bot controls
    const btnStart       = $("btn-start");
    const btnStop        = $("btn-stop");
    const chainSelect    = $("chain-select");
    const botDot         = $("bot-dot");
    const botStatusText  = $("bot-status-text");
    const botChainLabel  = $("bot-chain-label");

    // Stats
    const statBlock      = $("stat-block");
    const statPairs      = $("stat-pairs");
    const statActive     = $("stat-active");
    const statPrice      = $("stat-price");

    // Pipeline counts
    const pipeMempoolCnt = $("pipe-mempool-count");
    const pipeDetectCnt  = $("pipe-detect-count");
    const pipeAnalyzeCnt = $("pipe-analyze-count");
    const pipeLiqCnt     = $("pipe-liq-count");
    const pipeSnipeCnt   = $("pipe-snipe-count");
    const pipeProfitCnt  = $("pipe-profit-count");

    // Tables
    const detectedTbody  = $("detected-tbody");
    const snipesTbody    = $("snipes-tbody");

    // Feed
    const feedDiv        = $("sniper-feed");

    // Settings
    const setMinLiq      = $("set-min-liq");
    const setMaxBuyTax   = $("set-max-buy-tax");
    const setMaxSellTax  = $("set-max-sell-tax");
    const setBuyAmount   = $("set-buy-amount");
    const setTP          = $("set-tp");
    const setSL          = $("set-sl");
    const setSlippage    = $("set-slippage");
    const setOnlySafe    = $("set-only-safe");
    const setAutoBuy     = $("set-auto-buy");
    const setMaxHold     = $("set-max-hold");
    const btnSaveSettings = $("btn-save-settings");

    // Performance settings
    const setMaxConcurrent = $("set-max-concurrent");
    const setBlockRange    = $("set-block-range");
    const setPollInterval  = $("set-poll-interval");

    // Wallet badge
    const walletBadge    = $("wallet-badge");
    const walletAddr     = $("wallet-addr");

    // Detected modal
    const detectedModal  = $("detected-modal");
    const btnOpenDetected  = $("btn-open-detected");
    const btnCloseDetected = $("btn-close-detected");
    const detectedBadge    = $("detected-badge");

    // Chart modal
    const chartModal        = $("chart-modal");
    const chartModalSymbol  = $("chart-modal-symbol");
    const chartModalMeta    = $("chart-modal-meta");
    const chartModalContainer = $("chart-modal-container");
    const chartLoading      = $("chart-loading");
    const chartInfoBar      = $("chart-info-bar");
    const cmiPrice          = $("cmi-price");
    const cmiChange         = $("cmi-change");
    const cmiVol            = $("cmi-vol");
    const cmiLiq            = $("cmi-liq");
    const btnCloseChartModal = $("btn-close-chart-modal");

    // Log modal
    const logModal          = $("log-modal");
    const logModalTitle     = $("log-modal-title");
    const logModalBody      = $("log-modal-body");
    const btnCloseLogModal  = $("btn-close-log-modal");

    // v4: Dashboard KPI refs
    const kpiTrades       = $("kpi-trades");
    const kpiWinRate      = $("kpi-winrate");
    const kpiPnl          = $("kpi-pnl");
    const kpiDetections   = $("kpi-detections");
    const kpiFilterRate   = $("kpi-filter-rate");
    const kpiAvgSpeed     = $("kpi-avg-speed");
    const btnRefreshDash  = $("btn-refresh-dashboard");
    const btnToggleDash   = $("btn-toggle-dashboard");
    const dashboardBody   = $("dashboard-body");

    // v4: Effectiveness refs
    const effSafe         = $("eff-safe-pct");
    const effAvgGain      = $("eff-avg-gain");
    const effAvgLoss      = $("eff-avg-loss");
    const effTxFail       = $("eff-tx-fail");
    const effAvgHold      = $("eff-avg-hold");
    const effBestTrade    = $("eff-best-trade");

    // v4: Resource bar refs
    const resMemory       = $("res-memory");
    const resCpu          = $("res-cpu");
    const resTokens       = $("res-tokens");
    const resWs           = $("res-ws");
    const resRpc          = $("res-rpc");

    // v4: Tutorial
    const tutorialOverlay = $("tutorial-overlay");

    /* ═══════════════════════════════════════════════════════════════
     *  State
     * ═══════════════════════════════════════════════════════════════ */
    let ws = null;
    let botRunning = false;
    let allDetected = [];        // ALL tokens detected (liquid + no-liquid)
    let detectedTokens = [];     // [{token, symbol, name, risk, buy_tax, sell_tax, liquidity_usd, pair}]
    let activeSnipes = [];
    let activeFilter = "all";    // current tab filter

    // Chart state
    let chartCurrentToken = null;  // {token, symbol, pair}
    let chartRefreshTimer = null;

    // Log modal state
    let _logModalAddr = null;      // token address currently shown in log modal
    let _logAutoFollow = true;     // auto-update modal with each new token_detected

    // Pipeline counters
    let pipeStats = {
        mempool: 0,
        detected: 0,
        analyzed: 0,
        liquidity: 0,
        sniped: 0,
    };

    // v4: Dashboard state
    let _dashboardTimer = null;
    let _dashCollapsed  = false;   // skip DOM updates when collapsed

    /* ═══════════════════════════════════════════════════════════════
     *  Feed
     * ═══════════════════════════════════════════════════════════════ */
    function addFeed(text, type = "info") {
        const line = document.createElement("div");
        line.className = `feed-line feed-${type}`;
        const ts = new Date().toLocaleTimeString();
        line.textContent = `[${ts}] ${text}`;
        feedDiv.appendChild(line);

        // Keep max 500 lines
        while (feedDiv.children.length > 500) {
            feedDiv.removeChild(feedDiv.firstChild);
        }
        feedDiv.scrollTop = feedDiv.scrollHeight;
    }

    /** Like addFeed but allows HTML (for clickable TX links). */
    function addFeedHTML(html, type = "info") {
        const line = document.createElement("div");
        line.className = `feed-line feed-${type}`;
        const ts = new Date().toLocaleTimeString();
        line.innerHTML = `[${ts}] ${html}`;
        feedDiv.appendChild(line);
        while (feedDiv.children.length > 500) {
            feedDiv.removeChild(feedDiv.firstChild);
        }
        feedDiv.scrollTop = feedDiv.scrollHeight;
    }

    /** Get block explorer base URL for current chain. */
    function _explorerBase() {
        const cid = wallet.chain_id || parseInt(chainSelect.value) || 56;
        return cid === 56  ? "https://bscscan.com"
             : cid === 1   ? "https://etherscan.io"
             : cid === 137  ? "https://polygonscan.com"
             : cid === 42161 ? "https://arbiscan.io"
             :                 "https://bscscan.com";
    }
    function explorerTxUrl(txHash) { return `${_explorerBase()}/tx/${txHash}`; }
    function explorerTokenUrl(addr) { return `${_explorerBase()}/token/${addr}`; }
    function explorerAddrUrl(addr)  { return `${_explorerBase()}/address/${addr}`; }

    function nativeSymbol() {
        const cid = wallet.chain_id || parseInt(chainSelect.value) || 56;
        return cid === 56 ? "BNB" : cid === 1 ? "ETH" : cid === 137 ? "MATIC" : cid === 42161 ? "ETH" : "Native";
    }

    function dexScreenerUrl(token) {
        const cid = wallet.chain_id || parseInt(chainSelect.value) || 56;
        const chain = cid === 56 ? "bsc" : cid === 1 ? "ethereum" : cid === 137 ? "polygon" : "bsc";
        return `https://dexscreener.com/${chain}/${token}`;
    }

    function coinMarketCapUrl(token) {
        const cid = wallet.chain_id || parseInt(chainSelect.value) || 56;
        const chain = cid === 56 ? "bsc" : cid === 1 ? "ethereum" : cid === 137 ? "polygon-pos" : "bsc";
        return `https://coinmarketcap.com/dexscan/${chain}/${token}`;
    }

    /**
     * Build a full HTML log card for a detected token.
     * Shows ALL analysis data in a compact formatted block.
     */
    function buildTokenLogCard(data) {
        const rIcon = data.risk === "safe" ? "🟢" : data.risk === "warning" ? "🟡" : data.risk === "danger" ? "🔴" : "⚪";
        const liqStr = data.liquidity_usd > 0 ? `$${Number(data.liquidity_usd).toLocaleString()}` : "$0";
        const liqNative = data.liquidity_native > 0 ? `${data.liquidity_native} ${nativeSymbol()}` : "0";
        const tokenUrl = explorerTokenUrl(data.token);
        const dexUrl = dexScreenerUrl(data.token);
        const cmcUrl = coinMarketCapUrl(data.token);
        const pairUrl = data.pair ? explorerAddrUrl(data.pair) : "#";

        // Boolean flags with checkmark/cross
        const boolIcon = (val) => val ? '<span style="color:#e74c3c">✗</span>' : '<span style="color:#02c076">✓</span>';
        const boolIconGood = (val) => val ? '<span style="color:#02c076">✓</span>' : '<span style="color:#e74c3c">✗</span>';

        // API status bar
        const apiDot = (ok) => ok ? '🟢' : '🔴';
        const apiCount = [data.goplus_ok, data.honeypot_ok, data.dexscreener_ok, data.coingecko_ok, data.tokensniffer_ok].filter(Boolean).length;

        // DexScreener data
        const dxVol = data.dexscreener_volume_24h > 0 ? `$${Number(data.dexscreener_volume_24h).toLocaleString()}` : "—";
        const dxLiq = data.dexscreener_liquidity > 0 ? `$${Number(data.dexscreener_liquidity).toLocaleString()}` : "—";
        const dxAge = data.dexscreener_age_hours != null ? `${data.dexscreener_age_hours.toFixed(1)}h` : "—";
        const dxBuys = data.dexscreener_buys_24h || 0;
        const dxSells = data.dexscreener_sells_24h || 0;

        // TokenSniffer
        const tsScore = data.tokensniffer_score != null ? `${data.tokensniffer_score}/100` : "—";
        const tsScam = data.tokensniffer_is_scam;

        // Supply
        const supply = data.total_supply ? Number(data.total_supply).toLocaleString() : "—";
        const holders = data.holder_count || "—";

        // Risk reasons
        const reasons = (data.risk_reasons && data.risk_reasons.length)
            ? data.risk_reasons.map(r => `  ⚠️ ${r}`).join('<br>')
            : '  ✅ Sin alertas';

        return `
<div class="token-log-card" data-token="${data.token}">
  <div class="tlc-header">
    <span class="tlc-risk">${rIcon} ${data.risk.toUpperCase()}</span>
    <strong class="tlc-symbol">${data.symbol}</strong>
    <span class="tlc-name">(${data.name})</span>
    <span class="tlc-block">Block #${Number(data.block).toLocaleString()}</span>
  </div>
  <div class="tlc-links">
    📋 <a href="${tokenUrl}" target="_blank">${data.token}</a>
    &nbsp;|&nbsp; 🔗 <a href="${dexUrl}" target="_blank">DexScreener</a>
    &nbsp;|&nbsp; 📈 <a href="${cmcUrl}" target="_blank">CoinMarketCap</a>
    &nbsp;|&nbsp; 🏊 <a href="${pairUrl}" target="_blank">Pair</a>
  </div>
  <div class="tlc-grid">
    <div class="tlc-section">
      <div class="tlc-title">💰 Liquidez</div>
      <div>USD: <strong>${liqStr}</strong></div>
      <div>Native: ${liqNative}</div>
      <div>Min required: $${Number(parseFloat(setMinLiq?.value) || 5000).toLocaleString()}</div>
      <div>Passes: ${data.has_liquidity ? '<span style="color:#02c076">SÍ ✓</span>' : '<span style="color:#e74c3c">NO ✗</span>'}</div>
    </div>
    <div class="tlc-section">
      <div class="tlc-title">📊 Impuestos</div>
      <div>Buy Tax: <strong>${data.buy_tax}%</strong></div>
      <div>Sell Tax: <strong>${data.sell_tax}%</strong></div>
      <div>Honeypot: ${data.is_honeypot ? '🍯 <span style="color:#e74c3c">SÍ</span>' : '<span style="color:#02c076">NO ✓</span>'}</div>
    </div>
    <div class="tlc-section">
      <div class="tlc-title">🔒 Seguridad</div>
      <div>${boolIcon(data.is_mintable)} Mintable</div>
      <div>${boolIcon(data.has_blacklist)} Blacklist</div>
      <div>${boolIcon(data.can_pause_trading)} Pause Trading</div>
      <div>${boolIcon(data.is_proxy)} Proxy</div>
      <div>${boolIcon(data.has_hidden_owner)} Hidden Owner</div>
      <div>${boolIcon(data.can_self_destruct)} Self-Destruct</div>
      <div>${boolIcon(data.cannot_sell_all)} Cannot Sell All</div>
      <div>${boolIcon(data.owner_can_change_balance)} Owner Changes Bal</div>
      <div>${boolIconGood(data.is_open_source)} Open Source</div>
      <div>${boolIcon(data.has_owner)} Has Owner</div>
    </div>
    <div class="tlc-section">
      <div class="tlc-title">📈 DexScreener</div>
      <div>Pairs: ${data.dexscreener_pairs || 0}</div>
      <div>Vol 24h: ${dxVol}</div>
      <div>Liq: ${dxLiq}</div>
      <div>Buys: ${dxBuys} | Sells: ${dxSells}</div>
      <div>Age: ${dxAge}</div>
      <div style="margin-top:4px;font-weight:600">Variación Precio</div>
      <div>5m: <span style="color:${(data.price_change_m5||0)>=0?'#02c076':'#e74c3c'}">${(data.price_change_m5||0).toFixed(1)}%</span></div>
      <div>1h: <span style="color:${(data.price_change_h1||0)>=0?'#02c076':'#e74c3c'}">${(data.price_change_h1||0).toFixed(1)}%</span></div>
      <div>6h: <span style="color:${(data.price_change_h6||0)>=0?'#02c076':'#e74c3c'}">${(data.price_change_h6||0).toFixed(1)}%</span></div>
      <div>24h: <span style="color:${(data.price_change_h24||0)>=0?'#02c076':'#e74c3c'}">${(data.price_change_h24||0).toFixed(1)}%</span></div>
    </div>
    <div class="tlc-section">
      <div class="tlc-title">🔐 LP Lock</div>
      <div>Bloqueado: ${data.lp_locked ? '<span style="color:#02c076">SÍ ✓</span>' : '<span style="color:#e74c3c">NO ✗</span>'}</div>
      <div>% Locked: <strong>${(data.lp_lock_percent||0).toFixed(1)}%</strong></div>
      <div>Restante: ${data.lp_lock_hours_remaining > 0 ? `<strong>${data.lp_lock_hours_remaining.toFixed(1)}h</strong>` : '—'}</div>
      <div>Fuente: ${data.lp_lock_source || '—'}</div>
    </div>
    <div class="tlc-section">
      <div class="tlc-title">🌐 Plataformas</div>
      <div>${boolIconGood(data.listed_coingecko)} CoinGecko</div>
      <div>${boolIconGood(data.has_website)} Website</div>
      <div>${boolIconGood(data.has_social_links)} Redes Sociales</div>
      <div>TokenSniffer: ${tsScore}${tsScam ? ' 🚩 SCAM' : ''}</div>
      <div>Supply: ${supply}</div>
      <div>Holders: ${holders}</div>
    </div>
    <div class="tlc-section">
      <div class="tlc-title">🔌 APIs (${apiCount}/5)</div>
      <div>${apiDot(data.goplus_ok)} GoPlus</div>
      <div>${apiDot(data.honeypot_ok)} Honeypot.is</div>
      <div>${apiDot(data.dexscreener_ok)} DexScreener</div>
      <div>${apiDot(data.coingecko_ok)} CoinGecko</div>
      <div>${apiDot(data.tokensniffer_ok)} TokenSniffer</div>
    </div>
    ${data.pump_score != null ? `
    <div class="tlc-section">
      <div class="tlc-title">🚀 Pump Score</div>
      <div>Score: <strong style="color:${data.pump_score>=80?'#02c076':data.pump_score>=60?'#f0b90b':data.pump_score>=40?'#f39c12':'#e74c3c'}">${data.pump_score}/100</strong></div>
      <div>Grade: <strong>${data.pump_grade || '—'}</strong></div>
      ${(data.pump_signals||[]).map(s => `<div>📌 ${s}</div>`).join('')}
    </div>` : ''}
    ${data.simulation_ok ? `
    <div class="tlc-section">
      <div class="tlc-title">🧪 Swap Simulation</div>
      <div>Can Buy: ${data.sim_can_buy ? '<span style="color:#02c076">✓</span>' : '<span style="color:#e74c3c">✗</span>'}</div>
      <div>Can Sell: ${data.sim_can_sell ? '<span style="color:#02c076">✓</span>' : '<span style="color:#e74c3c">✗</span>'}</div>
      <div>Buy Tax (sim): ${(data.sim_buy_tax||0).toFixed(1)}%</div>
      <div>Sell Tax (sim): ${(data.sim_sell_tax||0).toFixed(1)}%</div>
      <div>Honeypot (sim): ${data.sim_is_honeypot ? '🍯 <span style="color:#e74c3c">SÍ</span>' : '<span style="color:#02c076">NO ✓</span>'}</div>
      ${data.sim_honeypot_reason ? `<div>Razón: ${data.sim_honeypot_reason}</div>` : ''}
    </div>` : ''}
    ${data.bytecode_ok ? `
    <div class="tlc-section">
      <div class="tlc-title">🔬 Bytecode</div>
      <div>Size: ${data.bytecode_size || '—'} bytes</div>
      <div>${boolIcon(data.bytecode_has_selfdestruct)} SELFDESTRUCT</div>
      <div>${boolIcon(data.bytecode_has_delegatecall)} DELEGATECALL</div>
      <div>${boolIcon(data.bytecode_is_proxy)} Proxy Pattern</div>
      ${(data.bytecode_flags||[]).map(f => `<div>⚠️ ${f}</div>`).join('')}
    </div>` : ''}
    ${data.smart_money_buyers > 0 ? `
    <div class="tlc-section">
      <div class="tlc-title">🐋 Smart Money</div>
      <div>Buyers: <strong style="color:#02c076">${data.smart_money_buyers}</strong></div>
      <div>Confidence: ${(data.smart_money_confidence*100).toFixed(0)}%</div>
    </div>` : ''}
    ${data.dev_ok ? `
    <div class="tlc-section">
      <div class="tlc-title">👨‍💻 Dev Tracker</div>
      <div>Score: <strong style="color:${data.dev_score>=70?'#02c076':data.dev_score>=40?'#f0b90b':'#e74c3c'}">${data.dev_score}/100</strong></div>
      <div>Label: <strong>${data.dev_label || '—'}</strong></div>
      <div>Tracked: ${data.dev_is_tracked ? '<span style="color:#02c076">SÍ</span>' : 'NO'}</div>
      <div>Launches: ${data.dev_total_launches || 0}</div>
      <div>Exitosos: <span style="color:#02c076">${data.dev_successful_launches || 0}</span></div>
      <div>Rugs: <span style="color:#e74c3c">${data.dev_rug_pulls || 0}</span></div>
      <div>Best x: ${(data.dev_best_multiplier||0).toFixed(1)}x</div>
      ${data.dev_is_serial_scammer ? '<div style="color:#e74c3c;font-weight:700">⛔ SERIAL SCAMMER</div>' : ''}
    </div>` : ''}
    ${data.risk_engine_ok ? `
    <div class="tlc-section">
      <div class="tlc-title">🎯 Risk Engine</div>
      <div>Score: <strong style="color:${data.risk_engine_score>=80?'#02c076':data.risk_engine_score>=65?'#f0b90b':data.risk_engine_score>=50?'#f39c12':'#e74c3c'}">${data.risk_engine_score}/100</strong></div>
      <div>Action: <strong style="color:${data.risk_engine_action==='STRONG_BUY'||data.risk_engine_action==='BUY'?'#02c076':data.risk_engine_action==='WATCH'?'#f0b90b':'#e74c3c'}">${data.risk_engine_action}</strong></div>
      <div>Confidence: ${(data.risk_engine_confidence*100).toFixed(0)}%</div>
      ${data.risk_engine_hard_stop ? '<div style="color:#e74c3c;font-weight:700">⛔ HARD STOP</div>' : ''}
      ${(data.risk_engine_signals||[]).slice(0,5).map(s => `<div>📌 ${s}</div>`).join('')}
    </div>` : ''}
    ${data.pump_market_cap_usd > 0 || data.pump_holder_growth_rate > 0 ? `
    <div class="tlc-section">
      <div class="tlc-title">📊 Pump v3 Avanzado</div>
      ${data.pump_market_cap_usd > 0 ? `<div>Market Cap: <strong>$${Number(data.pump_market_cap_usd).toLocaleString()}</strong></div>` : ''}
      ${data.pump_mcap_score > 0 ? `<div>MCap Score: ${data.pump_mcap_score}/100</div>` : ''}
      ${data.pump_holder_growth_rate > 0 ? `<div>Holder Growth: <strong style="color:#02c076">+${data.pump_holder_growth_rate.toFixed(2)}/min</strong></div>` : ''}
      ${data.pump_holder_growth_score > 0 ? `<div>Growth Score: ${data.pump_holder_growth_score}/100</div>` : ''}
      ${data.pump_lp_growth_percent != null && data.pump_lp_growth_percent !== 0 ? `<div>LP Growth: <span style="color:${data.pump_lp_growth_percent>=0?'#02c076':'#e74c3c'}">${data.pump_lp_growth_percent>0?'+':''}${data.pump_lp_growth_percent.toFixed(1)}%</span></div>` : ''}
      ${data.pump_lp_growth_score > 0 ? `<div>LP Score: ${data.pump_lp_growth_score}/100</div>` : ''}
    </div>` : ''}
    ${data.proxy_ok ? `
    <div class="tlc-section">
      <div class="tlc-title">🔐 Proxy Detection (v5)</div>
      <div>Is Proxy: ${data.proxy_is_proxy ? '<span style="color:#e74c3c">SÍ</span>' : '<span style="color:#02c076">NO ✓</span>'}</div>
      ${data.proxy_type ? `<div>Type: <strong>${data.proxy_type}</strong></div>` : ''}
      <div>Risk: <strong style="color:${data.proxy_risk_level==='safe'?'#02c076':data.proxy_risk_level==='moderate'?'#f0b90b':'#e74c3c'}">${data.proxy_risk_level}</strong></div>
      <div>${data.proxy_has_multisig ? '✅' : '❌'} Multisig</div>
      <div>${data.proxy_has_timelock ? '✅' : '❌'} Timelock</div>
      ${(data.proxy_signals||[]).map(s => `<div>📌 ${s}</div>`).join('')}
    </div>` : ''}
    ${data.stress_ok ? `
    <div class="tlc-section">
      <div class="tlc-title">💪 Stress Test (v5)</div>
      <div>Max Safe: <strong>${(data.stress_max_safe_amount||0).toFixed(3)}</strong> native</div>
      <div>Liq Depth: <strong style="color:${data.stress_liquidity_depth>=60?'#02c076':data.stress_liquidity_depth>=30?'#f0b90b':'#e74c3c'}">${data.stress_liquidity_depth}/100</strong></div>
      <div>Avg Slippage: ${(data.stress_avg_slippage||0).toFixed(1)}%</div>
    </div>` : ''}
    ${data.volatility_ok ? `
    <div class="tlc-section">
      <div class="tlc-title">📈 Volatility (v5)</div>
      <div>Score: <strong>${(data.volatility_score||0).toFixed(2)}</strong></div>
      <div>Slippage rec: <strong style="color:#f0b90b">${(data.volatility_recommended_slippage||12).toFixed(1)}%</strong></div>
    </div>` : ''}
    ${data.ml_pump_ok ? `
    <div class="tlc-section">
      <div class="tlc-title">🤖 ML Predictor (v5)</div>
      <div>Score: <strong style="color:${data.ml_pump_score>=70?'#02c076':data.ml_pump_score>=40?'#f0b90b':'#e74c3c'}">${data.ml_pump_score}/100</strong></div>
      <div>Label: <strong style="color:${data.ml_pump_label==='safe'?'#02c076':data.ml_pump_label==='neutral'?'#f0b90b':'#e74c3c'}">${data.ml_pump_label}</strong></div>
      <div>Confidence: ${((data.ml_pump_confidence||0)*100).toFixed(0)}%</div>
    </div>` : ''}
    ${data.social_ok ? `
    <div class="tlc-section">
      <div class="tlc-title">📱 Social Sentiment (v5)</div>
      <div>Score: <strong style="color:${data.social_sentiment_score>=60?'#02c076':data.social_sentiment_score>=30?'#f0b90b':'#e74c3c'}">${data.social_sentiment_score}/100</strong></div>
      <div>Label: <strong>${data.social_sentiment_label || '—'}</strong></div>
      <div>Platforms: <strong>${data.social_platforms_active || 0}</strong></div>
      ${(data.social_signals||[]).slice(0,4).map(s => `<div>📌 ${s}</div>`).join('')}
    </div>` : ''}
    ${data.anomaly_ok ? `
    <div class="tlc-section">
      <div class="tlc-title">🔍 Anomaly Detection (v5)</div>
      <div>Anomalous: ${data.anomaly_is_anomalous ? '<span style="color:#e74c3c;font-weight:700">SÍ ⚠️</span>' : '<span style="color:#02c076">NO ✓</span>'}</div>
      <div>Score: <strong style="color:${data.anomaly_score>=0.7?'#e74c3c':data.anomaly_score>=0.4?'#f0b90b':'#02c076'}">${(data.anomaly_score||0).toFixed(2)}</strong></div>
      ${data.anomaly_type ? `<div>⚠️ ${data.anomaly_type}</div>` : ''}
    </div>` : ''}
    ${data.whale_ok ? `
    <div class="tlc-section">
      <div class="tlc-title">🐋 Whale Activity (v5)</div>
      <div>Buys: <span style="color:#02c076">${data.whale_total_buys||0}</span> | Sells: <span style="color:#e74c3c">${data.whale_total_sells||0}</span></div>
      <div>Net Flow: <strong style="color:${data.whale_net_flow>=0?'#02c076':'#e74c3c'}">${data.whale_net_flow>=0?'+':''}${(data.whale_net_flow||0).toFixed(2)}</strong> native</div>
      ${data.whale_coordinated ? '<div style="color:#f0b90b;font-weight:700">⚠️ Compra coordinada</div>' : ''}
      ${data.whale_dev_dumping ? '<div style="color:#e74c3c;font-weight:700">⛔ DEV DUMPING</div>' : ''}
      <div>Concentración: ${(data.whale_concentration_pct||0).toFixed(0)}%</div>
      ${(data.whale_signals||[]).slice(0,4).map(s => `<div>📌 ${s}</div>`).join('')}
    </div>` : ''}
    ${data.dev_ml_cluster && data.dev_ml_cluster !== 'neutral' ? `
    <div class="tlc-section">
      <div class="tlc-title">🧬 Dev ML (v5)</div>
      <div>Cluster: <strong style="color:${data.dev_ml_cluster==='legit_dev'?'#02c076':data.dev_ml_cluster==='suspicious'?'#f0b90b':'#e74c3c'}">${data.dev_ml_cluster}</strong></div>
      <div>ML Rep: <strong>${data.dev_ml_reputation||50}/100</strong></div>
      <div>Confidence: ${((data.dev_ml_confidence||0)*100).toFixed(0)}%</div>
    </div>` : ''}
    ${data.backend_buy_available ? `
    <div class="tlc-section">
      <div class="tlc-title">⚡ Backend Executor</div>
      <div style="color:#02c076;font-weight:600">Ready for auto-execution</div>
    </div>` : ''}
    ${data.mev_ok ? `
    <div class="tlc-section">
      <div class="tlc-title">🛡️ MEV Protection (v6)</div>
      <div>Threat: <strong style="color:${data.mev_threat_level==='critical'||data.mev_threat_level==='high'?'#e74c3c':data.mev_threat_level==='medium'?'#f0b90b':'#02c076'}">${(data.mev_threat_level||'none').toUpperCase()}</strong></div>
      <div>Frontrun Risk: ${((data.mev_frontrun_risk||0)*100).toFixed(0)}%</div>
      <div>Sandwich Risk: ${((data.mev_sandwich_risk||0)*100).toFixed(0)}%</div>
      ${data.mev_strategy_used ? `<div>Strategy: <strong>${data.mev_strategy_used}</strong></div>` : ''}
    </div>` : ''}
    ${data.multi_dex_ok ? `
    <div class="tlc-section">
      <div class="tlc-title">🔀 Multi-DEX Route (v6)</div>
      <div>Best DEX: <strong style="color:#1e96ff">${data.best_dex_name||'—'}</strong></div>
      <div>Amount Out: ${(data.best_dex_amount_out||0).toFixed(6)}</div>
    </div>` : ''}
    ${data.ai_regime ? `
    <div class="tlc-section">
      <div class="tlc-title">🧠 AI Optimizer (v6)</div>
      <div>Regime: <strong style="color:${data.ai_regime==='bull'?'#02c076':data.ai_regime==='bear'?'#e74c3c':'#f0b90b'}">${(data.ai_regime||'—').toUpperCase()}</strong></div>
      ${data.ai_suggested_tp ? `<div>Suggested TP: <strong style="color:#02c076">${data.ai_suggested_tp}%</strong></div>` : ''}
      ${data.ai_suggested_sl ? `<div>Suggested SL: <strong style="color:#e74c3c">${data.ai_suggested_sl}%</strong></div>` : ''}
    </div>` : ''}
    ${data.copy_trade_signal ? `
    <div class="tlc-section">
      <div class="tlc-title">📡 Copy Trade (v6)</div>
      <div>Wallet: <strong>${data.copy_trade_wallet||'—'}</strong></div>
      <div>Confidence: ${((data.copy_trade_confidence||0)*100).toFixed(0)}%</div>
    </div>` : ''}
    ${data.rl_ok ? `
    <div class="tlc-section">
      <div class="tlc-title">🧠 RL Learner (v7)</div>
      <div>Decision: <strong style="color:${data.rl_decision==='buy'?'#02c076':data.rl_decision==='sell'?'#e74c3c':'#f0b90b'}">${(data.rl_decision||'hold').toUpperCase()}</strong></div>
      <div>Confidence: ${((data.rl_confidence||0)*100).toFixed(0)}%</div>
      ${data.rl_suggested_tp ? `<div>RL TP: <strong style="color:#02c076">${data.rl_suggested_tp}%</strong></div>` : ''}
      ${data.rl_suggested_sl ? `<div>RL SL: <strong style="color:#e74c3c">${data.rl_suggested_sl}%</strong></div>` : ''}
    </div>` : ''}
    ${data.orderflow_ok ? `
    <div class="tlc-section">
      <div class="tlc-title">📊 Orderflow (v7)</div>
      <div>Organic: <strong style="color:${data.orderflow_organic_score>=60?'#02c076':data.orderflow_organic_score>=30?'#f0b90b':'#e74c3c'}">${(data.orderflow_organic_score||0).toFixed(0)}/100</strong></div>
      <div>Bot %: <strong style="color:${data.orderflow_bot_pct<=20?'#02c076':data.orderflow_bot_pct<=50?'#f0b90b':'#e74c3c'}">${(data.orderflow_bot_pct||0).toFixed(1)}%</strong></div>
      <div>Manipulation: <strong style="color:${data.orderflow_manipulation_risk<=0.3?'#02c076':data.orderflow_manipulation_risk<=0.6?'#f0b90b':'#e74c3c'}">${((data.orderflow_manipulation_risk||0)*100).toFixed(0)}%</strong></div>
    </div>` : ''}
    ${data.sim_v7_ok ? `
    <div class="tlc-section">
      <div class="tlc-title">🎰 Market Simulator (v7)</div>
      <div>Risk: <strong style="color:${data.sim_v7_risk_score<=30?'#02c076':data.sim_v7_risk_score<=60?'#f0b90b':'#e74c3c'}">${(data.sim_v7_risk_score||0).toFixed(0)}/100</strong></div>
      <div>Recommend: <strong style="color:${data.sim_v7_recommendation==='buy'?'#02c076':data.sim_v7_recommendation==='avoid'?'#e74c3c':'#f0b90b'}">${(data.sim_v7_recommendation||'—').toUpperCase()}</strong></div>
      <div>Slippage: ${(data.sim_v7_slippage_pct||0).toFixed(2)}%</div>
      ${data.sim_v7_mev_vulnerable ? '<div style="color:#e74c3c;font-weight:700">⚠️ MEV Vulnerable</div>' : ''}
      <div>Expected P&L: <strong style="color:${data.sim_v7_expected_pnl>=0?'#02c076':'#e74c3c'}">${data.sim_v7_expected_pnl>=0?'+':''}${(data.sim_v7_expected_pnl||0).toFixed(1)}%</strong></div>
    </div>` : ''}
    ${data.strategy_ok ? `
    <div class="tlc-section">
      <div class="tlc-title">🧬 Auto Strategy (v7)</div>
      <div>Decision: <strong style="color:${data.strategy_decision==='buy'?'#02c076':data.strategy_decision==='pass'?'#e74c3c':'#f0b90b'}">${(data.strategy_decision||'none').toUpperCase()}</strong></div>
      <div>Confidence: ${((data.strategy_confidence||0)*100).toFixed(0)}%</div>
      ${data.strategy_name ? `<div>Strategy: <strong>${data.strategy_name}</strong></div>` : ''}
    </div>` : ''}
    ${data.launch_ok ? `
    <div class="tlc-section">
      <div class="tlc-title">🔮 Launch Scanner (v7)</div>
      <div>Stage: <strong>${(data.launch_stage||'unknown').toUpperCase()}</strong></div>
      <div>Risk: <strong style="color:${data.launch_risk_score<=30?'#02c076':data.launch_risk_score<=60?'#f0b90b':'#e74c3c'}">${data.launch_risk_score||50}/100</strong></div>
      <div>Priority: <strong style="color:${data.launch_snipe_priority>=60?'#02c076':data.launch_snipe_priority>=30?'#f0b90b':'#e74c3c'}">${data.launch_snipe_priority||0}/100</strong></div>
    </div>` : ''}
    ${data.mempool_v7_ok ? `
    <div class="tlc-section">
      <div class="tlc-title">📡 Mempool v7</div>
      <div>Pressure: <strong style="color:${data.mempool_v7_net_pressure>=0?'#02c076':'#e74c3c'}">${data.mempool_v7_net_pressure>=0?'+':''}${(data.mempool_v7_net_pressure||0).toFixed(2)}</strong></div>
      <div>Frontrun Risk: <strong style="color:${data.mempool_v7_frontrun_risk<=0.3?'#02c076':data.mempool_v7_frontrun_risk<=0.6?'#f0b90b':'#e74c3c'}">${((data.mempool_v7_frontrun_risk||0)*100).toFixed(0)}%</strong></div>
      <div>Pending: 🟢${data.mempool_v7_pending_buys||0} buys / 🔴${data.mempool_v7_pending_sells||0} sells</div>
    </div>` : ''}
    ${data.whale_network_ok ? `
    <div class="tlc-section">
      <div class="tlc-title">🕸️ Whale Network (v7)</div>
      <div>Coordination: <strong style="color:${data.whale_network_coordination<=0.3?'#02c076':data.whale_network_coordination<=0.6?'#f0b90b':'#e74c3c'}">${((data.whale_network_coordination||0)*100).toFixed(0)}%</strong></div>
      <div>Sybil Risk: <strong style="color:${data.whale_network_sybil_risk<=0.3?'#02c076':data.whale_network_sybil_risk<=0.6?'#f0b90b':'#e74c3c'}">${((data.whale_network_sybil_risk||0)*100).toFixed(0)}%</strong></div>
      <div>Smart Money: <strong style="color:${data.whale_smart_money_sentiment==='bullish'?'#02c076':data.whale_smart_money_sentiment==='bearish'?'#e74c3c':'#f0b90b'}">${(data.whale_smart_money_sentiment||'neutral').toUpperCase()}</strong></div>
      <div>Clusters: ${data.whale_network_clusters||0}</div>
    </div>` : ''}
  </div>
  <div class="tlc-reasons">
    <div class="tlc-title">🚩 Risk Reasons</div>
    ${reasons}
  </div>
</div>`.trim();
    }

    /* ═══════════════════════════════════════════════════════════════
     *  WebSocket
     * ═══════════════════════════════════════════════════════════════ */
    function connectWS() {
        const proto = location.protocol === "https:" ? "wss" : "ws";
        ws = new WebSocket(`${proto}://${location.host}/ws/sniper/`);

        ws.onopen = () => {
            addFeed("Connected to Sniper server.", "system");
            ws.send(JSON.stringify({ action: "get_state" }));
        };

        ws.onclose = () => {
            addFeed("Disconnected from server. Reconnecting in 3s…", "warn");
            setTimeout(connectWS, 3000);
        };

        ws.onerror = () => {
            addFeed("WebSocket error.", "error");
        };

        ws.onmessage = (e) => {
            try {
                const msg = JSON.parse(e.data);
                handleMessage(msg);
            } catch (_) {}
        };
    }

    function sendWS(obj) {
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify(obj));
        }
    }

    /* ═══════════════════════════════════════════════════════════════
     *  Swap execution (auto-buy / auto-sell / manual)
     * ═══════════════════════════════════════════════════════════════ */

    /** Tracks tokens currently being bought to avoid double-buys. */
    const _buyingTokens = new Set();

    /**
     * Execute a BUY swap: native → token via DEX router.
     * @param {string}  tokenAddress  - The token contract address.
     * @param {string}  symbol        - Token symbol for logging.
     * @param {number}  amountNative  - Amount of native coin to spend (e.g. 0.05 BNB).
     * @param {number}  slippage      - Slippage tolerance in % (e.g. 12).
     * @param {string}  [source]      - "auto" | "manual" for feed labels.
     * @returns {Promise<{success:boolean, txHash?:string}>}
     */
    async function executeSnipeBuy(tokenAddress, symbol, amountNative, slippage, source = "auto", autoHoldHours = 0) {
        if (_buyingTokens.has(tokenAddress)) {
            addFeed(`⏳ Already buying ${symbol} — skipping duplicate.`, "warn");
            return { success: false };
        }
        _buyingTokens.add(tokenAddress);

        const label = source === "auto" ? "🤖 AUTO-BUY" : "🎯 MANUAL BUY";

        try {
            // 1) Ensure wallet + TxModule are ready
            if (!(await ensureTxReady())) {
                addFeed(`${label}: ❌ Wallet not connected — cannot swap.`, "error");
                return { success: false };
            }

            const native = nativeSymbol();
            addFeed(`${label}: Swapping ${amountNative} ${native} → ${symbol} (slippage ${slippage}%)…`, "system");

            // 2) Execute swap: tokenIn="" means native coin
            const receipt = await TxModule.swap("", tokenAddress, amountNative.toString(), slippage);

            const txHash = receipt.hash || receipt.transactionHash || "";
            const txUrl  = explorerTxUrl(txHash);
            addFeedHTML(`${label}: ✅ <strong>COMPRADO!</strong> ${amountNative} ${native} → ${symbol} | Block #${receipt.blockNumber} | <a href="${txUrl}" target="_blank" style="color:#02c076;text-decoration:underline;">${txHash.slice(0,14)}…</a>`, "opportunity");

            // 3) Register the position in the backend for TP/SL tracking
            const buyPriceUsd = await _estimateBuyPrice(tokenAddress, amountNative);
            sendWS({
                action: "register_snipe",
                token:     tokenAddress,
                symbol:    symbol,
                buy_price: buyPriceUsd,
                amount:    amountNative,
                tx:        txHash,
                auto_hold_hours: autoHoldHours,
            });

            return { success: true, txHash };
        } catch (err) {
            const reason = err.reason || err.shortMessage || err.message || String(err);
            addFeed(`${label}: ❌ COMPRA FALLIDA — ${reason}`, "error");
            console.error("executeSnipeBuy error:", err);
            return { success: false };
        } finally {
            _buyingTokens.delete(tokenAddress);
        }
    }

    /**
     * Execute a SELL swap: token → native via DEX router.
     * Sells ALL tokens held for the given token address.
     * @param {string}  tokenAddress  - Token to sell.
     * @param {string}  symbol        - Token symbol for logging.
     * @param {number}  slippage      - Slippage tolerance in %.
     * @param {string}  [reason]      - "tp" | "sl" | "manual" for feed labels.
     * @returns {Promise<{success:boolean, txHash?:string}>}
     */
    async function executeSnipeSell(tokenAddress, symbol, slippage, reason = "manual") {
        const label = reason === "tp"  ? "🏆 TAKE-PROFIT SELL"
                    : reason === "sl"  ? "🛑 STOP-LOSS SELL"
                    :                    "💰 MANUAL SELL";

        try {
            if (!(await ensureTxReady())) {
                addFeed(`${label}: ❌ Wallet not connected — cannot sell.`, "error");
                return { success: false };
            }

            // Get token balance
            const tokenContract = new ethers.Contract(
                tokenAddress,
                ["function balanceOf(address) view returns (uint256)", "function decimals() view returns (uint8)"],
                ethersProvider
            );
            const [rawBal, decimals] = await Promise.all([
                tokenContract.balanceOf(wallet.address),
                tokenContract.decimals(),
            ]);
            const tokenBal = ethers.formatUnits(rawBal, decimals);

            if (parseFloat(tokenBal) <= 0) {
                addFeed(`${label}: ⚠️ No ${symbol} balance to sell.`, "warn");
                return { success: false };
            }

            const native = nativeSymbol();
            addFeed(`${label}: Selling ${parseFloat(tokenBal).toFixed(4)} ${symbol} → ${native}…`, "system");

            // Execute sell: tokenIn = token, tokenOut = "" (native)
            const receipt = await TxModule.swap(tokenAddress, "", tokenBal, slippage);

            const txHash = receipt.hash || receipt.transactionHash || "";
            const txUrl  = explorerTxUrl(txHash);
            addFeedHTML(`${label}: ✅ <strong>VENDIDO!</strong> ${parseFloat(tokenBal).toFixed(4)} ${symbol} → ${native} | Block #${receipt.blockNumber} | <a href="${txUrl}" target="_blank" style="color:#02c076;text-decoration:underline;">${txHash.slice(0,14)}…</a>`, "opportunity");

            // Mark position as sold in backend
            sendWS({
                action: "mark_snipe_sold",
                token:   tokenAddress,
                sell_tx: txHash,
            });

            return { success: true, txHash };
        } catch (err) {
            const reason2 = err.reason || err.shortMessage || err.message || String(err);
            addFeed(`${label}: ❌ VENTA FALLIDA — ${reason2}`, "error");
            console.error("executeSnipeSell error:", err);
            return { success: false };
        }
    }

    /**
     * Try to estimate the buy price in USD using DexScreener.
     * Falls back to 0 if unavailable (backend will update it via polling).
     */
    async function _estimateBuyPrice(tokenAddress, amountNative) {
        try {
            const chain = (wallet.chain_id || 56) === 56 ? "bsc" : "ethereum";
            const resp = await fetch(`https://api.dexscreener.com/latest/dex/tokens/${tokenAddress}`, { signal: AbortSignal.timeout(3000) });
            if (!resp.ok) return 0;
            const json = await resp.json();
            const pair = json.pairs?.[0];
            if (pair && pair.priceUsd) return parseFloat(pair.priceUsd);
        } catch (_) {}
        return 0;
    }

    /* ═══════════════════════════════════════════════════════════════
     *  Message handler
     * ═══════════════════════════════════════════════════════════════ */
    function handleMessage(msg) {
        const type = msg.type;
        const data = msg.data || {};

        switch (type) {
            case "full_state":
                syncState(data);
                break;

            case "bot_started":
            case "bot_status":
                if (data.status === "running" || type === "bot_started") {
                    setBotRunning(true);
                    if (data.native_price) statPrice.textContent = "$" + data.native_price;
                    addFeed(`Bot running on chain ${data.chain_id || ""}`, "system");
                } else if (data.status === "stopped") {
                    setBotRunning(false);
                    addFeed("Bot stopped.", "system");
                } else if (data.status === "starting") {
                    addFeed("Bot starting…", "system");
                }
                break;

            case "bot_stopped":
                setBotRunning(false);
                addFeed("Bot stopped.", "system");
                break;

            case "heartbeat":
                statBlock.textContent = data.block ? Number(data.block).toLocaleString() : "—";
                statPairs.textContent = data.pairs_detected || 0;
                statActive.textContent = data.active_snipes || 0;
                if (data.native_price) statPrice.textContent = "$" + data.native_price;
                pipeMempoolCnt.textContent = data.block ? Number(data.block).toLocaleString() : "—";
                // Sync WS indicator
                {
                    const wsIcon = data.sync_ws_active ? "🟢" : "🔴";
                    const wsText = data.sync_ws_active ? `WS ${data.sync_pairs_tracked || 0} pairs` : "WS off";
                    if (pipeSnipeCnt) pipeSnipeCnt.title = `${wsIcon} ${wsText}`;
                }
                break;

            /* ── Verbose scan events ─────────────────────────── */
            case "scan_info":
                if (data.chain_name) {
                    addFeed(`⛓️ Chain: ${data.chain_name} (ID ${data.chain_id})`, "system");
                    addFeed(`🌐 RPC: ${data.rpc_http}`, "system");
                    addFeed(`🏭 Factory: ${data.factory_label} (${shortAddr(data.factory)})`, "system");
                    addFeed(`🪙 Base token: ${data.weth_label} (${shortAddr(data.weth)})`, "system");
                    addFeed(`📡 Listening for: ${data.event_topic}`, "system");
                } else if (data.message) {
                    addFeed(`ℹ️ ${data.message}`, "system");
                }
                break;

            case "scan_block":
                {
                    const behind = data.behind || 0;
                    const behindText = behind > 0 ? ` (${behind} blocks behind)` : " (real-time)";
                    addFeed(`🔎 Scanning blocks #${Number(data.from_block).toLocaleString()} → #${Number(data.to_block).toLocaleString()} (${data.blocks_count} blocks)${behindText}`, "scan");
                }
                break;

            case "scan_result":
                if (data.events_found > 0) {
                    addFeed(`✨ Found ${data.events_found} PairCreated event(s) in blocks #${Number(data.from_block).toLocaleString()}-#${Number(data.to_block).toLocaleString()}! (Total: ${data.total_events} events in ${Number(data.total_scanned).toLocaleString()} blocks)`, "detect");
                } else {
                    addFeed(`📭 No new pairs in blocks #${Number(data.from_block).toLocaleString()}-#${Number(data.to_block).toLocaleString()} — Total scanned: ${Number(data.total_scanned).toLocaleString()} blocks`, "scan");
                }
                break;

            case "scan_idle":
                // Don't spam feed with idle messages, just update visually
                break;

            case "scan_error":
                addFeed(`⚠️ ${data.message}`, "error");
                break;

            case "new_pair_raw":
                pipeStats.detected++;
                pipeDetectCnt.textContent = pipeStats.detected;
                flashPipe("pipe-detect");
                addFeed(`🔍 New pair detected @ block ${Number(data.block).toLocaleString()} — analyzing…`, "detect");
                break;

            case "liquidity_check":
                pipeStats.liquidity++;
                pipeLiqCnt.textContent = pipeStats.liquidity;
                flashPipe("pipe-liquidity");
                break;

            case "token_detected":
                {
                    console.log("[TOKEN_DETECTED]", JSON.stringify(data, null, 2));
                    const cardType = data.risk === "danger" ? "error" : data.risk === "safe" ? "good" : "warn";

                    // If a feed card for this token already exists, update it in-place
                    const existingCard = feedDiv.querySelector(`.token-log-card[data-token="${data.token}"]`);
                    if (existingCard) {
                        const tmp = document.createElement('div');
                        tmp.innerHTML = buildTokenLogCard(data);
                        const newCard = tmp.firstElementChild;
                        // Replace the card inside its feed-line wrapper
                        const feedLine = existingCard.closest('.feed-line');
                        if (feedLine) {
                            feedLine.className = `feed-line feed-${cardType}`;
                            const ts = feedLine.textContent.match(/^\[.*?\]/);
                            feedLine.innerHTML = `${ts ? ts[0] + ' ' : ''}${newCard.outerHTML}`;
                        } else {
                            existingCard.replaceWith(newCard);
                        }
                    } else {
                        // First time seeing this token — add new card
                        addFeedHTML(buildTokenLogCard(data), cardType);
                        pipeStats.analyzed++;
                        pipeAnalyzeCnt.textContent = pipeStats.analyzed;
                        flashPipe("pipe-analyze");
                    }

                    // Upsert in detected list & table
                    addAllDetected(data);

                    // Auto-update log modal if open
                    _refreshLogModal(data);
                }
                break;

            case "contract_analysis":
                // Already shown in token_detected log card — skip duplicate
                break;

            case "token_updated":
                {
                    console.log("[TOKEN_UPDATED]", data.symbol, {
                        liquidity_usd: data.liquidity_usd,
                        liquidity_native: data.liquidity_native,
                        dexscreener_liquidity: data.dexscreener_liquidity,
                        dexscreener_pairs: data.dexscreener_pairs,
                        dexscreener_volume_24h: data.dexscreener_volume_24h,
                        price_change_h1: data.price_change_h1,
                        price_change_h24: data.price_change_h24,
                        lp_locked: data.lp_locked,
                        lp_lock_percent: data.lp_lock_percent,
                        goplus_ok: data.goplus_ok,
                        dexscreener_ok: data.dexscreener_ok,
                        tokensniffer_ok: data.tokensniffer_ok,
                        risk: data.risk,
                        has_liquidity: data.has_liquidity,
                    });
                    // Real-time refresh of a previously detected token
                    const idx = allDetected.findIndex(d => d.token === data.token);
                    if (idx !== -1) {
                        // Preserve the original block number
                        data.block = data.block || allDetected[idx].block;
                        allDetected[idx] = data;
                        renderDetectedTable();
                    }
                    // Live-update the feed card so DexScreener/LP/APIs refresh in place
                    const feedCard = feedDiv.querySelector(`.token-log-card[data-token="${data.token}"]`);
                    if (feedCard) {
                        const tmp = document.createElement('div');
                        tmp.innerHTML = buildTokenLogCard(data);
                        const newCard = tmp.firstElementChild;
                        feedCard.replaceWith(newCard);
                    }
                    // Refresh log modal if it shows this token
                    _refreshLogModal(data);
                }
                break;

            case "snipe_opportunity":
                pipeStats.sniped++;
                pipeSnipeCnt.textContent = pipeStats.sniped;
                flashPipe("pipe-snipe");
                {
                    const lockInfo = data.lp_locked ? ` | 🔒 LP ${(data.lp_lock_percent||0).toFixed(0)}% locked (${(data.lp_lock_hours||0).toFixed(1)}h)` : "";
                    const riskInfo = data.risk_engine_score > 0 ? ` | 🎯 Risk: ${data.risk_engine_score}/100 (${data.risk_engine_action})` : "";
                    const devInfo = data.dev_label ? ` | 👨‍💻 Dev: ${data.dev_label} (${data.dev_score})` : "";
                    addFeed(`🎯 OPPORTUNITY: ${data.symbol} — $${data.liquidity_usd} liquidity — Risk: ${data.risk}${lockInfo}${riskInfo}${devInfo}`, "opportunity");
                }
                if (data.auto_buy && !data.backend_buy_available) {
                    // Frontend auto-buy only when backend executor is not active
                    addFeed(`🤖 Auto-buy is ON — executing swap…`, "system");
                    const buyAmt  = parseFloat(setBuyAmount.value) || 0.05;
                    const slip    = parseFloat(setSlippage.value)  || 12;
                    const autoHold = data.auto_hold_hours || 0;
                    executeSnipeBuy(data.token, data.symbol, buyAmt, slip, "auto", autoHold);
                } else if (data.auto_buy && data.backend_buy_available) {
                    addFeed(`⚡ Backend executor handling auto-buy for ${data.symbol}…`, "system");
                }
                break;

            case "snipe_update":
                updateSnipeRow(data);
                const pnlIcon = data.pnl_percent >= 0 ? "📈" : "📉";
                // Don't spam feed with every update, only log significant changes
                break;

            case "take_profit_alert":
                addFeed(`🏆 TAKE PROFIT HIT! ${data.symbol} at +${data.pnl_percent}% (target: +${data.target}%)`, "opportunity");
                flashPipe("pipe-profit");
                pipeProfitCnt.textContent = (parseInt(pipeProfitCnt.textContent) || 0) + 1;
                {
                    const slip = parseFloat(setSlippage.value) || 12;
                    executeSnipeSell(data.token, data.symbol, slip, "tp");
                }
                break;

            case "stop_loss_alert":
                addFeed(`🛑 STOP LOSS HIT! ${data.symbol} at ${data.pnl_percent}% (limit: ${data.target}%)`, "error");
                flashPipe("pipe-profit");
                {
                    const slip = parseFloat(setSlippage.value) || 12;
                    executeSnipeSell(data.token, data.symbol, slip, "sl");
                }
                break;

            case "time_limit_alert":
                addFeed(`⏰ TIME LIMIT! ${data.symbol} — ${data.held_hours}h/${data.max_hold_hours}h — P&L: ${data.pnl_percent}%`, "opportunity");
                flashPipe("pipe-profit");
                {
                    const slip = parseFloat(setSlippage.value) || 12;
                    executeSnipeSell(data.token, data.symbol, slip, "time");
                }
                break;

            case "settings_updated":
                addFeed("⚙️ Settings updated.", "system");
                break;

            case "snipe_registered":
                addFeed(`✅ Position registered: ${data.symbol} — tracking TP/SL`, "good");
                // Add to local active snipes and re-render table
                activeSnipes.push(data);
                renderSnipes();
                break;

            case "snipe_sold":
                addFeed(`💰 Position sold: ${data.symbol}`, "good");
                {
                    const idx = activeSnipes.findIndex(s => s.token_address === data.token_address);
                    if (idx !== -1) {
                        activeSnipes[idx].status = "sold";
                        activeSnipes[idx].sell_tx = data.sell_tx || "";
                    }
                    renderSnipes();
                }
                break;

            /* ── Professional v2 events ─────────────────── */
            case "mempool_event":
                {
                    const tkn = data.token ? shortAddr(data.token) : "pending";
                    const bnb = data.value_bnb ? ` — ${data.value_bnb} BNB` : "";
                    addFeed(`📡 Mempool: ${data.method} — token ${tkn}${bnb} (tx: ${shortAddr(data.tx_hash)})`, "detect");
                }
                break;

            case "rug_alert":
                {
                    const levelIcon = data.level === "EMERGENCY" ? "🚨" : data.level === "WARNING" ? "⚠️" : "ℹ️";
                    const feedType = data.level === "EMERGENCY" ? "error" : data.level === "WARNING" ? "warn" : "system";
                    addFeed(`${levelIcon} RUG ALERT [${data.level}]: ${data.symbol} — ${data.message}`, feedType);

                    // Auto-sell on EMERGENCY
                    if (data.level === "EMERGENCY" && data.action === "sell_immediately") {
                        const slip = parseFloat(setSlippage.value) || 12;
                        addFeed(`🚨 Emergency sell triggered for ${data.symbol}!`, "error");
                        executeSnipeSell(data.token, data.symbol, slip, "rug");
                    }
                }
                break;

            case "prelaunch_detected":
                addFeed(`🔍 Pre-launch: ${data.symbol || 'Unknown'} (${shortAddr(data.address)}) — Launch prob: ${data.launch_probability}%${data.router_approved ? ' ✅ Router approved!' : ''}`, "detect");
                break;

            case "smart_money_signal":
                addFeed(`🐋 Smart Money: ${data.wallets || 0} whale(s) buying ${data.symbol || shortAddr(data.token)} — confidence ${((data.confidence||0)*100).toFixed(0)}%`, "opportunity");
                break;

            /* ── Professional v3 events ─────────────────── */
            case "backend_buy_executed": {
                const mevTag = data.mev_protected ? ` 🛡️MEV(${data.mev_strategy||'private'})` : '';
                addFeedHTML(`⚡ <strong>BACKEND BUY</strong>: ${data.symbol} — ${data.amount_native} ${nativeSymbol()}${mevTag} | Gas: ${data.gas_used} | ${data.execution_ms}ms | <a href="${explorerTxUrl(data.tx_hash)}" target="_blank" style="color:#02c076;text-decoration:underline;">${(data.tx_hash||'').slice(0,14)}…</a>`, "opportunity");
                break;
            }

            case "backend_buy_failed":
                addFeed(`⚡ Backend buy FAILED for ${data.symbol}: ${data.error}`, "error");
                break;

            case "error":
                addFeed(`❌ ${data.message}`, "error");
                break;

            case "dashboard":
            case "dashboard_response":
                updateDashboard(data);
                break;

            case "alert_config_updated": {
                addFeed("🔔 Alert config updated.", "system");
                // Sync toggles with server state
                const ac = data;
                const tgT = $("alert-telegram"), dcT = $("alert-discord"), emT = $("alert-email");
                if (tgT && ac.telegram_enabled !== undefined) tgT.checked = ac.telegram_enabled;
                if (dcT && ac.discord_enabled  !== undefined) dcT.checked = ac.discord_enabled;
                if (emT && ac.email_enabled    !== undefined) emT.checked = ac.email_enabled;
                const dcRow = $("discord-webhook-row");
                if (dcRow) dcRow.style.display = ac.discord_enabled ? "flex" : "none";
                break;
            }

            case "webhook_test_result": {
                const btn = $("btn-test-webhook");
                if (btn) { btn.disabled = false; btn.textContent = "🔔 Test Webhook"; }
                const parts = [];
                if (data.discord === true) parts.push("✅ Discord OK");
                else if (data.discord === false) parts.push(`❌ Discord falló${data.discord_error ? ": " + data.discord_error : ""}`);
                else parts.push("⚪ Discord no configurado");
                if (data.telegram === true) parts.push("✅ Telegram OK");
                else if (data.telegram === false) parts.push(`❌ Telegram falló${data.telegram_error ? ": " + data.telegram_error : ""}`);
                else parts.push("⚪ Telegram no configurado");
                if (data.error) parts.push(`❌ Error: ${data.error}`);
                addFeed(`🔔 Test webhook: ${parts.join(" | ")}`, data.discord || data.telegram ? "info" : "error");
                break;
            }

            case "api_health": {
                const apis = data.apis || {};
                for (const [name, api] of Object.entries(apis)) {
                    const dot = $(`api-dot-${name}`);
                    const st  = $(`api-st-${name}`);
                    const lat = $(`api-lat-${name}`);
                    if (!dot) continue;
                    const status = api.status || "unknown";
                    dot.className = `api-dot ${status}`;
                    if (st) {
                        st.textContent = status.charAt(0).toUpperCase() + status.slice(1);
                        st.className = `api-status ${status}`;
                    }
                    if (lat) {
                        const ms = api.avg_latency_ms;
                        lat.textContent = ms > 0 ? `${Math.round(ms)}ms` : "—";
                    }
                }
                const cache = data.cache || {};
                const hrEl = $("api-cache-hit-rate");
                const enEl = $("api-cache-entries");
                if (hrEl) hrEl.textContent = cache.hit_rate != null ? `${(cache.hit_rate * 100).toFixed(1)}%` : "—";
                if (enEl) enEl.textContent = cache.entries || 0;
                break;
            }
        }
    }

    /* ═══════════════════════════════════════════════════════════════
     *  Sync full state
     * ═══════════════════════════════════════════════════════════════ */
    function syncState(state) {
        if (!state) return;

        setBotRunning(!!state.running);

        if (state.settings) {
            if (setMinLiq)     setMinLiq.value     = state.settings.min_liquidity_usd || 5000;
            if (setMaxBuyTax)  setMaxBuyTax.value  = state.settings.max_buy_tax || 10;
            if (setMaxSellTax) setMaxSellTax.value = state.settings.max_sell_tax || 15;
            if (setBuyAmount)  setBuyAmount.value  = state.settings.buy_amount_native || 0.05;
            if (setTP)         setTP.value         = state.settings.take_profit || 40;
            if (setSL)         setSL.value         = state.settings.stop_loss || 15;
            if (setSlippage)   setSlippage.value   = state.settings.slippage || 12;
            if (setMaxHold)    setMaxHold.value    = state.settings.max_hold_hours || 0;
            if (setOnlySafe)   setOnlySafe.checked = state.settings.only_safe !== false;
            if (setAutoBuy)    setAutoBuy.checked  = !!state.settings.auto_buy;
            if (setMaxConcurrent) setMaxConcurrent.value = state.settings.max_concurrent || 5;
            if (setBlockRange)    setBlockRange.value    = state.settings.block_range || 5;
            if (setPollInterval)  setPollInterval.value  = state.settings.poll_interval || 1.5;
        }

        // Sync alert toggles from server state
        if (state.alert_stats && state.alert_stats.channels) {
            const ch = state.alert_stats.channels;
            const tgT = $("alert-telegram"), dcT = $("alert-discord"), emT = $("alert-email");
            if (tgT && ch.telegram !== undefined) tgT.checked = ch.telegram;
            if (dcT && ch.discord  !== undefined) dcT.checked = ch.discord;
            if (emT && ch.email    !== undefined) emT.checked = ch.email;
            const dcRow = $("discord-webhook-row");
            if (dcRow) dcRow.style.display = ch.discord ? "flex" : "none";
        }

        // Replay detected pairs into table
        if (state.detected_pairs) {
            allDetected = [];
            state.detected_pairs.forEach(p => {
                const ti = p.token_info || {};
                allDetected.push({
                    token: p.new_token,
                    symbol: ti.symbol || "?",
                    name: ti.name || "Unknown",
                    risk: ti.risk || "unknown",
                    buy_tax: ti.buy_tax ?? null,
                    sell_tax: ti.sell_tax ?? null,
                    is_honeypot: !!ti.is_honeypot,
                    has_owner: !!ti.has_owner,
                    is_mintable: !!ti.is_mintable,
                    has_blacklist: !!ti.has_blacklist,
                    can_pause_trading: !!ti.can_pause_trading,
                    is_proxy: !!ti.is_proxy,
                    has_hidden_owner: !!ti.has_hidden_owner,
                    can_self_destruct: !!ti.can_self_destruct,
                    cannot_sell_all: !!ti.cannot_sell_all,
                    owner_can_change_balance: !!ti.owner_can_change_balance,
                    is_open_source: !!ti.is_open_source,
                    holder_count: ti.holder_count || 0,
                    total_supply: ti.total_supply || 0,
                    risk_reasons: ti.risk_reasons || [],
                    // API status
                    goplus_ok: !!ti._goplus_ok,
                    honeypot_ok: !!ti._honeypot_ok,
                    dexscreener_ok: !!ti._dexscreener_ok,
                    coingecko_ok: !!ti._coingecko_ok,
                    tokensniffer_ok: !!ti._tokensniffer_ok,
                    // Cross-platform
                    listed_coingecko: !!ti.listed_coingecko,
                    coingecko_id: ti.coingecko_id || "",
                    dexscreener_pairs: ti.dexscreener_pairs || 0,
                    dexscreener_volume_24h: ti.dexscreener_volume_24h || 0,
                    dexscreener_liquidity: ti.dexscreener_liquidity || 0,
                    dexscreener_buys_24h: ti.dexscreener_buys_24h || 0,
                    dexscreener_sells_24h: ti.dexscreener_sells_24h || 0,
                    dexscreener_age_hours: ti.dexscreener_age_hours || 0,
                    has_social_links: !!ti.has_social_links,
                    has_website: !!ti.has_website,
                    tokensniffer_score: ti.tokensniffer_score ?? -1,
                    tokensniffer_is_scam: !!ti.tokensniffer_is_scam,
                    // v2 analysis
                    pump_score: ti.pump_score ?? null,
                    pump_grade: ti.pump_grade || '',
                    pump_signals: ti.pump_signals || [],
                    simulation_ok: !!ti._simulation_ok,
                    sim_can_buy: !!ti.sim_can_buy,
                    sim_can_sell: !!ti.sim_can_sell,
                    sim_buy_tax: ti.sim_buy_tax ?? 0,
                    sim_sell_tax: ti.sim_sell_tax ?? 0,
                    sim_is_honeypot: !!ti.sim_is_honeypot,
                    sim_honeypot_reason: ti.sim_honeypot_reason || '',
                    bytecode_ok: !!ti._bytecode_ok,
                    bytecode_size: ti.bytecode_size || 0,
                    bytecode_has_selfdestruct: !!ti.bytecode_has_selfdestruct,
                    bytecode_has_delegatecall: !!ti.bytecode_has_delegatecall,
                    bytecode_is_proxy: !!ti.bytecode_is_proxy,
                    bytecode_flags: ti.bytecode_flags || [],
                    smart_money_buyers: ti.smart_money_buyers || 0,
                    smart_money_confidence: ti.smart_money_confidence || 0,
                    // v3 analysis
                    dev_ok: !!ti._dev_ok,
                    dev_score: ti.dev_score || 0,
                    dev_label: ti.dev_label || '',
                    dev_is_tracked: !!ti.dev_is_tracked,
                    dev_total_launches: ti.dev_total_launches || 0,
                    dev_successful_launches: ti.dev_successful_launches || 0,
                    dev_rug_pulls: ti.dev_rug_pulls || 0,
                    dev_best_multiplier: ti.dev_best_multiplier || 0,
                    dev_is_serial_scammer: !!ti.dev_is_serial_scammer,
                    risk_engine_ok: !!ti._risk_engine_ok,
                    risk_engine_score: ti.risk_engine_score || 0,
                    risk_engine_action: ti.risk_engine_action || '',
                    risk_engine_confidence: ti.risk_engine_confidence || 0,
                    risk_engine_hard_stop: !!ti.risk_engine_hard_stop,
                    risk_engine_signals: ti.risk_engine_signals || [],
                    pump_mcap_score: ti.pump_mcap_score || 0,
                    pump_market_cap_usd: ti.pump_market_cap_usd || 0,
                    pump_holder_growth_rate: ti.pump_holder_growth_rate || 0,
                    pump_lp_growth_percent: ti.pump_lp_growth_percent || 0,
                    pump_holder_growth_score: ti.pump_holder_growth_score || 0,
                    pump_lp_growth_score: ti.pump_lp_growth_score || 0,
                    backend_buy_available: !!ti.backend_buy_available,
                    // Liquidity
                    liquidity_usd: p.liquidity_usd || 0,
                    has_liquidity: (p.liquidity_usd || 0) >= parseFloat(setMinLiq.value || 5000),
                    pair: p.pair_address,
                    block: p.block_number || "?",
                });
            });
            renderDetectedTable();
        }

        // Active snipes
        if (state.active_snipes) {
            activeSnipes = state.active_snipes;
            renderSnipes();
        }

        if (state.native_price_usd) {
            statPrice.textContent = "$" + state.native_price_usd;
        }

        // v4: Sync dashboard data
        if (state.metrics_dashboard) {
            updateDashboard(state.metrics_dashboard);
        }
        if (state.resource_stats) {
            updateResourceBar(state.resource_stats);
        }

        // v6: sync module status chips
        if (state.multi_dex_stats) {
            const dexChip = document.getElementById("v6-dex-chip");
            if (dexChip) {
                dexChip.querySelector("span").textContent = state.multi_dex_stats.current_chain_dexes || 0;
                dexChip.classList.toggle("active", state.multi_dex_stats.current_chain_dexes > 0);
            }
        }
        if (state.ai_optimizer_stats) {
            const aiChip = document.getElementById("v6-ai-chip");
            if (aiChip) {
                aiChip.querySelector("span").textContent = state.ai_optimizer_stats.regime || "—";
                aiChip.classList.add("active");
            }
        }
        if (state.copy_trader_stats) {
            const copyChip = document.getElementById("v6-copy-chip");
            if (copyChip) {
                const wallets = state.copy_trader_stats.tracked_wallets || 0;
                copyChip.querySelector("span").textContent = wallets > 0 ? wallets : "OFF";
                copyChip.classList.toggle("active", wallets > 0);
            }
        }
        if (state.mev_stats) {
            const mevChip = document.getElementById("v6-mev-chip");
            if (mevChip) {
                mevChip.querySelector("span").textContent = "ON";
                mevChip.classList.add("active");
            }
        }

        // v7: sync module status chips
        if (state.rl_learner_stats) {
            const chip = document.getElementById("v7-rl-chip");
            if (chip) { chip.querySelector("span").textContent = "ON"; chip.classList.add("active"); }
        }
        if (state.orderflow_stats) {
            const chip = document.getElementById("v7-orderflow-chip");
            if (chip) { chip.querySelector("span").textContent = state.orderflow_stats.total_analyses || 0; chip.classList.add("active"); }
        }
        if (state.market_simulator_stats) {
            const chip = document.getElementById("v7-sim-chip");
            if (chip) { chip.querySelector("span").textContent = state.market_simulator_stats.total_simulations || 0; chip.classList.add("active"); }
        }
        if (state.auto_strategy_stats) {
            const chip = document.getElementById("v7-strategy-chip");
            if (chip) { chip.querySelector("span").textContent = state.auto_strategy_stats.total_strategies || 0; chip.classList.add("active"); }
        }
        if (state.launch_scanner_stats) {
            const chip = document.getElementById("v7-launch-chip");
            if (chip) {
                const pending = state.launch_scanner_stats.pending_launches || 0;
                chip.querySelector("span").textContent = pending > 0 ? pending : "ON";
                chip.classList.add("active");
            }
        }
        if (state.mempool_v7_stats) {
            const chip = document.getElementById("v7-mempool-chip");
            if (chip) {
                chip.querySelector("span").textContent = state.mempool_v7_stats.total_alerts || 0;
                chip.classList.add("active");
            }
        }
        if (state.whale_graph_stats) {
            const chip = document.getElementById("v7-whale-chip");
            if (chip) {
                chip.querySelector("span").textContent = state.whale_graph_stats.total_wallets || 0;
                chip.classList.add("active");
            }
        }
    }

    /* ═══════════════════════════════════════════════════════════════
     *  UI Helpers
     * ═══════════════════════════════════════════════════════════════ */
    function setBotRunning(running) {
        botRunning = running;
        botDot.className = running ? "bot-status-dot running" : "bot-status-dot";
        botStatusText.textContent = running ? "Running" : "Stopped";
        btnStart.disabled = running;
        btnStop.disabled = !running;
        chainSelect.disabled = running;

        const chain = chainSelect.value;
        botChainLabel.textContent = chain === "56" ? "BSC" : "ETH";
    }

    function flashPipe(elementId) {
        const el = document.getElementById(elementId);
        if (!el) return;
        el.classList.add("pipe-flash");
        setTimeout(() => el.classList.remove("pipe-flash"), 600);
    }

    // ─── Log modal auto-update logic ──────
    function _refreshLogModal(data) {
        if (!logModal || logModal.style.display === "none") return;

        // auto-follow → always show latest token
        // or if same token is being viewed → update its data
        if (_logAutoFollow || (data.token && data.token === _logModalAddr)) {
            _openLogForData(data);
        }
    }

    function _openLogForData(data) {
        _logModalAddr = data.token;
        logModalTitle.innerHTML = `📋 Log — <strong>${data.symbol || "?"}</strong> (${data.name || "Unknown"})`;
        logModalBody.innerHTML = buildTokenLogCard(data);
        logModal.style.display = "flex";

        // Update auto-follow button state
        const afBtn = document.getElementById("btn-log-auto-follow");
        if (afBtn) {
            afBtn.classList.toggle("active", _logAutoFollow);
            afBtn.textContent = _logAutoFollow ? "⏩ Auto" : "⏸ Auto";
        }

        // Reset re-analyze button when new data arrives
        const raBtn = document.getElementById("btn-log-reanalyze");
        if (raBtn && raBtn.disabled) {
            raBtn.disabled = false;
            raBtn.textContent = "🔄 Re-Analizar";
            raBtn.classList.remove("loading");
        }
    }

    // ─── ALL Detected tokens (liquid + no-liquid) ──────
    function addAllDetected(data) {
        // Upsert: if token already exists, update in place instead of duplicating
        const existIdx = allDetected.findIndex(d => d.token === data.token);
        if (existIdx !== -1) {
            data.block = data.block || allDetected[existIdx].block;
            allDetected[existIdx] = data;
        } else {
            allDetected.push(data);
        }
        // Keep max 100
        if (allDetected.length > 100) allDetected = allDetected.slice(-80);
        renderDetectedTable();
    }

    function renderDetectedTable() {
        // Filter
        let filtered = allDetected;
        if (activeFilter === "liquid")    filtered = allDetected.filter(d => d.has_liquidity);
        if (activeFilter === "no-liquid") filtered = allDetected.filter(d => !d.has_liquidity);
        if (activeFilter === "safe")      filtered = allDetected.filter(d => d.risk === "safe");
        if (activeFilter === "danger")    filtered = allDetected.filter(d => d.risk === "danger" || d.is_honeypot);

        // Update counters
        const counterEl = document.getElementById("detected-counter");
        if (counterEl) counterEl.textContent = allDetected.length;
        const tabLiq   = document.getElementById("tab-liquid-count");
        const tabNoLiq = document.getElementById("tab-noliq-count");
        const tabSafe  = document.getElementById("tab-safe-count");
        const tabDang  = document.getElementById("tab-danger-count");
        if (tabLiq)   tabLiq.textContent   = allDetected.filter(d => d.has_liquidity).length;
        if (tabNoLiq) tabNoLiq.textContent = allDetected.filter(d => !d.has_liquidity).length;
        if (tabSafe)  tabSafe.textContent  = allDetected.filter(d => d.risk === "safe").length;
        if (tabDang)  tabDang.textContent  = allDetected.filter(d => d.risk === "danger" || d.is_honeypot).length;

        // Update stat
        statPairs.textContent = allDetected.length;
        if (detectedBadge) detectedBadge.textContent = allDetected.length;

        // Render table (newest first)
        detectedTbody.innerHTML = "";
        if (!filtered.length) {
            detectedTbody.innerHTML = `<tr class="empty-row"><td colspan="7">${activeFilter === "all" ? "Waiting for detections…" : "No tokens match this filter"}</td></tr>`;
            return;
        }

        [...filtered].reverse().forEach(data => {
            const tr = document.createElement("tr");

            // Row styling based on liquidity
            const hasLiq = data.has_liquidity;
            tr.className = hasLiq ? "row-liquid" : "row-no-liquid";

            // Risk badge
            const riskBadge = data.risk === "safe"    ? '<span class="risk-badge safe">SAFE</span>'
                            : data.risk === "warning" ? '<span class="risk-badge warning">WARN</span>'
                            : data.risk === "danger"  ? '<span class="risk-badge danger">DANGER</span>'
                            :                          '<span class="risk-badge unknown">???</span>';

            const honeypotTag = data.is_honeypot ? ' <span class="honeypot-tag">🍯 HP</span>' : "";
            const shortToken = data.token ? data.token.slice(0, 6) + "…" + data.token.slice(-4) : "?";
            const liqClass = hasLiq ? "liq-yes" : "liq-no";
            const liqText = data.liquidity_usd > 0 ? `$${Number(data.liquidity_usd).toLocaleString()}` : "$0";
            const liqIcon = hasLiq ? "💧" : "🚫";

            const explorer = chainSelect.value === "56" ? "bscscan.com" : "etherscan.io";
            const dexChain = chainSelect.value === "56" ? "bsc" : "ethereum";
            const cmcChain = chainSelect.value === "56" ? "bsc" : "ethereum";

            // ── Security flags pills ──
            const flags = [];
            if (data.is_honeypot)              flags.push({icon:"🍯", label:"Honeypot",   cls:"flag-danger"});
            if (data.is_mintable)              flags.push({icon:"🖨️", label:"Mintable",    cls:"flag-danger"});
            if (data.has_blacklist)             flags.push({icon:"🚫", label:"Blacklist",   cls:"flag-danger"});
            if (data.can_pause_trading)         flags.push({icon:"⏸️", label:"Pausable",    cls:"flag-danger"});
            if (data.cannot_sell_all)           flags.push({icon:"🔐", label:"Can't sell",  cls:"flag-danger"});
            if (data.owner_can_change_balance)  flags.push({icon:"⚠️", label:"Ctrl balance",cls:"flag-danger"});
            if (data.can_self_destruct)         flags.push({icon:"💣", label:"Self-destruct",cls:"flag-danger"});
            if (data.is_proxy)                  flags.push({icon:"🔄", label:"Proxy",       cls:"flag-warn"});
            if (data.has_hidden_owner)          flags.push({icon:"👤", label:"Hidden owner", cls:"flag-warn"});
            if (data.has_owner)                 flags.push({icon:"👑", label:"Has owner",   cls:"flag-warn"});
            if (data.is_open_source === false)  flags.push({icon:"🔒", label:"Not verified",cls:"flag-warn"});
            // Safe flags — only trust API-confirmed data
            if (!data.is_honeypot && data.risk === "safe" && data.goplus_ok) flags.push({icon:"✅", label:"Safe", cls:"flag-safe"});
            if (data.is_open_source === true && data.goplus_ok) {
                flags.push({icon:"📝", label:"Verified", cls:"flag-safe"});
            }
            // Cross-platform flags
            if (data.listed_coingecko)              flags.push({icon:"🦎", label:"CoinGecko", cls:"flag-safe"});
            if (data.coingecko_ok && !data.listed_coingecko) flags.push({icon:"🦎", label:"No CoinGecko", cls:"flag-warn"});
            if (data.tokensniffer_is_scam)           flags.push({icon:"🚩", label:"Scam (TS)", cls:"flag-danger"});
            if (data.tokensniffer_ok && data.tokensniffer_score >= 0 && data.tokensniffer_score < 30)
                flags.push({icon:"🚩", label:`TS ${data.tokensniffer_score}/100`, cls:"flag-danger"});
            if (data.tokensniffer_ok && data.tokensniffer_score >= 30 && data.tokensniffer_score < 60)
                flags.push({icon:"⚠️", label:`TS ${data.tokensniffer_score}/100`, cls:"flag-warn"});
            if (data.tokensniffer_ok && data.tokensniffer_score >= 60)
                flags.push({icon:"✅", label:`TS ${data.tokensniffer_score}/100`, cls:"flag-safe"});
            if (data.has_website)                   flags.push({icon:"🌐", label:"Website", cls:"flag-safe"});
            if (data.has_social_links)              flags.push({icon:"📱", label:"Socials", cls:"flag-safe"});
            // No API data indicator
            if (!data.goplus_ok && !data.honeypot_ok && !data.dexscreener_ok && !data.coingecko_ok) {
                flags.push({icon:"❓", label:"Sin datos API", cls:"flag-warn"});
            }

            const flagsHtml = flags.map(f =>
                `<span class="sec-flag ${f.cls}" title="${f.label}">${f.icon} ${f.label}</span>`
            ).join("");

            // Risk reasons tooltip
            const reasons = (data.risk_reasons || []);
            const reasonsHtml = reasons.length
                ? `<div class="dt-reasons">${reasons.map(r => `<div class="dt-reason-line">${r}</div>`).join("")}</div>`
                : "";

            tr.innerHTML = `
                <td>
                    <div class="dt-token-name">${data.symbol || "?"} ${honeypotTag}</div>
                    <div class="dt-token-fullname">${data.name || "Unknown"}</div>
                    <div class="dt-token-addr">${shortToken}</div>
                </td>
                <td>
                    ${riskBadge}
                    <button class="btn-expand-flags" title="Ver análisis">▸</button>
                </td>
                <td>${data.buy_tax != null ? data.buy_tax + "%" : "?"}</td>
                <td>${data.sell_tax != null ? data.sell_tax + "%" : "?"}</td>
                <td class="${liqClass}">${liqIcon} ${liqText}</td>
                <td class="td-block">#${data.block ? Number(data.block).toLocaleString() : "?"}</td>
                <td>
                    <a class="btn-sniper btn-mini btn-view-contract"
                       href="https://${explorer}/address/${data.token}"
                       target="_blank" rel="noopener" title="View on explorer">🔗</a>
                    <a class="btn-sniper btn-mini btn-view-dex"
                       href="https://dexscreener.com/${dexChain}/${data.token}"
                       target="_blank" rel="noopener" title="DEX Screener">📊</a>
                    <a class="btn-sniper btn-mini btn-view-cmc"
                       href="https://coinmarketcap.com/dexscan/${cmcChain}/${data.token}"
                       target="_blank" rel="noopener" title="CoinMarketCap">💹</a>
                    <button class="btn-sniper btn-mini btn-view-chart"
                        data-token="${data.token || ""}"
                        data-symbol="${data.symbol || "?"}"
                        data-pair="${data.pair || ""}"
                        title="Ver gráfico de precio">📈</button>
                    <button class="btn-sniper btn-mini btn-view-log"
                        data-idx="${allDetected.indexOf(data)}"
                        title="Ver log completo">📋</button>
                    ${hasLiq ? `<button class="btn-sniper btn-mini btn-buy-snipe"
                        data-token="${data.token || ""}"
                        data-symbol="${data.symbol || "?"}"
                        data-pair="${data.pair || ""}"
                        title="Snipe this token">🎯</button>` : ""}
                </td>
            `;

            // Expandable detail row (hidden by default)
            const detailTr = document.createElement("tr");
            detailTr.className = "detail-row hidden";

            // Build cross-platform info block
            const apiParts = [];
            if (data.goplus_ok)       apiParts.push('<span class="api-badge api-ok">🛡️ GoPlus</span>');
            else                      apiParts.push('<span class="api-badge api-fail">🛡️ GoPlus ✗</span>');
            if (data.honeypot_ok)     apiParts.push('<span class="api-badge api-ok">🍯 Honeypot.is</span>');
            else                      apiParts.push('<span class="api-badge api-fail">🍯 Honeypot.is ✗</span>');
            if (data.dexscreener_ok)  apiParts.push('<span class="api-badge api-ok">📊 DexScreener</span>');
            else                      apiParts.push('<span class="api-badge api-fail">📊 DexScreener ✗</span>');
            if (data.coingecko_ok)    apiParts.push(`<span class="api-badge ${data.listed_coingecko ? 'api-ok' : 'api-warn'}">🦎 CoinGecko ${data.listed_coingecko ? '✓' : 'No listado'}</span>`);
            else                      apiParts.push('<span class="api-badge api-fail">🦎 CoinGecko ✗</span>');
            if (data.tokensniffer_ok) apiParts.push(`<span class="api-badge ${data.tokensniffer_score >= 50 ? 'api-ok' : 'api-warn'}">🐽 TS: ${data.tokensniffer_score}/100</span>`);
            else                      apiParts.push('<span class="api-badge api-fail">🐽 TokenSniffer ✗</span>');

            // DexScreener extra data
            let dexExtra = "";
            if (data.dexscreener_ok) {
                dexExtra = `<div class="dt-extra">📊 Vol 24h: $${Number(data.dexscreener_volume_24h || 0).toLocaleString()} | Pares: ${data.dexscreener_pairs || 0} | Buys: ${data.dexscreener_buys_24h || 0} / Sells: ${data.dexscreener_sells_24h || 0} | Edad: ${data.dexscreener_age_hours ? data.dexscreener_age_hours + "h" : "?"}</div>`;
            }

            detailTr.innerHTML = `<td colspan="7">
                <div class="detail-panel">
                    <div class="sec-flags-row">${flagsHtml || '<span class="sec-flag flag-safe">✅ Sin flags detectados</span>'}</div>
                    ${reasonsHtml}
                    <div class="dt-apis-row">${apiParts.join("")}</div>
                    ${dexExtra}
                    ${data.holder_count ? `<div class="dt-extra">👥 Holders: ${data.holder_count} | Supply: ${data.total_supply ? Number(data.total_supply).toLocaleString() : "?"}</div>` : ""}
                </div>
            </td>`;

            detectedTbody.appendChild(tr);
            detectedTbody.appendChild(detailTr);

            // Toggle detail on expand button click
            tr.querySelector(".btn-expand-flags").addEventListener("click", (e) => {
                e.stopPropagation();
                const btn = e.currentTarget;
                detailTr.classList.toggle("hidden");
                btn.textContent = detailTr.classList.contains("hidden") ? "▸" : "▾";
            });
        });
    }

    // Legacy function for state sync
    function addDetectedToken(data) {
        addAllDetected(data);
    }

    /* ═══════════════════════════════════════════════════════════════
     *  Token Chart — DexScreener embed
     * ═══════════════════════════════════════════════════════════════ */

    /** Format a price with dynamic decimals */
    function fmtPrice(v) {
        if (v == null || !isFinite(v)) return "—";
        if (v === 0) return "0";
        if (v >= 1)     return v.toFixed(2);
        if (v >= 0.001) return v.toFixed(6);
        return v.toFixed(12);
    }

    /* ── DexScreener embed ─────────────────────────────────────── */

    function showDexScreenerEmbed(chain, pairAddr) {
        // Remove old iframe
        const old = chartModalContainer.querySelector(".dex-embed-iframe");
        if (old) old.remove();
        // Remove fallback message if any
        const oldMsg = chartModalContainer.querySelector(".chart-no-data");
        if (oldMsg) oldMsg.remove();

        const iframe = document.createElement("iframe");
        iframe.className = "dex-embed-iframe";
        iframe.src = `https://dexscreener.com/${chain}/${pairAddr}?embed=1&theme=dark&trades=0&info=0`;
        iframe.style.cssText = "width:100%;height:100%;border:none;position:absolute;inset:0;border-radius:0;";
        iframe.setAttribute("loading", "lazy");
        iframe.setAttribute("allowfullscreen", "true");

        chartModalContainer.appendChild(iframe);
        chartLoading.style.display = "none";
    }

    /** Show GeckoTerminal embed as fallback */
    function showGeckoTerminalEmbed(chain, tokenAddr) {
        const old = chartModalContainer.querySelector(".dex-embed-iframe");
        if (old) old.remove();
        const oldMsg = chartModalContainer.querySelector(".chart-no-data");
        if (oldMsg) oldMsg.remove();

        const geckoChain = chain === "bsc" ? "bsc" : "eth";
        const iframe = document.createElement("iframe");
        iframe.className = "dex-embed-iframe";
        iframe.src = `https://www.geckoterminal.com/${geckoChain}/pools/${tokenAddr}?embed=1&info=0&swaps=0&grayscale=0&light_chart=0`;
        iframe.style.cssText = "width:100%;height:100%;border:none;position:absolute;inset:0;border-radius:0;";
        iframe.setAttribute("loading", "lazy");
        iframe.setAttribute("allowfullscreen", "true");

        chartModalContainer.appendChild(iframe);
        chartLoading.style.display = "none";
    }

    /** Show a 'no data yet' message with retry */
    function showChartNoData(token, symbol) {
        const old = chartModalContainer.querySelector(".chart-no-data");
        if (old) old.remove();
        chartLoading.style.display = "none";

        const div = document.createElement("div");
        div.className = "chart-no-data";
        div.innerHTML = `
            <div class="chart-no-data-icon">⏳</div>
            <div class="chart-no-data-title">Token demasiado nuevo</div>
            <div class="chart-no-data-desc">
                <strong>${symbol || "Token"}</strong> acaba de ser creado en la blockchain.
                <br>DexScreener aún no ha indexado este par.
                <br>Espera unos minutos e intenta de nuevo.
            </div>
            <button class="btn-sniper btn-retry-chart" data-token="${token}" data-symbol="${symbol}">
                🔄 Reintentar
            </button>
            <a class="btn-sniper btn-mini" href="https://dexscreener.com/bsc/${token}" target="_blank" rel="noopener"
               style="margin-top:8px;font-size:.8em;opacity:.7;">Abrir en DexScreener ↗</a>
        `;
        chartModalContainer.appendChild(div);

        // Retry handler
        div.querySelector(".btn-retry-chart").addEventListener("click", () => {
            div.remove();
            chartLoading.textContent = "Reintentando…";
            chartLoading.style.display = "flex";
            if (chartCurrentToken) {
                fetchTokenChartData(chartCurrentToken.token, chartCurrentToken.pair, chartCurrentToken.symbol);
            }
        });
    }

    /* ── Main chart flow ──────────────────────────────────────────── */

    async function openTokenChart(token, symbol, pair) {
        chartCurrentToken = { token, symbol, pair };
        chartModal.style.display = "flex";
        chartModalSymbol.textContent = `📈 ${symbol || "Token"}`;
        chartModalMeta.innerHTML = `<span class="cmi-addr">${token ? token.slice(0, 8) + "…" + token.slice(-6) : ""}</span>`;

        // Reset info bar
        cmiPrice.textContent = "—";
        cmiChange.textContent = "";
        cmiChange.className = "cmi-change";
        cmiVol.textContent = "";
        cmiLiq.textContent = "";
        chartLoading.textContent = "Cargando gráfico…";
        chartLoading.style.display = "flex";

        // Remove any old iframe
        const oldIframe = chartModalContainer.querySelector(".dex-embed-iframe");
        if (oldIframe) oldIframe.remove();

        // Fetch and render
        await fetchTokenChartData(token, pair, symbol);

        // Auto-refresh info bar every 30s
        clearInterval(chartRefreshTimer);
        chartRefreshTimer = setInterval(() => {
            if (chartModal.style.display !== "none" && chartCurrentToken) {
                fetchDexScreenerInfoSilent(chartCurrentToken.token);
            }
        }, 30000);
    }

    /** Fetch DexScreener info for the info bar (silent refresh, no embed change) */
    async function fetchDexScreenerInfoSilent(token) {
        try {
            const chain = chainSelect.value === "56" ? "bsc" : "ethereum";
            const res = await fetch(`https://api.dexscreener.com/latest/dex/tokens/${token}`);
            if (!res.ok) return;
            const json = await res.json();
            if (!json.pairs || !json.pairs.length) return;

            const chainPairs = json.pairs.filter(p => p.chainId === chain);
            const best = chainPairs.length > 0
                ? chainPairs.sort((a, b) => (b.liquidity?.usd || 0) - (a.liquidity?.usd || 0))[0]
                : json.pairs[0];

            updateInfoBar(best);
        } catch (_) {}
    }

    /**
     * Load chart for a detected token.
     * 1. Check DexScreener API for indexed pairs
     * 2. If found → embed DexScreener with the real pair address
     * 3. If not → try GeckoTerminal, else show "too new" message
     */
    async function fetchTokenChartData(token, pair, symbol) {
        const chain = chainSelect.value === "56" ? "bsc" : "ethereum";

        try {
            // Ask DexScreener API if this token has any indexed pairs
            const res = await fetch(`https://api.dexscreener.com/latest/dex/tokens/${token}`);
            if (res.ok) {
                const json = await res.json();
                const pairs = (json.pairs || []).filter(p => p.chainId === chain);

                if (pairs.length > 0) {
                    // Use highest-liquidity pair
                    const best = pairs.sort((a, b) =>
                        (b.liquidity?.usd || 0) - (a.liquidity?.usd || 0)
                    )[0];

                    // Update info bar
                    updateInfoBar(best);

                    // Show DexScreener embed with the REAL pair address
                    showDexScreenerEmbed(chain, best.pairAddress);
                    return;
                }
            }
        } catch (_) {}

        // DexScreener doesn't have it — try GeckoTerminal
        try {
            const geckoChain = chain === "bsc" ? "bsc" : "eth";
            const geckoRes = await fetch(
                `https://api.geckoterminal.com/api/v2/networks/${geckoChain}/tokens/${token}/pools?page=1`,
                { headers: { "Accept": "application/json" } }
            );
            if (geckoRes.ok) {
                const geckoJson = await geckoRes.json();
                const pools = geckoJson.data || [];
                if (pools.length > 0) {
                    const poolAddr = pools[0].attributes?.address || pools[0].id?.split("_").pop();
                    if (poolAddr) {
                        showGeckoTerminalEmbed(geckoChain, poolAddr);
                        // Try to fill info bar from pool data
                        try {
                            const attrs = pools[0].attributes || {};
                            const price = parseFloat(attrs.base_token_price_usd) || 0;
                            if (price > 0) cmiPrice.textContent = `$${fmtPrice(price)}`;
                            const vol = parseFloat(attrs.volume_usd?.h24) || 0;
                            if (vol > 0) cmiVol.textContent = `Vol: $${Number(vol).toLocaleString()}`;
                            const liq = parseFloat(attrs.reserve_in_usd) || 0;
                            if (liq > 0) cmiLiq.textContent = `Liq: $${Number(liq).toLocaleString()}`;
                        } catch (_) {}
                        return;
                    }
                }
            }
        } catch (_) {}

        // Neither has data — show "too new" fallback
        showChartNoData(token, symbol);
    }

    /** Update the info bar from a DexScreener pair object */
    function updateInfoBar(best) {
        const price = parseFloat(best.priceUsd) || 0;
        cmiPrice.textContent = `$${fmtPrice(price)}`;

        const change = best.priceChange?.h24 ?? null;
        if (change !== null) {
            const pct = parseFloat(change);
            cmiChange.textContent = `${pct >= 0 ? "+" : ""}${pct.toFixed(2)}% (24h)`;
            cmiChange.className = `cmi-change ${pct >= 0 ? "cmi-up" : "cmi-down"}`;
        }

        const vol = best.volume?.h24 || 0;
        cmiVol.textContent = vol > 0 ? `Vol: $${Number(vol).toLocaleString()}` : "";

        const liq = best.liquidity?.usd || 0;
        cmiLiq.textContent = liq > 0 ? `Liq: $${Number(liq).toLocaleString()}` : "";
    }

    /** Close the chart modal */
    function closeTokenChart() {
        chartModal.style.display = "none";
        clearInterval(chartRefreshTimer);
        chartRefreshTimer = null;
        chartCurrentToken = null;

        // Remove DexScreener/GeckoTerminal iframe
        const iframe = chartModalContainer.querySelector(".dex-embed-iframe");
        if (iframe) iframe.remove();
        // Remove 'no data' fallback
        const noData = chartModalContainer.querySelector(".chart-no-data");
        if (noData) noData.remove();
    }

    // ─── Snipes table ───────────────────────────────────
    function _formatCountdown(s) {
        if (!s || !s.timestamp || !s.max_hold_hours || s.max_hold_hours <= 0) return "—";
        const now = Date.now() / 1000;
        const elapsed = now - s.timestamp;
        const limitSec = s.max_hold_hours * 3600;
        const remaining = Math.max(0, limitSec - elapsed);
        if (remaining <= 0) return '<span style="color:#f6465d">⏰ 0:00:00</span>';
        const h = Math.floor(remaining / 3600);
        const m = Math.floor((remaining % 3600) / 60);
        const sec = Math.floor(remaining % 60);
        const txt = `${h}:${String(m).padStart(2,"0")}:${String(sec).padStart(2,"0")}`;
        // Color: red if < 1h, yellow if < 3h, normal otherwise
        if (remaining < 3600) return `<span style="color:#f6465d">⏰ ${txt}</span>`;
        if (remaining < 10800) return `<span style="color:#f0b90b">⏳ ${txt}</span>`;
        return `⏳ ${txt}`;
    }

    function _formatHeld(s) {
        if (!s || !s.timestamp) return "";
        const elapsed = (s.held_seconds != null) ? s.held_seconds : Math.floor(Date.now() / 1000 - s.timestamp);
        const h = Math.floor(elapsed / 3600);
        const m = Math.floor((elapsed % 3600) / 60);
        return `${h}h${String(m).padStart(2,"0")}m`;
    }

    function renderSnipes() {
        if (!activeSnipes.length) {
            snipesTbody.innerHTML = '<tr class="empty-row"><td colspan="8">No active positions</td></tr>';
            return;
        }

        snipesTbody.innerHTML = "";
        activeSnipes.forEach(s => {
            const pnlClass = s.pnl_percent >= 0 ? "pnl-positive" : "pnl-negative";
            const pnlText = (s.pnl_percent >= 0 ? "+" : "") + (s.pnl_percent || 0).toFixed(2) + "%";
            const statusBadge = s.status === "active" ? '<span class="status-badge active">ACTIVE</span>'
                              : s.status === "sold"   ? '<span class="status-badge sold">SOLD</span>'
                              :                         '<span class="status-badge stopped">STOPPED</span>';

            const sellBtn = s.status === "active"
                ? `<button class="btn-sell-snipe" data-token="${s.token_address}" data-symbol="${s.symbol || '?'}" title="Sell now">💰 Sell</button>`
                : "—";

            const countdown = _formatCountdown(s);
            const held = _formatHeld(s);
            const timeCell = (s.max_hold_hours > 0) ? `${countdown}<br><small style="color:#848e9c">${held}</small>` : (held ? `<small style="color:#848e9c">${held}</small>` : "—");

            const tr = document.createElement("tr");
            tr.dataset.token = s.token_address;
            tr.innerHTML = `
                <td><strong>${s.symbol || "?"}</strong></td>
                <td>$${(s.buy_price_usd || 0).toFixed(6)}</td>
                <td>$${(s.current_price_usd || 0).toFixed(6)}</td>
                <td class="${pnlClass}">${pnlText}</td>
                <td>+${s.take_profit}% / -${s.stop_loss}%</td>
                <td>${timeCell}</td>
                <td>${statusBadge}</td>
                <td>${sellBtn}</td>
            `;
            snipesTbody.appendChild(tr);
        });
    }

    function updateSnipeRow(data) {
        // Find existing row
        const existing = activeSnipes.find(s => s.token_address === data.token);
        if (existing) {
            existing.current_price_usd = data.current_price || existing.current_price_usd;
            existing.pnl_percent = data.pnl_percent || existing.pnl_percent;
            existing.status = data.status || existing.status;
            if (data.timestamp) existing.timestamp = data.timestamp;
            if (data.max_hold_hours != null) existing.max_hold_hours = data.max_hold_hours;
            if (data.held_seconds != null) existing.held_seconds = data.held_seconds;
        }
        renderSnipes();
    }

    /* ═══════════════════════════════════════════════════════════════
     *  Event listeners
     * ═══════════════════════════════════════════════════════════════ */

    // ─── Detected modal open / close ───────────────────
    btnOpenDetected.addEventListener("click", () => {
        detectedModal.style.display = "flex";
        renderDetectedTable();
    });
    btnCloseDetected.addEventListener("click", () => {
        detectedModal.style.display = "none";
    });
    detectedModal.addEventListener("click", (e) => {
        if (e.target === detectedModal) detectedModal.style.display = "none";
    });

    // ─── Chart modal open / close ──────────────────────
    btnCloseChartModal.addEventListener("click", closeTokenChart);
    chartModal.addEventListener("click", (e) => {
        if (e.target === chartModal) closeTokenChart();
    });



    // ─── Legend toggle ─────────────────────────────────
    const btnLegend = document.getElementById("btn-toggle-legend");
    const legendPanel = document.getElementById("legend-panel");
    if (btnLegend && legendPanel) {
        btnLegend.addEventListener("click", () => {
            const open = legendPanel.style.display !== "none";
            legendPanel.style.display = open ? "none" : "block";
            btnLegend.classList.toggle("active", !open);
        });
    }

    // ─── Filter tab clicks ─────────────────────────────
    document.querySelectorAll(".dft-tab").forEach(tab => {
        tab.addEventListener("click", () => {
            document.querySelectorAll(".dft-tab").forEach(t => t.classList.remove("active"));
            tab.classList.add("active");
            activeFilter = tab.dataset.filter || "all";
            renderDetectedTable();
        });
    });

    btnStart.addEventListener("click", () => {
        const chainId = parseInt(chainSelect.value);
        sendWS({ action: "start", chain_id: chainId });
        addFeed(`Starting bot on chain ${chainId}…`, "system");
    });

    btnStop.addEventListener("click", () => {
        sendWS({ action: "stop" });
        addFeed("Stopping bot…", "system");
    });

    btnSaveSettings.addEventListener("click", () => {
        const settings = {
            min_liquidity_usd: parseFloat(setMinLiq.value)   || 5000,
            max_buy_tax:       parseFloat(setMaxBuyTax.value) || 10,
            max_sell_tax:      parseFloat(setMaxSellTax.value) || 15,
            buy_amount_native: parseFloat(setBuyAmount.value) || 0.05,
            take_profit:       parseFloat(setTP.value)        || 40,
            stop_loss:         parseFloat(setSL.value)        || 15,
            slippage:          parseFloat(setSlippage.value)  || 12,
            max_hold_hours:    parseFloat(setMaxHold.value)    || 0,
            only_safe:         setOnlySafe.checked,
            auto_buy:          setAutoBuy.checked,
            max_concurrent:    parseInt(setMaxConcurrent.value) || 5,
            block_range:       parseInt(setBlockRange.value)   || 5,
            poll_interval:     parseFloat(setPollInterval.value) || 1.5,
            // v6 modules
            enable_mev_protection: document.getElementById("set-mev-protection")?.checked || false,
            enable_copy_trading:   document.getElementById("set-copy-trading")?.checked || false,
            enable_multi_dex:      document.getElementById("set-multi-dex")?.checked ?? true,
            enable_ai_optimizer:   document.getElementById("set-ai-optimizer")?.checked ?? true,
            enable_backtesting:    document.getElementById("set-backtesting")?.checked || false,
            // v7 modules
            enable_rl_learner:       document.getElementById("set-enable-rl-learner")?.checked ?? true,
            enable_orderflow:        document.getElementById("set-enable-orderflow")?.checked ?? true,
            enable_market_simulator: document.getElementById("set-enable-market-simulator")?.checked ?? true,
            enable_auto_strategy:    document.getElementById("set-enable-auto-strategy")?.checked ?? true,
            enable_launch_scanner:   document.getElementById("set-enable-launch-scanner")?.checked ?? true,
            enable_mempool_v7:       document.getElementById("set-enable-mempool-v7")?.checked ?? true,
            enable_whale_graph:      document.getElementById("set-enable-whale-graph")?.checked ?? true,
        };
        sendWS({ action: "update_settings", settings });
        addFeed("Saving settings…", "system");
    });

    // Delegate click on "Chart", "Log" and "Snipe" buttons in detected table
    detectedTbody.addEventListener("click", (e) => {
        // Chart button
        const chartBtn = e.target.closest(".btn-view-chart");
        if (chartBtn) {
            const token  = chartBtn.dataset.token;
            const symbol = chartBtn.dataset.symbol;
            const pair   = chartBtn.dataset.pair;
            if (token) openTokenChart(token, symbol, pair);
            return;
        }

        // Log button — open full log modal
        const logBtn = e.target.closest(".btn-view-log");
        if (logBtn) {
            const idx = parseInt(logBtn.dataset.idx);
            const data = allDetected[idx];
            if (data) {
                _logAutoFollow = false; // user picked a specific token
                _openLogForData(data);
            }
            return;
        }

        // Snipe button — execute a manual buy swap
        const btn = e.target.closest(".btn-buy-snipe");
        if (!btn) return;

        const token  = btn.dataset.token;
        const symbol = btn.dataset.symbol;

        if (!token) return;

        const buyAmt  = parseFloat(setBuyAmount.value) || 0.05;
        const slip    = parseFloat(setSlippage.value)  || 12;

        if (!confirm(`Buy ${symbol} with ${buyAmt} native (slippage ${slip}%)?`)) return;

        btn.disabled = true;
        btn.textContent = "⏳";
        executeSnipeBuy(token, symbol, buyAmt, slip, "manual").then(result => {
            btn.disabled = false;
            btn.textContent = result.success ? "✅" : "🎯";
        });
    });

    // Close log modal
    function _closeLogModal() {
        logModal.style.display = "none";
        _logModalAddr = null;
    }
    if (btnCloseLogModal) btnCloseLogModal.addEventListener("click", _closeLogModal);
    if (logModal) logModal.addEventListener("click", (e) => { if (e.target === logModal) _closeLogModal(); });

    // Auto-follow toggle
    const btnLogAutoFollow = document.getElementById("btn-log-auto-follow");
    if (btnLogAutoFollow) {
        btnLogAutoFollow.addEventListener("click", () => {
            _logAutoFollow = !_logAutoFollow;
            btnLogAutoFollow.classList.toggle("active", _logAutoFollow);
            btnLogAutoFollow.textContent = _logAutoFollow ? "⏩ Auto" : "⏸ Auto";
            if (_logAutoFollow && allDetected.length) {
                // Jump to latest token immediately
                _openLogForData(allDetected[allDetected.length - 1]);
            }
        });
    }

    // Re-analyze button
    const btnLogReanalyze = document.getElementById("btn-log-reanalyze");
    if (btnLogReanalyze) {
        btnLogReanalyze.addEventListener("click", () => {
            if (!_logModalAddr) return;
            // Disable button and show loading state
            btnLogReanalyze.disabled = true;
            btnLogReanalyze.textContent = "⏳ Analizando…";
            btnLogReanalyze.classList.add("loading");
            // Pause auto-follow so modal stays on this token
            _logAutoFollow = false;
            if (btnLogAutoFollow) {
                btnLogAutoFollow.classList.remove("active");
                btnLogAutoFollow.textContent = "⏸ Auto";
            }
            sendWS({ action: "re_analyze_token", token: _logModalAddr });
            // Re-enable after timeout (analysis takes ~10-30s)
            setTimeout(() => {
                btnLogReanalyze.disabled = false;
                btnLogReanalyze.textContent = "🔄 Re-Analizar";
                btnLogReanalyze.classList.remove("loading");
            }, 60000);
        });
    }

    // Delegate click on "Sell" buttons in active snipes table
    snipesTbody.addEventListener("click", (e) => {
        const btn = e.target.closest(".btn-sell-snipe");
        if (!btn) return;

        const token  = btn.dataset.token;
        const symbol = btn.dataset.symbol;
        if (!token) return;

        const slip = parseFloat(setSlippage.value) || 12;
        if (!confirm(`Sell ALL ${symbol} tokens (slippage ${slip}%)?`)) return;

        btn.disabled = true;
        btn.textContent = "⏳";
        executeSnipeSell(token, symbol, slip, "manual").then(result => {
            btn.disabled = false;
            btn.textContent = result.success ? "✅" : "💰 Sell";
        });
    });

    /* ═══════════════════════════════════════════════════════════════
     *  v4: Performance Dashboard
     * ═══════════════════════════════════════════════════════════════ */

    /** Build lightweight mini-bar chart (pure DOM, no Canvas) */
    function renderMiniBars(containerId, values, mode) {
        const el = document.getElementById(containerId);
        if (!el || !values || !values.length) return;
        const max = Math.max(...values.map(v => Math.abs(v)), 1);
        // Limit to last 12 bars for performance
        const slice = values.slice(-12);
        el.innerHTML = "";
        const frag = document.createDocumentFragment();
        for (const v of slice) {
            const bar = document.createElement("div");
            bar.className = "bar" + (mode === "pnl" ? (v >= 0 ? " pos" : " neg") : "");
            bar.style.height = Math.max(2, (Math.abs(v) / max) * 100) + "%";
            frag.appendChild(bar);
        }
        el.appendChild(frag);
    }

    /** Update the full dashboard from backend metrics_dashboard payload */
    function updateDashboard(dash) {
        if (!dash || _dashCollapsed) return;  // skip when collapsed

        // KPI cards
        const ts = dash.trade_stats || {};
        const ds = dash.detection_stats || {};

        if (kpiTrades)     kpiTrades.textContent     = ts.total_trades || 0;
        if (kpiWinRate)    kpiWinRate.textContent     = (ts.win_rate || 0).toFixed(1) + "%";
        if (kpiPnl)        kpiPnl.textContent         = "$" + (ts.total_pnl || 0).toFixed(2);
        if (kpiDetections) kpiDetections.textContent   = ds.total_detections || 0;
        if (kpiFilterRate) kpiFilterRate.textContent   = (ds.filter_rate || 0).toFixed(1) + "%";
        if (kpiAvgSpeed)   kpiAvgSpeed.textContent     = (ds.avg_detection_ms || 0).toFixed(0) + "ms";

        // Effectiveness chips
        const eff = dash.effectiveness || {};
        if (effSafe)      effSafe.textContent      = (eff.safe_token_pct || 0).toFixed(1) + "%";
        if (effAvgGain)   effAvgGain.textContent   = "+" + (eff.avg_gain_pct || 0).toFixed(1) + "%";
        if (effAvgLoss)   effAvgLoss.textContent   = (eff.avg_loss_pct || 0).toFixed(1) + "%";
        if (effTxFail)    effTxFail.textContent     = (eff.tx_failure_pct || 0).toFixed(1) + "%";
        if (effAvgHold)   effAvgHold.textContent    = (eff.avg_hold_minutes || 0).toFixed(0) + "m";
        if (effBestTrade) effBestTrade.textContent   = "+" + (eff.best_trade_pct || 0).toFixed(1) + "%";

        // Resource bar from dashboard
        if (dash.resource_stats) updateResourceBar(dash.resource_stats);

        // Lightweight mini-bars (max 12 bars, pure DOM)
        const hourly = dash.hourly_series || {};
        if (hourly.pnl) {
            renderMiniBars("bars-pnl", hourly.pnl, "pnl");
            const last = hourly.pnl[hourly.pnl.length - 1];
            const valEl = document.getElementById("bars-pnl-val");
            if (valEl && last !== undefined) valEl.textContent = "$" + last.toFixed(2);
        }
        if (hourly.detections) {
            renderMiniBars("bars-det", hourly.detections, "det");
            const total = hourly.detections.reduce((a, b) => a + b, 0);
            const valEl = document.getElementById("bars-det-val");
            if (valEl) valEl.textContent = total + " total";
        }
    }

    /** Update the resource monitor bar */
    function updateResourceBar(rs) {
        if (!rs) return;
        if (resMemory) resMemory.textContent = (rs.memory_rss_mb || 0).toFixed(0) + " MB";
        if (resCpu)    resCpu.textContent    = (rs.cpu_percent || 0).toFixed(0) + "%";
        if (resTokens) resTokens.textContent = rs.tokens_processed || 0;
        if (resWs)     resWs.textContent     = rs.ws_connections || 0;
        if (resRpc)    resRpc.textContent    = rs.rpc_calls || 0;
    }

    /* ═══════════════════════════════════════════════════════════════
     *  v4: User Profile Presets
     * ═══════════════════════════════════════════════════════════════ */

    const profilePresets = {
        novato: {
            min_liquidity_usd: 10000, max_buy_tax: 5, max_sell_tax: 10,
            buy_amount_native: 0.02, take_profit: 30, stop_loss: 10,
            slippage: 12, only_safe: true, auto_buy: false,
            max_hold_hours: 2, max_concurrent: 3, block_range: 5, poll_interval: 2,
        },
        intermedio: {
            min_liquidity_usd: 5000, max_buy_tax: 10, max_sell_tax: 15,
            buy_amount_native: 0.05, take_profit: 40, stop_loss: 15,
            slippage: 12, only_safe: true, auto_buy: false,
            max_hold_hours: 6, max_concurrent: 5, block_range: 5, poll_interval: 1.5,
        },
        avanzado: {
            min_liquidity_usd: 2000, max_buy_tax: 15, max_sell_tax: 20,
            buy_amount_native: 0.1, take_profit: 60, stop_loss: 20,
            slippage: 15, only_safe: false, auto_buy: true,
            max_hold_hours: 12, max_concurrent: 10, block_range: 10, poll_interval: 1,
        },
    };

    function applyProfile(name) {
        const preset = profilePresets[name];
        if (!preset) return;
        if (setMinLiq)        setMinLiq.value        = preset.min_liquidity_usd;
        if (setMaxBuyTax)     setMaxBuyTax.value     = preset.max_buy_tax;
        if (setMaxSellTax)    setMaxSellTax.value    = preset.max_sell_tax;
        if (setBuyAmount)     setBuyAmount.value     = preset.buy_amount_native;
        if (setTP)            setTP.value            = preset.take_profit;
        if (setSL)            setSL.value            = preset.stop_loss;
        if (setSlippage)      setSlippage.value      = preset.slippage;
        if (setMaxHold)       setMaxHold.value       = preset.max_hold_hours;
        if (setOnlySafe)      setOnlySafe.checked    = preset.only_safe;
        if (setAutoBuy)       setAutoBuy.checked     = preset.auto_buy;
        if (setMaxConcurrent) setMaxConcurrent.value = preset.max_concurrent;
        if (setBlockRange)    setBlockRange.value    = preset.block_range;
        if (setPollInterval)  setPollInterval.value  = preset.poll_interval;

        // Update active button
        document.querySelectorAll(".profile-btn").forEach(b => b.classList.remove("active"));
        const btn = document.querySelector(`.profile-btn[data-profile="${name}"]`);
        if (btn) btn.classList.add("active");

        // Update description
        const descEl = document.getElementById("profile-desc");
        const descs = {
            novato: "Configuración conservadora — solo tokens seguros, bajo riesgo.",
            intermedio: "Balance entre seguridad y oportunidad — configuración recomendada.",
            avanzado: "Configuración agresiva — más riesgo, auto-buy activo.",
        };
        if (descEl) descEl.textContent = descs[name] || "";

        addFeed(`👤 Perfil "${name}" aplicado — guarda los settings para confirmar.`, "system");
    }

    document.querySelectorAll(".profile-btn").forEach(btn => {
        btn.addEventListener("click", () => applyProfile(btn.dataset.profile));
    });

    /* ═══════════════════════════════════════════════════════════════
     *  v4: Tutorial Overlay
     * ═══════════════════════════════════════════════════════════════ */

    function initTutorial() {
        if (!tutorialOverlay) return;
        // Check localStorage
        if (localStorage.getItem("sniper_tutorial_done") === "1") {
            tutorialOverlay.style.display = "none";
            return;
        }

        let currentStep = 0;
        const steps = tutorialOverlay.querySelectorAll(".tutorial-step");
        const dots  = tutorialOverlay.querySelectorAll(".tutorial-dot");
        const prevBtn = tutorialOverlay.querySelector(".tutorial-prev");
        const nextBtn = tutorialOverlay.querySelector(".tutorial-next");
        const closeBtn = tutorialOverlay.querySelector(".tutorial-close");
        const noShow  = tutorialOverlay.querySelector("#tutorial-no-show");

        function showStep(idx) {
            currentStep = Math.max(0, Math.min(idx, steps.length - 1));
            steps.forEach((s, i) => s.classList.toggle("active", i === currentStep));
            dots.forEach((d, i) => d.classList.toggle("active", i === currentStep));
            if (prevBtn) prevBtn.style.visibility = currentStep === 0 ? "hidden" : "visible";
            if (nextBtn) nextBtn.textContent = currentStep === steps.length - 1 ? "✅ Empezar" : "Siguiente →";
        }

        if (prevBtn) prevBtn.addEventListener("click", () => showStep(currentStep - 1));
        if (nextBtn) nextBtn.addEventListener("click", () => {
            if (currentStep >= steps.length - 1) {
                closeTutorial();
            } else {
                showStep(currentStep + 1);
            }
        });

        function closeTutorial() {
            tutorialOverlay.style.display = "none";
            if (noShow && noShow.checked) {
                localStorage.setItem("sniper_tutorial_done", "1");
            }
        }

        if (closeBtn) closeBtn.addEventListener("click", closeTutorial);
        tutorialOverlay.addEventListener("click", (e) => {
            if (e.target === tutorialOverlay) closeTutorial();
        });

        showStep(0);
    }

    /* ═══════════════════════════════════════════════════════════════
     *  v4: Alert Configuration
     * ═══════════════════════════════════════════════════════════════ */

    function initAlertConfig() {
        const telegramToggle = $("alert-telegram");
        const discordToggle  = $("alert-discord");
        const emailToggle    = $("alert-email");
        const discordWebhookRow = $("discord-webhook-row");
        const discordWebhookUrl = $("discord-webhook-url");

        // Show/hide webhook URL input when Discord toggle changes
        function updateDiscordUI() {
            if (discordWebhookRow) {
                discordWebhookRow.style.display = discordToggle && discordToggle.checked ? "flex" : "none";
            }
        }

        function syncAlertConfig() {
            updateDiscordUI();
            const config = {
                telegram_enabled: telegramToggle ? telegramToggle.checked : false,
                discord_enabled:  discordToggle  ? discordToggle.checked  : false,
                email_enabled:    emailToggle    ? emailToggle.checked    : false,
            };
            // Include webhook URL if provided from the UI
            if (discordWebhookUrl && discordWebhookUrl.value.trim()) {
                config.discord_webhook_url = discordWebhookUrl.value.trim();
            }
            sendWS({ action: "update_alert_config", config });
        }

        if (telegramToggle) telegramToggle.addEventListener("change", syncAlertConfig);
        if (discordToggle)  discordToggle.addEventListener("change", syncAlertConfig);
        if (emailToggle)    emailToggle.addEventListener("change", syncAlertConfig);
        if (discordWebhookUrl) discordWebhookUrl.addEventListener("change", syncAlertConfig);

        // Test Webhook button
        const btnTestWebhook = $("btn-test-webhook");
        if (btnTestWebhook) {
            btnTestWebhook.addEventListener("click", () => {
                if (!ws || ws.readyState !== WebSocket.OPEN) {
                    addFeed("❌ WebSocket no conectado. Inicia el bot primero.", "error");
                    return;
                }
                btnTestWebhook.disabled = true;
                btnTestWebhook.textContent = "⏳ Enviando…";
                sendWS({ action: "test_webhook" });
                setTimeout(() => {
                    btnTestWebhook.disabled = false;
                    btnTestWebhook.textContent = "🔔 Test Webhook";
                }, 5000);
            });
        }

        updateDiscordUI();
    }

    // Dashboard refresh button
    if (btnRefreshDash) {
        btnRefreshDash.addEventListener("click", () => {
            sendWS({ action: "get_dashboard" });
            addFeed("📊 Refreshing dashboard…", "system");
        });
    }

    // Dashboard toggle (collapse/expand) — skip updates when collapsed
    if (btnToggleDash && dashboardBody) {
        btnToggleDash.addEventListener("click", () => {
            _dashCollapsed = !_dashCollapsed;
            dashboardBody.classList.toggle("collapsed", _dashCollapsed);
            btnToggleDash.textContent = _dashCollapsed ? "▶" : "▼";
        });
    }

    // Auto-refresh dashboard every 60s (was 30s — reduce overhead)
    function startDashboardAutoRefresh() {
        if (_dashboardTimer) clearInterval(_dashboardTimer);
        _dashboardTimer = setInterval(() => {
            if (ws && ws.readyState === WebSocket.OPEN && botRunning && !_dashCollapsed) {
                sendWS({ action: "get_dashboard" });
            }
        }, 60000);
    }

    // v8: API Health refresh button + auto-poll
    const btnRefreshApiHealth = $("btn-refresh-api-health");
    let _apiHealthTimer = null;

    function fetchApiHealth() {
        if (ws && ws.readyState === WebSocket.OPEN && botRunning) {
            sendWS({ action: "get_api_health" });
        }
    }

    if (btnRefreshApiHealth) {
        btnRefreshApiHealth.addEventListener("click", fetchApiHealth);
    }

    function startApiHealthAutoRefresh() {
        if (_apiHealthTimer) clearInterval(_apiHealthTimer);
        _apiHealthTimer = setInterval(fetchApiHealth, 15000); // every 15s
    }

    /* ═══════════════════════════════════════════════════════════════
     *  Init
     * ═══════════════════════════════════════════════════════════════ */
    (async () => {
        const ok = await reconnectWallet();
        if (ok && walletAddr) {
            walletAddr.textContent = shortAddr(wallet.address);
            walletBadge.classList.add("connected");
        }
    })();

    // v4 init (no Chart.js needed)
    initTutorial();
    initAlertConfig();
    startDashboardAutoRefresh();
    startApiHealthAutoRefresh();

    connectWS();
})();

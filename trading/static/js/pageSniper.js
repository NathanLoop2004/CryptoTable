/**
 * pageSniper.js — Sniper Bot frontend controller.
 *
 * Connects to ws://.../ws/sniper/ and controls the backend SniperBot.
 * Displays real-time pipeline, detected tokens, active positions, live feed.
 */
(function () {
    "use strict";

    /* ═══════════════════════════════════════════════════════════════
     *  Wallet (from walletConnect.js)
     * ═══════════════════════════════════════════════════════════════ */
    const wallet = window.__walletState || { address: "", chain_id: 56 };

    async function reconnectWallet() {
        if (typeof window.reconnectWallet === "function") {
            return window.reconnectWallet();
        }
        const prov = window.trustwallet?.ethereum || window.ethereum;
        if (!prov) return false;
        try {
            const accs = await prov.request({ method: "eth_accounts" });
            if (accs.length) {
                wallet.address = accs[0];
                const cid = await prov.request({ method: "eth_chainId" });
                wallet.chain_id = parseInt(cid, 16);
                window.__walletState = wallet;
                return true;
            }
        } catch (_) {}
        return false;
    }

    function shortAddr(a) {
        return a ? a.slice(0, 6) + "…" + a.slice(-4) : "Not connected";
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

    /* ═══════════════════════════════════════════════════════════════
     *  State
     * ═══════════════════════════════════════════════════════════════ */
    let ws = null;
    let botRunning = false;
    let allDetected = [];        // ALL tokens detected (liquid + no-liquid)
    let detectedTokens = [];     // [{token, symbol, name, risk, buy_tax, sell_tax, liquidity_usd, pair}]
    let activeSnipes = [];
    let activeFilter = "all";    // current tab filter

    // Pipeline counters
    let pipeStats = {
        mempool: 0,
        detected: 0,
        analyzed: 0,
        liquidity: 0,
        sniped: 0,
    };

    /* ═══════════════════════════════════════════════════════════════
     *  Feed
     * ═══════════════════════════════════════════════════════════════ */
    function addFeed(text, type = "info") {
        const line = document.createElement("div");
        line.className = `feed-line feed-${type}`;
        const ts = new Date().toLocaleTimeString();
        line.textContent = `[${ts}] ${text}`;
        feedDiv.appendChild(line);

        // Keep max 200 lines
        while (feedDiv.children.length > 200) {
            feedDiv.removeChild(feedDiv.firstChild);
        }
        feedDiv.scrollTop = feedDiv.scrollHeight;
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
                    const liqTag = data.has_liquidity ? "💧" : "🚫";
                    const liqStr = data.liquidity_usd > 0 ? `$${Number(data.liquidity_usd).toLocaleString()}` : "$0";
                    const rIcon = data.risk === "safe" ? "🟢" : data.risk === "warning" ? "🟡" : data.risk === "danger" ? "🔴" : "⚪";
                    addFeed(`${liqTag} ${data.symbol} (${data.name}) — ${rIcon} ${data.risk.toUpperCase()} | Liq: ${liqStr} | Buy: ${data.buy_tax}% | Sell: ${data.sell_tax}%`, data.has_liquidity ? "good" : "warn");
                    if (data.is_honeypot) addFeed(`   🍯 HONEYPOT: ${data.symbol}`, "error");
                    if (data.risk_reasons && data.risk_reasons.length) addFeed(`   ⚠️ ${data.risk_reasons.join(", ")}`, "warn");

                    // Add to full detected list & table
                    addAllDetected(data);
                    pipeStats.analyzed++;
                    pipeAnalyzeCnt.textContent = pipeStats.analyzed;
                    flashPipe("pipe-analyze");
                }
                break;

            case "contract_analysis":
                // Detailed analysis for liquid tokens (already in table from token_detected)
                {
                    const riskIcon2 = data.risk === "safe" ? "🟢" : data.risk === "warning" ? "🟡" : "🔴";
                    addFeed(`🛡️ Full analysis: ${data.symbol} — ${riskIcon2} ${data.risk.toUpperCase()} | Buy: ${data.buy_tax}% | Sell: ${data.sell_tax}% | Liq: $${data.liquidity_usd}`, data.risk === "danger" ? "error" : "good");
                }
                break;

            case "snipe_opportunity":
                pipeStats.sniped++;
                pipeSnipeCnt.textContent = pipeStats.sniped;
                flashPipe("pipe-snipe");
                addFeed(`🎯 OPPORTUNITY: ${data.symbol} — $${data.liquidity_usd} liquidity — Risk: ${data.risk}`, "opportunity");
                if (data.auto_buy) {
                    addFeed(`🤖 Auto-buy is ON — executing…`, "system");
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
                break;

            case "stop_loss_alert":
                addFeed(`🛑 STOP LOSS HIT! ${data.symbol} at ${data.pnl_percent}% (limit: ${data.target}%)`, "error");
                flashPipe("pipe-profit");
                break;

            case "settings_updated":
                addFeed("⚙️ Settings updated.", "system");
                break;

            case "snipe_registered":
                addFeed(`✅ Position registered: ${data.symbol}`, "good");
                break;

            case "error":
                addFeed(`❌ ${data.message}`, "error");
                break;
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
            if (setOnlySafe)   setOnlySafe.checked = state.settings.only_safe !== false;
            if (setAutoBuy)    setAutoBuy.checked  = !!state.settings.auto_buy;
            if (setMaxConcurrent) setMaxConcurrent.value = state.settings.max_concurrent || 5;
            if (setBlockRange)    setBlockRange.value    = state.settings.block_range || 5;
            if (setPollInterval)  setPollInterval.value  = state.settings.poll_interval || 1.5;
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

    // ─── ALL Detected tokens (liquid + no-liquid) ──────
    function addAllDetected(data) {
        allDetected.push(data);
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

    // ─── Snipes table ───────────────────────────────────
    function renderSnipes() {
        if (!activeSnipes.length) {
            snipesTbody.innerHTML = '<tr class="empty-row"><td colspan="6">No active positions</td></tr>';
            return;
        }

        snipesTbody.innerHTML = "";
        activeSnipes.forEach(s => {
            const pnlClass = s.pnl_percent >= 0 ? "pnl-positive" : "pnl-negative";
            const pnlText = (s.pnl_percent >= 0 ? "+" : "") + (s.pnl_percent || 0).toFixed(2) + "%";
            const statusBadge = s.status === "active" ? '<span class="status-badge active">ACTIVE</span>'
                              : s.status === "sold"   ? '<span class="status-badge sold">SOLD</span>'
                              :                         '<span class="status-badge stopped">STOPPED</span>';

            const tr = document.createElement("tr");
            tr.dataset.token = s.token_address;
            tr.innerHTML = `
                <td><strong>${s.symbol || "?"}</strong></td>
                <td>$${(s.buy_price_usd || 0).toFixed(6)}</td>
                <td>$${(s.current_price_usd || 0).toFixed(6)}</td>
                <td class="${pnlClass}">${pnlText}</td>
                <td>+${s.take_profit}% / -${s.stop_loss}%</td>
                <td>${statusBadge}</td>
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
            only_safe:         setOnlySafe.checked,
            auto_buy:          setAutoBuy.checked,
            max_concurrent:    parseInt(setMaxConcurrent.value) || 5,
            block_range:       parseInt(setBlockRange.value)   || 5,
            poll_interval:     parseFloat(setPollInterval.value) || 1.5,
        };
        sendWS({ action: "update_settings", settings });
        addFeed("Saving settings…", "system");
    });

    // Delegate click on "Snipe" buttons in detected table
    detectedTbody.addEventListener("click", (e) => {
        const btn = e.target.closest(".btn-buy-snipe");
        if (!btn) return;

        const token  = btn.dataset.token;
        const symbol = btn.dataset.symbol;

        if (!token) return;

        // Open token on DEX Screener for quick action
        const chain = chainSelect.value === "56" ? "bsc" : "ethereum";
        window.open(`https://dexscreener.com/${chain}/${token}`, "_blank");
    });

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

    connectWS();
})();

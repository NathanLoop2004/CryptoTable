"""
trading app — Plataforma de Trading + Sniper Bot BSC/ETH

Módulos principales:
───────────────────
  Services/sniperService.py   (~2300 líneas)
      Motor del Sniper Bot. Escanea bloques, detecta PairCreated,
      analiza con 5 APIs de seguridad (GoPlus, Honeypot.is, DexScreener,
      CoinGecko, TokenSniffer), ejecuta auto-buy/sell con TP/SL.
      Integra 6 módulos profesionales v2 para análisis profundo.
      - Verificación estricta de contrato: open source obligatorio,
        bloquea proxy, hidden owner, fake renounce, airdrop scam,
        fake ERC-20, holder concentration (top≥30%, creator≥20%)
      - Enrichment dual: Fast ~3s (APIs fallidas) / Slow ~15s (DexScreener full)
      - Buy Gating: LP Lock ≥80% y ≥24h, anti-pump, anti-dump, pump score,
        swap simulation, bytecode analysis
      - P&L update cada ~3s con TP(40%)/SL(20%)/MaxHoldHours auto
      - Sync WS listener para precio en tiempo real
      - RPC rotation (5 fallbacks BSC, 4 ETH)

  Services/pumpAnalyzer.py     (~430 líneas)
      Pump scoring engine 0-100. 7 componentes: liquidity, holder,
      activity, whale, momentum, age, social. Grades: HIGH/MEDIUM/LOW/AVOID.

  Services/swapSimulator.py    (~570 líneas)
      Swap simulation via eth_call (buy+sell). BytecodeAnalyzer: detecta
      SELFDESTRUCT, DELEGATECALL, minimal proxy, bytecode anómalo.

  Services/mempoolService.py   (~470 líneas)
      Mempool listener. Detecta txs pendientes (addLiquidity, createPair,
      removeLiquidity, approve) 10-30s antes de confirmación en bloque.

  Services/rugDetector.py      (~495 líneas)
      Post-buy rug detection. Monitorea LP drain, tax increase, dev selling,
      blacklist, pause. EMERGENCY auto-sell triggers.

  Services/preLaunchDetector.py (~445 líneas)
      Pre-launch token detection. Contract creation scan, ERC-20 bytecode
      matching, router approval detection. Launch probability 0-100.

  Services/smartMoneyTracker.py (~410 líneas)
      Smart money wallet tracking. Auto-discovers profitable wallets,
      emits signals when tracked whales buy new tokens.

  WebSocket/sniperConsumer.py  (~161 líneas)
      Django Channels consumer. Bridge Frontend ↔ SniperBot.
      Acciones: start, stop, update_settings, register_snipe, mark_sold.

  static/js/pageSniper.js     (~1660 líneas)
      Frontend del Sniper. WebSocket handler, feed cards con data-token
      para updates en vivo, executeSnipeBuy/Sell via ethers.js TxModule,
      auto-sell en TP/SL/TimeLimit/Rug alerts. v2: pump score, swap sim,
      bytecode, mempool events, rug alerts, pre-launch, smart money.

  templates/sniper.html        (~384 líneas)
      UI del Sniper Bot. Tabla de tokens, feed log, modal de detalle,
      panel de settings (stop_loss=20%, take_profit=40%), controles.

Stack:
  Django 6.0 + Channels/Daphne (ASGI) + web3.py + aiohttp + ethers.js v6
  Trust Wallet auth (PyJWT + pycryptodome)
  BSC (PancakeSwap V2) / ETH (Uniswap V2)
"""
from django.apps import AppConfig


class TradingConfig(AppConfig):
    name = 'trading'

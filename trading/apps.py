"""
trading app — Plataforma de Trading + Sniper Bot BSC/ETH

Módulos principales:
───────────────────
  Services/sniperService.py   (~1980 líneas)
      Motor del Sniper Bot. Escanea bloques, detecta PairCreated,
      analiza con 5 APIs de seguridad (GoPlus, Honeypot.is, DexScreener,
      CoinGecko, TokenSniffer), ejecuta auto-buy/sell con TP/SL.
      - Verificación estricta de contrato: open source obligatorio,
        bloquea proxy, hidden owner, fake renounce, airdrop scam,
        fake ERC-20, holder concentration (top≥30%, creator≥20%)
      - Enrichment dual: Fast ~3s (APIs fallidas) / Slow ~15s (DexScreener full)
      - Buy Gating: LP Lock ≥80% y ≥24h, anti-pump (5m≥+30%, 1h≥+50%),
        anti-dump (24h≤-50%, 6h≤-40%, 1h≤-25%), honeypot, tax limits
      - P&L update cada ~3s con TP(40%)/SL(20%)/MaxHoldHours auto
      - Sync WS listener para precio en tiempo real
      - RPC rotation (5 fallbacks BSC, 4 ETH)

  WebSocket/sniperConsumer.py  (~161 líneas)
      Django Channels consumer. Bridge Frontend ↔ SniperBot.
      Acciones: start, stop, update_settings, register_snipe, mark_sold.

  static/js/pageSniper.js     (~1627 líneas)
      Frontend del Sniper. WebSocket handler, feed cards con data-token
      para updates en vivo, executeSnipeBuy/Sell via ethers.js TxModule,
      auto-sell en TP/SL/TimeLimit alerts.

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

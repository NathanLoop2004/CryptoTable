# 📊 TradingWeb — Módulo de Trade

## Índice

1. [Descripción General](#descripción-general)
2. [Arquitectura](#arquitectura)
3. [Stack Tecnológico](#stack-tecnológico)
4. [Flujo de Datos](#flujo-de-datos)
5. [Páginas y Rutas](#páginas-y-rutas)
6. [Dashboard (Trade)](#dashboard-trade)
7. [Wallet](#wallet)
8. [Transactions](#transactions)
9. [WebSocket — Datos en Tiempo Real](#websocket--datos-en-tiempo-real)
10. [DEX Swap (Buy / Sell)](#dex-swap-buy--sell)
11. [Comprar Crypto (Fiat On-Ramp)](#comprar-crypto-fiat-on-ramp)
12. [Estructura de Archivos](#estructura-de-archivos)
13. [Diagrama de Flujo](#diagrama-de-flujo)

---

## Descripción General

El módulo **Trade** es el core de TradingWeb. Proporciona una interfaz estilo Binance para:

- **Visualizar** datos de mercado en tiempo real (precios, velas, order book, trades).
- **Ejecutar** operaciones de compra/venta directamente desde la wallet conectada via DEX (PancakeSwap en BSC, Uniswap en Ethereum).
- **Gestionar** la wallet: ver balances, enviar tokens nativos y ERC-20, hacer swaps.
- **Comprar crypto** con tarjeta/banco a través de proveedores fiat (Binance, Changelly, ChangeNOW).

---

## Arquitectura

```
┌─────────────────────────────────────────────────────────────┐
│                      FRONTEND (Browser)                      │
│                                                              │
│  ┌──────────┐  ┌──────────────┐  ┌──────────────┐           │
│  │ login.html│  │dashboard.html│  │  wallet.html │           │
│  │           │  │              │  │              │           │
│  │walletCon- │  │ dashboard.js │  │ pageWallet.js│           │
│  │ nect.js   │  │transactions.js│  │              │           │
│  └─────┬─────┘  └──────┬───────┘  └──────┬───────┘           │
│        │               │                 │                   │
│        │    ethers.js (BrowserProvider)   │                   │
│        │    TradingView Lightweight Charts│                   │
│        └───────────┬───┴─────────────────┘                   │
│                    │ WebSocket                                │
├────────────────────┼─────────────────────────────────────────┤
│                    │       BACKEND (Django + Channels)        │
│                    ▼                                         │
│  ┌──────────────────────────────────┐                        │
│  │  Daphne ASGI Server (:8000)      │                        │
│  │                                  │                        │
│  │  ┌────────────────────────────┐  │                        │
│  │  │ BinanceConsumer (ws/binance)│  │ ← Binance WebSocket   │
│  │  │ WalletConsumer  (ws/wallet) │  │ ← Wallet events       │
│  │  └────────────────────────────┘  │                        │
│  │                                  │                        │
│  │  ┌────────────────────────────┐  │                        │
│  │  │ ViewController             │  │ ← HTTP page renders    │
│  │  └────────────────────────────┘  │                        │
│  └──────────────────────────────────┘                        │
└─────────────────────────────────────────────────────────────┘
```

---

## Stack Tecnológico

| Componente       | Tecnología                                          |
|------------------|-----------------------------------------------------|
| **Backend**      | Django 6.0.2, Python 3.14                           |
| **ASGI Server**  | Daphne 4.2.1                                        |
| **WebSocket**    | Django Channels 4.3.2                               |
| **Blockchain**   | ethers.js 6.13.4 (frontend), web3.py (backend)     |
| **Charts**       | TradingView Lightweight Charts 4.1.3                |
| **Datos**        | Binance WebSocket API (streams en tiempo real)      |
| **DEX**          | PancakeSwap V2 (BSC), Uniswap V2 (Ethereum)        |
| **Wallet**       | Trust Wallet / MetaMask (via `window.ethereum`)     |

---

## Flujo de Datos

```
Usuario conecta wallet (Trust Wallet / MetaMask)
        │
        ▼
Login page ──► Detecta provider (window.ethereum)
        │       Solicita eth_requestAccounts
        │       Obtiene address + chainId
        │       Guarda en localStorage
        ▼
Dashboard ──► Carga ethers.js BrowserProvider
        │     Reconecta wallet desde localStorage
        │     Abre WebSocket ws/binance/
        │     Abre WebSocket ws/wallet/
        │
        ├── ws/binance/ ──► Binance streams (kline, trade, depth, ticker)
        │                    Actualiza chart, order book, market trades
        │
        ├── ws/wallet/ ──► Balance updates, block number, gas price
        │                   Token holdings, wallet events
        │
        └── DEX Swap ──► ethers.js → PancakeSwap/Uniswap Router
                         swapExactETHForTokens (Buy)
                         swapExactTokensForETH (Sell)
```

---

## Páginas y Rutas

### URLs (HTTP)

| Ruta             | Vista                          | Descripción                 |
|------------------|--------------------------------|-----------------------------|
| `/`              | `ViewController.login`         | Login / Home — Conectar wallet |
| `/dashboard/`    | `ViewController.dashboard`     | Dashboard de trading principal |
| `/transactions/` | `ViewController.transactions`  | Página de transacciones      |
| `/wallet/`       | `ViewController.wallet_page`   | Información completa de wallet |
| `/sniper/`       | `ViewController.sniper_page`   | Sniper Bot (ver doc separada)|

### WebSocket Endpoints

| Ruta            | Consumer           | Descripción                     |
|-----------------|--------------------|---------------------------------|
| `ws/binance/`   | `BinanceConsumer`  | Stream de datos de Binance      |
| `ws/wallet/`    | `WalletConsumer`   | Eventos de wallet en tiempo real|

---

## Dashboard (Trade)

**Archivo:** `trading/templates/dashboard.html` + `trading/static/js/dashboard.js`

### Layout (Grid estilo Binance)

```
┌──────────────────────────────────────────────────────────────┐
│  TOPBAR: Logo │ Trade │ Transactions │ Wallet │ Sniper │ WS  │
├──────────────────────────────────────────────────────────────┤
│  PAIR BAR: Search │ Coin Icon │ Price │ 24h Stats │ Stream   │
├────────────────┬──────────────┬───────────────────────────────┤
│                │              │                               │
│   CHART        │  ORDER BOOK  │   BUY / SELL                 │
│   (TradingView │  Asks        │   Side tabs (Buy/Sell)       │
│    Candles/    │  Spread      │   Price input                │
│    Line)       │  Bids        │   Amount input               │
│                │              │   % buttons (25/50/75/100)   │
│   Timeframes:  │              │   Slippage selector          │
│   1m 5m 15m    │              │   Est. Output                │
│   1H 4H 1D 1W │              │   Native balance             │
│                │              │   Cost (swap + gas)          │
│                │              │   [Confirm Button]           │
│◄──resizer──►   │◄──resizer──► │                              │
├────────────────┴──────────────┴──────────────────────────────┤
│◄────────────────── resizer vertical ────────────────────────►│
├─────────────────────────────┬────────────────────────────────┤
│  BOTTOM LEFT                │  BOTTOM RIGHT                  │
│  Tabs: Market Trades │      │  Tabs: Transactions │          │
│        Live Feed │          │        Wallet Info             │
│        Watchlist            │                                │
│  Market Trades: Price,      │  TX: Send Native │             │
│    Amount, Time             │      Send Token │              │
│                             │      Swap                      │
│  Live Feed: Raw WS data    │                                │
│  Watchlist: Price cards     │  Wallet: Account, Network,    │
│                             │    Balances, Tokens            │
└─────────────────────────────┴────────────────────────────────┘
```

### Funcionalidades Principales

#### 1. Búsqueda de Pares
- Input con autocompletado que busca en todos los pares de Binance.
- Muestra icono de la moneda (via CoinGecko/CryptoCompare CDN).
- Al seleccionar un par, se subscribe automáticamente al stream de klines.

#### 2. Chart (Gráfico de Velas)
- **TradingView Lightweight Charts** renderiza candlestick o line chart.
- **Timeframes**: 1m, 5m, 15m, 1H, 4H, 1D, 1W.
- Toolbar muestra OHLC (Open-High-Low-Close) del crosshair.
- Carga datos históricos via Binance REST API (`/api/v3/klines`).
- Actualización en tiempo real via `kline_Xm` WebSocket stream.

#### 3. Order Book
- Visualización de asks (rojo, arriba) y bids (verde, abajo).
- Spread en el centro.
- Barras de profundidad con gradiente proporcional al volumen.
- Datos via `depth@100ms` Binance stream.

#### 4. Buy / Sell (DEX Swap)
- **Tabs**: Buy (verde) / Sell (rojo).
- **Precio**: Sincronizable con el precio de mercado (botón ↻ Market).
- **Amount**: Input manual + botones de porcentaje (25%, 50%, 75%, 100%).
- **Slippage**: Selector 0.5% / 1% / 3% + input custom.
- **Estimación**: Llama `getAmountsOut()` del router para estimar output.
- **Costo**: Muestra swap cost + gas fee estimado en tokens nativos.
- **Balance**: Muestra saldo disponible en BNB/ETH.
- **Modal de confirmación**: Side, Pair, Amount In, Est. Output, USD Value, Slippage, DEX.
- **Ejecución**: 
  - **Buy**: `swapExactETHForTokensSupportingFeeOnTransferTokens()` via ethers.js.
  - **Sell**: Approve token → `swapExactTokensForETHSupportingFeeOnTransferTokens()`.

#### 5. Streams Activos
- Badges que muestran qué streams están activos (trade, kline, depth, ticker, etc.).
- Botones Sub/Unsub para controlar subscripciones individualmente.

#### 6. Market Trades
- Lista en tiempo real de trades ejecutados en el par seleccionado.
- Precio, Amount, Timestamp con color verde/rojo según dirección.

#### 7. Paneles Redimensionables
- Resizers verticales y horizontales (`resizer-col`, `resizer-row`).
- Drag para ajustar tamaños de chart, order book, y paneles.

---

## Wallet

**Archivo:** `trading/templates/wallet.html` + `trading/static/js/pageWallet.js`

### Secciones

#### 1. Account Info
| Campo       | Descripción                              |
|-------------|------------------------------------------|
| Address     | Dirección completa (click to copy)       |
| Provider    | "Trust Wallet" / "MetaMask" / etc.       |
| Status      | "Connected" / "Disconnected"             |
| Connected   | Timestamp de conexión                    |

#### 2. Network Info
| Campo       | Descripción                              |
|-------------|------------------------------------------|
| Network     | "BNB Smart Chain" / "Ethereum" / etc.    |
| Chain ID    | 56, 1, 97, etc.                          |
| Block #     | Último bloque conocido                   |
| Gas Price   | Precio actual del gas en Gwei            |

#### 3. Balances
- **Balance Nativo**: Monto en BNB/ETH con valor en USD.
- Precio obtenido de Binance REST API (`/api/v3/ticker/price`).

#### 4. 💳 Buy Crypto (Fiat On-Ramp)
- Selector de monto en USD ($10, $20, $50, $100 + input custom).
- Estimación de tokens a recibir basada en precio actual.
- **Proveedores** (links públicos, sin API key):

| Proveedor  | URL Pattern                                      | Métodos       |
|------------|--------------------------------------------------|---------------|
| Binance    | `binance.com/en/crypto/buy/{slug}`               | Card/P2P/Bank |
| Changelly  | `changelly.com/buy/{slug}`                       | Visa/MC       |
| ChangeNOW  | `changenow.io/exchange?to={slug}`                | Crypto swap   |

- **NETWORK_MAP** define slugs por chainId:

```javascript
const NETWORK_MAP = {
    "0x38": { name: "BNB Smart Chain", symbol: "BNB", ..., binSlug: "BNB", chgSlug: "bnb", cnTo: "bnb" },
    "0x1":  { name: "Ethereum",        symbol: "ETH", ..., binSlug: "ETH", chgSlug: "eth", cnTo: "eth" },
    // ... más redes
};
```

#### 5. 📥 Deposit
- **QR Code** generado dinámicamente con la dirección de la wallet.
- **Dirección copiable** con botón "Copy".
- **Advertencia** sobre enviar tokens en la red correcta (BEP-20, ERC-20, etc.).

#### 6. Token Holdings
- Tabla de tokens ERC-20 con balance y dirección de contrato.
- Refrescable manualmente con botón ↻.

#### 7. Wallet Events
- Feed en tiempo real de eventos (cambios de cuenta, cambios de red, etc.).

---

## Transactions

**Archivo:** `trading/templates/transactions.html` + `trading/static/js/transactions.js`

### Tabs de Transacciones

#### 1. Send Native (BNB/ETH)
- **Recipient**: Input de dirección `0x…`.
- **Amount**: Input numérico con símbolo de la moneda nativa.
- Ejecuta `signer.sendTransaction({ to, value })` via ethers.js.

#### 2. Send Token (ERC-20)
- **Token Contract**: Input de dirección del contrato ERC-20.
- Auto-detecta nombre, símbolo y decimales del token.
- **Recipient** + **Amount**.
- Ejecuta `contract.transfer(to, amount)` via ethers.js.

#### 3. Swap
- **From**: Dirección del token de origen (vacío = nativo).
- **To Token**: Dirección del token de destino.
- **Amount** + **Slippage**.
- Estimación de output via `getAmountsOut()`.
- Ejecuta swap via router del DEX.

---

## WebSocket — Datos en Tiempo Real

### ws/binance/ (BinanceConsumer)

Streams disponibles de Binance:

| Stream          | Descripción                          | Datos               |
|-----------------|--------------------------------------|----------------------|
| `trade`         | Trades individuales                  | price, qty, time     |
| `kline_1m`      | Velas de 1 minuto                    | OHLCV                |
| `kline_5m`      | Velas de 5 minutos                   | OHLCV                |
| `kline_1h`      | Velas de 1 hora                      | OHLCV                |
| `ticker`        | Ticker 24h completo                  | price, change, vol   |
| `miniTicker`    | Ticker 24h resumido                  | price, change        |
| `depth@100ms`   | Order book incremental               | bids, asks           |

**Protocolo de mensajes**:
```json
// Subscribe
{ "action": "subscribe", "symbol": "btcusdt", "stream": "kline_1m" }

// Unsubscribe
{ "action": "unsubscribe", "symbol": "btcusdt", "stream": "kline_1m" }
```

### ws/wallet/ (WalletConsumer)

Emite actualizaciones de:
- Balance nativo
- Número de bloque
- Precio del gas
- Token holdings
- Eventos de cambio de cuenta/red

---

## DEX Swap (Buy / Sell)

### Contratos DEX

| Chain | DEX          | Router Address                              |
|-------|-------------|---------------------------------------------|
| BSC   | PancakeSwap | `0x10ED43C718714eb63d5aA57B78B54704E256024E` |
| ETH   | Uniswap V2  | `0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D` |

| Chain | WETH/WBNB                                     |
|-------|------------------------------------------------|
| BSC   | `0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c`  |
| ETH   | `0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2`  |

### Flujo de Compra (Buy)

```
1. Usuario ingresa precio + cantidad
2. Frontend calcula total (price × amount)
3. Llama getAmountsOut([WBNB, TokenAddress]) para estimar output
4. Muestra modal de confirmación
5. Usuario confirma
6. Ejecuta swapExactETHForTokensSupportingFeeOnTransferTokens({
       amountOutMin: output × (1 - slippage),
       path: [WBNB, tokenAddress],
       to: wallet.address,
       deadline: block.timestamp + 300,
       value: amountInWei
   })
7. Espera confirmación de TX
8. Muestra hash de transacción + link al explorer
```

### Flujo de Venta (Sell)

```
1. Primero: approve() el token al Router
2. Luego: swapExactTokensForETHSupportingFeeOnTransferTokens({
       amountIn: tokenAmount,
       amountOutMin: output × (1 - slippage),
       path: [tokenAddress, WBNB],
       to: wallet.address,
       deadline: block.timestamp + 300
   })
```

### Manejo de Errores

| Error                | Causa                              | Solución                          |
|----------------------|------------------------------------|-----------------------------------|
| `CALL_EXCEPTION`     | Función del contrato falla         | Verificar ABI y parámetros        |
| `NETWORK_ERROR`      | Provider desincronizado tras switch| `rebuildProvider()` recrea el BrowserProvider |
| Chain incorrecta     | Wallet en otra red                 | Auto-switch con `wallet_switchEthereumChain`  |
| Insufficient balance | Saldo insuficiente                 | Validar antes de enviar TX        |

---

## Comprar Crypto (Fiat On-Ramp)

### Proveedores Integrados

Los tres proveedores usan **URLs públicas** (no requieren API key):

1. **Binance** — `https://www.binance.com/en/crypto/buy/{binSlug}`
   - Acepta tarjeta de crédito/débito, P2P, transferencia bancaria.
   
2. **Changelly** — `https://changelly.com/buy/{chgSlug}`
   - Acepta Visa / Mastercard.

3. **ChangeNOW** — `https://changenow.io/exchange?to={cnTo}`
   - Permite intercambiar desde cualquier otra criptomoneda.

### Actualización de Links

Los links se actualizan dinámicamente en `updateProviderLinks()` después de que la wallet se reconecta, incorporando:
- La dirección de la wallet como destino.
- El monto en USD seleccionado.
- El slug correcto según la red detectada.

---

## Estructura de Archivos

```
TradingWeb/
├── TradingWeb/
│   ├── settings.py          # Configuración Django + Channels
│   ├── urls.py              # Rutas HTTP (/, /dashboard/, /wallet/, etc.)
│   └── asgi.py              # ASGI config (Daphne + Channels)
│
├── trading/
│   ├── Controllers/
│   │   └── viewController.py    # Renders de páginas HTML
│   │
│   ├── WebSocket/
│   │   ├── routing.py           # Rutas WebSocket (ws/binance/, ws/wallet/)
│   │   ├── binanceConsumer.py   # Consumer: datos de Binance en tiempo real
│   │   └── walletConsumer.py    # Consumer: eventos de wallet
│   │
│   ├── templates/
│   │   ├── base.html            # Template base (CSS + estructura)
│   │   ├── login.html           # Página de login / conexión de wallet
│   │   ├── dashboard.html       # Dashboard principal de trading
│   │   ├── transactions.html    # Página de transacciones
│   │   └── wallet.html          # Página de wallet completa
│   │
│   ├── static/
│   │   ├── css/
│   │   │   └── main.css         # Estilos globales (Binance dark theme)
│   │   └── js/
│   │       ├── walletConnect.js # Conexión Trust Wallet / MetaMask
│   │       ├── dashboard.js     # Lógica del dashboard (chart, orderbook, trade)
│   │       ├── transactions.js  # Lógica de transacciones (send, swap)
│   │       └── pageWallet.js    # Lógica de la página wallet
│   │
│   ├── Services/                # Servicios backend
│   ├── Models/                  # Modelos de datos
│   └── Routes/
│       └── urls.py              # Rutas API REST
│
└── docs/                        # ← Documentación (este archivo)
```

---

## Diagrama de Flujo

```
                    ┌────────────┐
                    │   Usuario   │
                    └─────┬──────┘
                          │
                    ┌─────▼──────┐
                    │  Login Page │
                    │ Connect     │
                    │ Wallet      │
                    └─────┬──────┘
                          │ eth_requestAccounts
                          │ → address + chainId
                    ┌─────▼──────────────────────────────────┐
                    │           DASHBOARD                      │
                    │                                          │
                    │  ┌─────────────┐    ┌─────────────┐     │
                    │  │  ws/binance/ │    │  ws/wallet/  │    │
                    │  │  ▲           │    │  ▲           │    │
                    │  │  │ klines    │    │  │ balance    │    │
                    │  │  │ trades    │    │  │ tokens     │    │
                    │  │  │ depth     │    │  │ events     │    │
                    │  │  │ ticker    │    │  │            │    │
                    │  └──┤           │    └──┤            │    │
                    │     ▼           │       ▼            │    │
                    │  ┌──────┐ ┌────┴┐  ┌───────────┐    │    │
                    │  │Chart │ │Order│  │ Balance   │    │    │
                    │  │      │ │Book │  │ Tokens    │    │    │
                    │  └──────┘ └─────┘  └───────────┘    │    │
                    │                                      │    │
                    │  ┌────────────────────────┐          │    │
                    │  │     BUY / SELL          │          │    │
                    │  │ ethers.js → DEX Router  │          │    │
                    │  │ PancakeSwap / Uniswap   │          │    │
                    │  └────────────────────────┘          │    │
                    └──────────────────────────────────────┘    │
                                                                │
                    ┌──────────────────────────────────────┐    │
                    │           WALLET PAGE                  │    │
                    │  Account │ Network │ Balances          │    │
                    │  Buy Crypto │ Deposit │ Holdings       │    │
                    └──────────────────────────────────────┘    │
                                                                │
                    ┌──────────────────────────────────────┐    │
                    │         TRANSACTIONS PAGE              │    │
                    │  Send Native │ Send Token │ Swap       │    │
                    └──────────────────────────────────────┘
```

---

## Notas Técnicas

### Reconexión de Wallet
- Al navegar entre páginas, la wallet se reconecta automáticamente usando `eth_accounts` (no re-solicita permisos).
- El estado se guarda en `localStorage` y `window.__walletState`.
- Si la chain no coincide con la esperada, se muestra un botón "Switch" que llama `wallet_switchEthereumChain`.

### Rebuild Provider
- Después de cambiar de red (`chainChanged`), ethers.js puede quedar desincronizado.
- `rebuildProvider()` crea un nuevo `BrowserProvider` limpio para evitar `NETWORK_ERROR`.

### Servir Estáticos con Daphne
- Daphne (ASGI) no sirve archivos estáticos por defecto.
- Se usa `staticfiles_urlpatterns()` en `urls.py` cuando `DEBUG=True`.

### Formato USD
- La función `fmtUsd()` maneja correctamente el valor `0` (evita `"$NaN"`).
- Usa `Intl.NumberFormat` para formato de moneda.

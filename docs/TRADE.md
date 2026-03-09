# Trading Dashboard — Documentación Técnica

Documentación del módulo de **Trading** de TradingWeb: panel estilo Binance con gráficas en tiempo real, order book, historial de trades, gestión de wallet, y ejecución de DEX swaps.

---

## Índice

1. [Arquitectura](#1-arquitectura)
2. [Dashboard de trading](#2-dashboard-de-trading)
3. [WebSocket Binance](#3-websocket-binance)
4. [Wallet](#4-wallet)
5. [DEX Swaps](#5-dex-swaps)
6. [Transacciones](#6-transacciones)
7. [Autenticación Trust Wallet](#7-autenticación-trust-wallet)
8. [Frontend](#8-frontend)
9. [Estilos](#9-estilos)
10. [Endpoints API](#10-endpoints-api)

---

## 1. Arquitectura

### Archivos del módulo Trading

| Archivo | Líneas | Rol |
|---|---|---|
| `static/js/dashboard.js` | 2,184 | Lógica completa del dashboard |
| `static/js/pageWallet.js` | 311 | Gestión de wallet |
| `static/js/transactions.js` | 282 | Historial de transacciones |
| `static/js/walletConnect.js` | 185 | Trust Wallet / MetaMask connection |
| `static/css/main.css` | 2,569 | Estilos Binance dark theme |
| `templates/dashboard.html` | 435 | Template del panel de trading |
| `templates/wallet.html` | 172 | Template de wallet |
| `templates/transactions.html` | 123 | Template de transacciones |
| `templates/login.html` | 63 | Template de login |
| `templates/base.html` | — | Template base (navbar + footer) |
| `Controllers/viewController.py` | — | Renders de páginas HTML |
| `Controllers/walletController.py` | — | API endpoints wallet |
| `Services/walletService.py` | 139 | Lógica de wallet |
| `Models/walletSessionModel.py` | — | Modelo de sesión de wallet |
| `WebSocket/binanceConsumer.py` | 148 | Consumer Binance WebSocket |
| `WebSocket/walletConsumer.py` | 140 | Consumer wallet events |
| `Routes/urls.py` | — | URL patterns de la app |
| `Routes/walletRouter.py` | — | Rutas API wallet |

### Diagrama de flujo

```
┌────────────────────┐     ┌──────────────────────┐     ┌─────────────────┐
│   dashboard.html   │◄────│   binanceConsumer.py  │◄────│  Binance WS API │
│   dashboard.js     │────►│   (148 líneas)        │────►│  stream.binance │
│   (2,184 + 435 ln) │     └──────────────────────┘     └─────────────────┘
└────────────────────┘
        │
        ▼
┌────────────────────┐     ┌──────────────────────┐
│   wallet.html      │◄────│   walletConsumer.py   │
│   pageWallet.js    │────►│   (140 líneas)        │
│   (311 + 172 ln)   │     └──────────────────────┘
└────────────────────┘
        │
        ▼
┌────────────────────┐     ┌──────────────────────┐
│   login.html       │────►│   walletController.py │
│   walletConnect.js │     │   walletService.py    │
│   (63 + 185 ln)    │     └──────────────────────┘
└────────────────────┘
```

---

## 2. Dashboard de trading

### Componentes del dashboard

El dashboard replica la interfaz de Binance con los siguientes paneles:

| Panel | Descripción |
|---|---|
| **Price Header** | Par actual, precio, cambio 24h, high/low, volumen |
| **Chart Area** | Gráfica de velas (TradingView widget o DOM mini-bars) |
| **Order Book** | Libro de órdenes buy/sell en tiempo real |
| **Trade History** | Últimos trades ejecutados en el mercado |
| **Order Form** | Formulario de compra/venta (Limit, Market, Stop) |
| **Open Orders** | Órdenes abiertas del usuario |
| **Pair Selector** | Selector de par BTC/USDT, ETH/USDT, etc. |
| **Balance** | Balance del usuario en la wallet conectada |

### Datos en tiempo real

El dashboard recibe datos via WebSocket desde Binance:

```javascript
// dashboard.js
const ws = new WebSocket(`wss://stream.binance.com:9443/ws/${pair}@kline_1m`);

ws.onmessage = (event) => {
    const data = JSON.parse(event.data);
    updatePrice(data.k.c);       // Precio de cierre
    updateVolume(data.k.v);      // Volumen
    updateCandlestick(data.k);   // Vela OHLC
};
```

### Mini-bars (optimización)

En lugar de cargar Chart.js (~200KB), el dashboard usa mini-bars DOM:

```javascript
function renderMiniBars(prices) {
    const container = document.querySelector('.mini-chart');
    container.innerHTML = '';
    const max = Math.max(...prices);
    const min = Math.min(...prices);

    prices.forEach(price => {
        const bar = document.createElement('div');
        bar.className = 'mini-bar';
        const height = ((price - min) / (max - min)) * 100;
        bar.style.height = `${Math.max(2, height)}%`;
        bar.style.backgroundColor = price >= prices[0]
            ? 'var(--clr-up)' : 'var(--clr-down)';
        container.appendChild(bar);
    });
}
```

### Auto-refresh

```javascript
// Refresh automático cada 60 segundos
setInterval(() => {
    refreshOrderBook();
    refreshTradeHistory();
    refreshBalance();
}, 60000);
```

---

## 3. WebSocket Binance

### `binanceConsumer.py` (148 líneas)

Consumer Django Channels que conecta al frontend con los streams de Binance.

```python
class BinanceConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.pair = self.scope['url_route']['kwargs'].get('pair', 'btcusdt')
        await self.accept()
        # Conectar al stream de Binance
        self.binance_ws = await websockets.connect(
            f'wss://stream.binance.com:9443/ws/{self.pair}@kline_1m'
        )
        asyncio.create_task(self._relay_messages())

    async def _relay_messages(self):
        """Relay mensajes de Binance al frontend."""
        async for message in self.binance_ws:
            await self.send(text_data=message)

    async def disconnect(self, code):
        if hasattr(self, 'binance_ws'):
            await self.binance_ws.close()
```

### Streams disponibles

| Stream | URL | Datos |
|---|---|---|
| Kline | `{pair}@kline_{interval}` | Velas OHLCV |
| Ticker | `{pair}@ticker` | Precio 24h, cambio, volumen |
| Depth | `{pair}@depth20` | Order book (20 niveles) |
| Trade | `{pair}@trade` | Trades individuales |
| Mini ticker | `{pair}@miniTicker` | Resumen compacto |

---

## 4. Wallet

### Trust Wallet / MetaMask connection

```javascript
// walletConnect.js
async function connectWallet() {
    if (typeof window.ethereum === 'undefined') {
        alert('Install Trust Wallet or MetaMask');
        return;
    }

    const accounts = await window.ethereum.request({
        method: 'eth_requestAccounts'
    });

    const address = accounts[0];
    const chainId = await window.ethereum.request({
        method: 'eth_chainId'
    });

    // Verificar chain (BSC = 0x38)
    if (chainId !== '0x38') {
        await switchToBSC();
    }

    // Generar JWT token
    const response = await fetch('/api/wallet/connect/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ address, chainId })
    });

    const { token } = await response.json();
    localStorage.setItem('jwt', token);
}
```

### Funciones de wallet (`pageWallet.js` — 311 líneas)

| Función | Descripción |
|---|---|
| `getBalance()` | Obtiene balance BNB/ETH del usuario |
| `getTokenBalances()` | Lista tokens ERC-20 del usuario |
| `sendTransaction()` | Envía BNB/ETH a otra dirección |
| `approveToken()` | Aprueba un token para swap en DEX router |
| `switchNetwork()` | Cambia entre BSC, Ethereum, etc. |

### Sesión de wallet (`walletSessionModel.py`)

```python
class WalletSession(models.Model):
    address     = models.CharField(max_length=42, unique=True)
    chain_id    = models.IntegerField(default=56)
    jwt_token   = models.TextField()
    created_at  = models.DateTimeField(auto_now_add=True)
    last_active = models.DateTimeField(auto_now=True)
    is_active   = models.BooleanField(default=True)
```

---

## 5. DEX Swaps

### Ejecución de swap (frontend)

```javascript
// Swap BNB → Token via PancakeSwap V2 Router
async function executeSwap(tokenAddress, amountBNB, slippage) {
    const router = new ethers.Contract(
        '0x10ED43C718714eb63d5aA57B78B54917F0Da2', // PancakeSwap V2 Router
        ROUTER_ABI,
        signer
    );

    const path = [WBNB_ADDRESS, tokenAddress];
    const deadline = Math.floor(Date.now() / 1000) + 300; // 5 min
    const amountIn = ethers.parseEther(amountBNB.toString());

    // Calcular amountOutMin con slippage
    const amounts = await router.getAmountsOut(amountIn, path);
    const amountOutMin = amounts[1] * BigInt(100 - slippage) / BigInt(100);

    const tx = await router.swapExactETHForTokensSupportingFeeOnTransferTokens(
        amountOutMin,
        path,
        userAddress,
        deadline,
        { value: amountIn, gasLimit: 300000 }
    );

    const receipt = await tx.wait();
    return receipt;
}
```

### Tipos de orden soportados

| Tipo | Descripción | Implementación |
|---|---|---|
| **Market** | Comprar/vender al precio actual | Swap directo en PancakeSwap |
| **Limit** | Orden a precio específico | Monitoreada en frontend, ejecuta cuando se alcanza |
| **Stop-Limit** | Activar limit al llegar a un precio | Monitoreada con trigger price |

---

## 6. Transacciones

### `transactions.js` (282 líneas)

Página de historial de transacciones del usuario.

| Feature | Descripción |
|---|---|
| Lista de txs | Todas las transacciones de la wallet |
| Filtros | Por tipo (swap, transfer, approve), token, fecha |
| Detalles | Hash, from, to, value, gas, status |
| BSCScan link | Link directo a BSCScan para cada tx |
| Paginación | 20 txs por página |

### Fuente de datos

```javascript
// Obtener transacciones de BSCScan API
async function fetchTransactions(address) {
    const url = `https://api.bscscan.com/api?module=account&action=txlist&address=${address}&sort=desc`;
    const response = await fetch(url);
    const data = await response.json();
    return data.result;
}
```

---

## 7. Autenticación Trust Wallet

### Flujo de autenticación

```
1. Usuario abre /login/
2. Clic en "Connect Wallet"
3. Trust Wallet / MetaMask popup → usuario aprueba
4. Frontend envía address + chainId al backend
5. Backend genera JWT (PyJWT + passlib)
6. JWT se guarda en localStorage
7. Todas las requests incluyen JWT en header
8. Backend verifica JWT en cada request
```

### JWT payload

```python
payload = {
    'address': '0x1234...abcd',
    'chain_id': 56,
    'iat': datetime.utcnow(),
    'exp': datetime.utcnow() + timedelta(hours=24),
}
token = jwt.encode(payload, SECRET_KEY, algorithm='HS256')
```

### Middleware de verificación

```python
def verify_jwt(request):
    auth_header = request.headers.get('Authorization', '')
    if not auth_header.startswith('Bearer '):
        return None
    token = auth_header.split(' ')[1]
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=['HS256'])
        return payload['address']
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None
```

---

## 8. Frontend

### Estructura de archivos

```
static/
├── css/
│   └── main.css          # 2,569 líneas — Binance dark theme completo
└── js/
    ├── dashboard.js      # 2,184 líneas — Dashboard de trading
    ├── pageSniper.js     # 1,862 líneas — Sniper Bot frontend
    ├── pageWallet.js     # 311 líneas — Wallet management
    ├── transactions.js   # 282 líneas — Transaction history
    └── walletConnect.js  # 185 líneas — Wallet connection

templates/
├── base.html             # Template base con navbar + footer
├── dashboard.html        # 435 líneas — Trading panel
├── sniper.html           # 519 líneas — Sniper Bot UI
├── wallet.html           # 172 líneas — Wallet page
├── transactions.html     # 123 líneas — Transaction history
└── login.html            # 63 líneas — Login / Connect wallet
```

### Librerías frontend (CDN)

| Librería | Versión | Uso |
|---|---|---|
| ethers.js | 6.x | Interacción con wallet y contratos |
| Font Awesome | 6.x | Iconos |
| Google Fonts | — | Fuente Inter |

> **Nota:** Chart.js fue removido por optimización de rendimiento. Se usan mini-bars DOM nativos.

---

## 9. Estilos

### `main.css` — 2,569 líneas

Theme Binance dark con variables CSS personalizables.

#### Variables principales

```css
:root {
    /* Backgrounds */
    --bg-primary:   #0b0e11;
    --bg-secondary: #1e2329;
    --bg-panel:     #181a20;
    --bg-hover:     #2b3139;

    /* Colores de trading */
    --clr-up:    #0ecb81;   /* Verde — precio sube */
    --clr-down:  #f6465d;   /* Rojo — precio baja */
    --clr-accent: #fcd535;  /* Amarillo Binance */

    /* Texto */
    --text-primary:   #eaecef;
    --text-secondary: #848e9c;
    --text-muted:     #5e6776;

    /* Bordes */
    --border-color: #2b3139;
    --border-radius: 4px;
}
```

#### Secciones del CSS

| Sección | Descripción |
|---|---|
| Base & Reset | Normalización, box-sizing, scrollbar custom |
| Layout | Grid system, flex containers, responsive breakpoints |
| Navbar | Barra superior con logo, navegación, wallet status |
| Dashboard | Order book, trade history, price header, chart area |
| Sniper Bot | Token table, feed cards, settings panel, status bar |
| Wallet | Balance cards, token list, send form |
| Forms | Inputs, buttons, selects, toggles, sliders |
| Modals | Popups de confirmación, detalles de token |
| Animations | Fade-in, slide, pulse, table row highlight |
| Responsive | Media queries para mobile/tablet |

---

## 10. Endpoints API

### Views (páginas HTML)

| URL | View | Template |
|---|---|---|
| `/` | `login_view` | `login.html` |
| `/login/` | `login_view` | `login.html` |
| `/dashboard/` | `dashboard_view` | `dashboard.html` |
| `/sniper/` | `sniper_view` | `sniper.html` |
| `/wallet/` | `wallet_view` | `wallet.html` |
| `/transactions/` | `transactions_view` | `transactions.html` |

### API endpoints (wallet)

| Método | URL | Descripción |
|---|---|---|
| `POST` | `/api/wallet/connect/` | Conectar wallet → JWT |
| `GET` | `/api/wallet/balance/` | Obtener balance |
| `POST` | `/api/wallet/disconnect/` | Desconectar sesión |
| `GET` | `/api/wallet/session/` | Verificar sesión activa |

### WebSocket endpoints

| URL | Consumer | Descripción |
|---|---|---|
| `ws/sniper/` | `SniperConsumer` | Sniper Bot bidireccional |
| `ws/binance/<pair>/` | `BinanceConsumer` | Stream datos Binance |
| `ws/wallet/` | `WalletConsumer` | Eventos wallet |

### Routing

```python
# TradingWeb/urls.py
urlpatterns = [
    path('', include('trading.Routes.urls')),
]

# trading/Routes/urls.py
urlpatterns = [
    path('', views.login_view),
    path('login/', views.login_view),
    path('dashboard/', views.dashboard_view),
    path('sniper/', views.sniper_view),
    path('wallet/', views.wallet_view),
    path('transactions/', views.transactions_view),
    path('api/wallet/', include('trading.Routes.walletRouter')),
]

# WebSocket/routing.py
websocket_urlpatterns = [
    re_path(r'ws/sniper/$', SniperConsumer.as_asgi()),
    re_path(r'ws/binance/(?P<pair>\w+)/$', BinanceConsumer.as_asgi()),
    re_path(r'ws/wallet/$', WalletConsumer.as_asgi()),
]
```

---

## Documentación relacionada

- [README.md](../README.md) — Guía general del proyecto, instalación, configuración
- [docs/SNIPER.md](SNIPER.md) — Documentación técnica del Sniper Bot (12 módulos, 18 capas)

---

*Última actualización: Junio 2025 — v4*

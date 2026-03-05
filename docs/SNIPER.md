# 🎯 TradingWeb — Módulo Sniper Bot

## Índice

1. [Descripción General](#descripción-general)
2. [Arquitectura del Pipeline](#arquitectura-del-pipeline)
3. [Stack Tecnológico](#stack-tecnológico)
4. [Backend — sniperService.py](#backend--sniperservicepy)
5. [WebSocket — sniperConsumer.py](#websocket--sniperconsumerpy)
6. [Frontend — pageSniper.js](#frontend--pageSniperjs)
7. [Interfaz de Usuario](#interfaz-de-usuario)
8. [Configuración (Settings)](#configuración-settings)
9. [Pipeline Paso a Paso](#pipeline-paso-a-paso)
10. [Análisis de Contratos](#análisis-de-contratos)
11. [Gestión de Posiciones](#gestión-de-posiciones)
12. [Protocolo WebSocket](#protocolo-websocket)
13. [Estructura de Archivos](#estructura-de-archivos)
14. [Diagramas](#diagramas)

---

## Descripción General

El **Sniper Bot** es un sistema automatizado para detectar, analizar y (opcionalmente) comprar tokens recién listados en exchanges descentralizados (DEX). Opera en BSC (PancakeSwap) y Ethereum (Uniswap V2).

### ¿Qué hace?

1. **Escanea** la blockchain bloque por bloque buscando eventos `PairCreated` en las factories de PancakeSwap/Uniswap.
2. **Detecta** nuevos pares de trading creados (nuevos tokens listados).
3. **Verifica** la liquidez del par (en USD).
4. **Analiza** el contrato del token buscando señales de scam (honeypot, impuestos altos, owner functions).
5. **Clasifica** el riesgo: SAFE 🟢 / WARNING 🟡 / DANGER 🔴.
6. **Notifica** oportunidades de snipe al usuario en tiempo real.
7. **Gestiona** posiciones activas con Take Profit y Stop Loss automáticos.

---

## Arquitectura del Pipeline

```
┌─────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐
│📡       │    │🔍        │    │🛡️        │    │💧        │    │🎯        │    │💰        │
│ Mempool │───►│ Token    │───►│ Contract │───►│Liquidity │───►│ Sniper  │───►│ Profit  │
│ Listener│    │ Detector │    │ Analyzer │    │ Detector │    │ Engine  │    │ Manager │
└─────────┘    └──────────┘    └──────────┘    └──────────┘    └──────────┘    └──────────┘
     │              │               │               │               │              │
   Blocks      PairCreated     Honeypot.is     Check Liq.      Opportunities    TP/SL
   polling      events         + ERC-20         reserves        + Auto-Buy      tracking
               from Factory    analysis
```

### Flujo detallado:

```
Por cada bloque nuevo:
│
├── 1. GET LOGS del Factory contract (PairCreated topic)
│       ↓
├── 2. Decode event → token0, token1, pair_address
│       ↓
├── 3. Identify → ¿Cuál es el nuevo token? (el que NO es WBNB/WETH)
│       ↓
├── 4. CHECK LIQUIDITY → pair.getReserves()
│       ├── < min_liquidity? → SKIP ❌
│       └── ≥ min_liquidity? → CONTINUE ✅
│           ↓
├── 5. ANALYZE CONTRACT:
│       ├── Read ERC-20: name, symbol, decimals, totalSupply, owner
│       ├── Call Honeypot.is API: isHoneypot, buyTax, sellTax
│       └── Calculate risk level
│           ↓
├── 6. SAFETY CHECK:
│       ├── is_honeypot? → REJECT 🍯
│       ├── buy_tax > max_buy_tax? → REJECT
│       ├── sell_tax > max_sell_tax? → REJECT
│       ├── only_safe && risk == "danger"? → REJECT
│       └── PASS → SNIPE OPPORTUNITY 🎯
│           ↓
└── 7. EMIT to frontend via WebSocket
```

---

## Stack Tecnológico

| Componente           | Tecnología                                    |
|----------------------|-----------------------------------------------|
| **Bot Engine**       | Python asyncio (async/await)                  |
| **Blockchain RPC**   | web3.py (HTTP Provider)                       |
| **Honeypot API**     | honeypot.is v2 (aiohttp)                     |
| **Price Feed**       | Binance REST API (aiohttp)                    |
| **WebSocket Server** | Django Channels (AsyncWebsocketConsumer)       |
| **Frontend**         | Vanilla JS (WebSocket client)                 |
| **ASGI**             | Daphne                                         |

---

## Backend — sniperService.py

**Archivo:** `trading/Services/sniperService.py` (~672 líneas)

### Constantes y ABIs

```python
# RPCs públicos (sin API key)
RPC_ENDPOINTS = {
    56: { "http": "https://bsc-dataseed1.binance.org",     "name": "BSC Mainnet" },
    1:  { "http": "https://eth.llamarpc.com",              "name": "Ethereum" },
}

# Factory contracts (donde se crean los pares)
FACTORY_ADDRESSES = {
    56: "0xcA143Ce32Fe78f1f7019d7d551a6402fC5350c73",  # PancakeSwap V2
    1:  "0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f",  # Uniswap V2
}

# Router contracts (para swaps)
ROUTER_ADDRESSES = {
    56: "0x10ED43C718714eb63d5aA57B78B54704E256024E",  # PancakeSwap V2
    1:  "0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D",  # Uniswap V2
}

# Wrapped native tokens
WETH_ADDRESSES = {
    56: "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c",  # WBNB
    1:  "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",  # WETH
}
```

### Data Classes

#### `TokenInfo`
```python
@dataclass
class TokenInfo:
    address: str
    name: str = "?"
    symbol: str = "?"
    decimals: int = 18
    total_supply: float = 0
    has_owner: bool = False        # ¿Tiene función owner()?
    owner_address: str = ""
    is_honeypot: bool = False      # ¿Honeypot detectado?
    buy_tax: float = 0             # % de impuesto de compra
    sell_tax: float = 0            # % de impuesto de venta
    has_blacklist: bool = False    # ¿Tiene blacklist?
    risk: str = "unknown"          # safe | warning | danger | unknown
    risk_reasons: list = []        # Lista de razones del riesgo
```

#### `NewPair`
```python
@dataclass
class NewPair:
    pair_address: str              # Dirección del par LP
    token0: str                    # Primer token del par
    token1: str                    # Segundo token del par
    new_token: str                 # El token NUEVO (no WBNB/WETH)
    base_token: str                # WBNB o WETH
    chain_id: int                  # 56 (BSC) o 1 (ETH)
    block_number: int = 0
    timestamp: float = 0
    liquidity_usd: float = 0      # Liquidez total en USD
    liquidity_native: float = 0   # Liquidez en BNB/ETH
    token_info: TokenInfo = None   # Resultado del análisis
```

#### `ActiveSnipe`
```python
@dataclass
class ActiveSnipe:
    token_address: str
    symbol: str
    chain_id: int
    buy_price_usd: float = 0      # Precio de compra
    buy_amount_native: float = 0   # BNB/ETH gastados
    buy_amount_tokens: float = 0   # Tokens recibidos
    buy_tx: str = ""               # Hash de la TX de compra
    current_price_usd: float = 0   # Precio actual
    pnl_percent: float = 0         # Ganancia/pérdida %
    take_profit: float = 40        # % para auto-venta (ganancia)
    stop_loss: float = 15          # % para auto-venta (pérdida)
    status: str = "active"         # active | sold | stopped
```

### Clase `ContractAnalyzer`

Analiza un contrato de token ERC-20 buscando señales de scam:

```python
class ContractAnalyzer:
    def __init__(self, w3: Web3, chain_id: int):
        self.w3 = w3
        self.chain_id = chain_id

    async def analyze(self, token_address: str) -> TokenInfo:
        # 1. Lee info básica: name, symbol, decimals, totalSupply
        # 2. Chequea owner() function
        # 3. Llama Honeypot.is API
        # 4. Calcula nivel de riesgo
        ...
```

#### Lógica de Riesgo

| Condición                    | Nivel     |
|------------------------------|-----------|
| `is_honeypot = True`        | 🔴 DANGER |
| `buy_tax > 30%` o `sell_tax > 30%` | 🔴 DANGER |
| `buy_tax > 10%` o `sell_tax > 10%` | 🟡 WARNING |
| `has_owner = True`           | 🟡 WARNING |
| Ninguna de las anteriores    | 🟢 SAFE   |

### Clase `SniperBot`

El motor principal del bot:

```python
class SniperBot:
    def __init__(self, chain_id: int = 56):
        self.chain_id = chain_id
        self.running = False
        self.settings = { ... }           # Configuración del usuario
        self.detected_pairs = []          # Pares detectados
        self.active_snipes = []           # Posiciones activas
        self.w3 = Web3(HTTPProvider(...)) # Conexión RPC
        self.analyzer = ContractAnalyzer(self.w3, chain_id)
        self.factory = ...               # Contrato Factory
```

#### Métodos Principales

| Método                      | Descripción                                      |
|-----------------------------|--------------------------------------------------|
| `run()`                     | Loop principal — poll blocks cada 3s             |
| `stop()`                    | Detiene el bot                                   |
| `update_settings(dict)`     | Actualiza configuración desde frontend           |
| `get_state() → dict`        | Retorna estado completo para sync                |
| `add_manual_snipe(...)`     | Registra posición comprada manualmente           |
| `_handle_new_pair(...)`     | Procesa un par nuevo detectado                   |
| `_get_pair_liquidity(...)`  | Consulta reserves del par LP                     |
| `_get_token_price_usd(...)` | Precio del token via router `getAmountsOut`      |
| `_fetch_native_price()`     | Obtiene BNB/ETH price de Binance API             |
| `_update_active_snipes()`   | Actualiza P&L de posiciones activas              |
| `_emit(type, data)`         | Emite evento al frontend via callback            |

#### Loop Principal (`run()`)

```python
async def run(self):
    last_block = self.w3.eth.block_number

    while self.running:
        current_block = self.w3.eth.block_number

        if current_block > last_block:
            # Escanea max 5 bloques por iteración
            logs = self.w3.eth.get_logs({
                "fromBlock": last_block + 1,
                "toBlock": min(current_block, last_block + 6),
                "address": FACTORY_ADDRESS,
                "topics": [PAIR_CREATED_TOPIC]
            })

            for log in logs:
                token0 = decode_from_topics(log)
                token1 = decode_from_topics(log)
                pair_addr = decode_from_data(log)
                await self._handle_new_pair(pair_addr, token0, token1, block)

        # Cada ~30s: actualizar precio nativo y P&L
        if price_tick >= 10:
            await self._fetch_native_price()
            await self._update_active_snipes()

        # Heartbeat cada 3s
        await self._emit("heartbeat", { block, pairs, snipes, price })
        await asyncio.sleep(3)
```

---

## WebSocket — sniperConsumer.py

**Archivo:** `trading/WebSocket/sniperConsumer.py` (~130 líneas)

Cada cliente WebSocket obtiene su propia instancia de `SniperBot`.

### Acciones del Cliente → Servidor

| Action              | Payload                                           | Descripción                     |
|---------------------|---------------------------------------------------|---------------------------------|
| `start`             | `{ chain_id: 56 }`                               | Crear e iniciar SniperBot       |
| `stop`              | `{}`                                              | Detener el bot                  |
| `get_state`         | `{}`                                              | Solicitar estado completo       |
| `update_settings`   | `{ settings: { min_liquidity_usd: 5000, ... } }` | Actualizar configuración        |
| `register_snipe`    | `{ token, symbol, buy_price, amount, tx }`        | Registrar posición manual       |

### Eventos del Servidor → Cliente

| Event Type          | Datos                                             | Cuándo                          |
|---------------------|---------------------------------------------------|---------------------------------|
| `bot_started`       | `{ chain_id }`                                    | Bot inició exitosamente         |
| `bot_stopped`       | `{}`                                              | Bot se detuvo                   |
| `bot_status`        | `{ status, chain_id, native_price }`              | Cambios de estado del bot       |
| `heartbeat`         | `{ block, pairs_detected, active_snipes, price }` | Cada 3 segundos                 |
| `new_pair_raw`      | `{ pair, token, block }`                          | Nuevo par detectado (raw)       |
| `liquidity_check`   | `{ token, liquidity_usd, passed }`                | Resultado de chequeo de liq.    |
| `contract_analysis` | `{ token, symbol, risk, buy_tax, sell_tax, ... }` | Resultado de análisis           |
| `snipe_opportunity` | `{ token, symbol, risk, liquidity_usd, pair }`    | Oportunidad de snipe detectada  |
| `snipe_update`      | `{ token, pnl_percent, buy_price, current_price }`| Actualización de P&L            |
| `take_profit_alert` | `{ token, symbol, pnl_percent, target }`          | TP alcanzado                    |
| `stop_loss_alert`   | `{ token, symbol, pnl_percent, target }`          | SL alcanzado                    |
| `full_state`        | `{ running, settings, detected_pairs, ... }`      | Sync completo del estado        |
| `settings_updated`  | `{ ...settings }`                                 | Settings actualizados           |
| `error`             | `{ message }`                                     | Error                           |

### Ciclo de Vida

```
Cliente se conecta → ws/sniper/
    │
    ├── connect() → accept() + bot = None
    │
    ├── receive("start") → _start_bot(chain_id)
    │       ├── Crear SniperBot(chain_id)
    │       ├── set_event_callback → reenvía eventos al WS
    │       └── asyncio.create_task(bot.run())
    │
    ├── receive("get_state") → bot.get_state() → send full_state
    │
    ├── receive("update_settings") → bot.update_settings(...)
    │
    ├── receive("stop") → bot.stop()
    │
    └── disconnect() → bot.stop() + cancel task
```

---

## Frontend — pageSniper.js

**Archivo:** `trading/static/js/pageSniper.js` (~492 líneas)

### Estructura

```javascript
(function() {
    "use strict";

    // 1. Wallet reconnect (reutiliza window.ethereum)
    // 2. DOM refs (todos los elementos de la UI)
    // 3. State (ws, botRunning, detectedTokens, activeSnipes, pipeStats)
    // 4. Feed — addFeed(text, type) → añade línea al live feed
    // 5. WebSocket — connectWS() → ws/sniper/
    // 6. Message Handler — handleMessage(msg) → switch(type)
    // 7. State Sync — syncState(state) → actualiza toda la UI
    // 8. UI Helpers — setBotRunning(), flashPipe(), addDetectedToken(), renderSnipes()
    // 9. Event Listeners — Start, Stop, Save Settings, Snipe button
    // 10. Init — reconnectWallet() + connectWS()
})();
```

### Funciones Clave

| Función              | Descripción                                           |
|----------------------|-------------------------------------------------------|
| `connectWS()`        | Abre WebSocket a ws/sniper/, auto-reconnect en 3s    |
| `sendWS(obj)`        | Envía mensaje JSON al servidor                        |
| `handleMessage(msg)` | Router de mensajes (switch por type)                  |
| `syncState(state)`   | Sincroniza UI completa con estado del bot             |
| `setBotRunning(bool)`| Toggle visual: dot, texto, botones, chain select     |
| `flashPipe(id)`      | Animación flash en un paso del pipeline               |
| `addFeed(text, type)`| Añade línea al live feed (max 200 líneas)            |
| `addDetectedToken(d)`| Añade fila a tabla de tokens detectados               |
| `renderSnipes()`     | Re-renderiza tabla de posiciones activas              |
| `updateSnipeRow(d)`  | Actualiza una fila específica de snipe                |

### Tipos de Feed

| Tipo         | Color    | Uso                            |
|--------------|----------|--------------------------------|
| `system`     | Gris     | Mensajes del sistema           |
| `detect`     | Azul     | Token detectado                |
| `good`       | Verde    | Análisis positivo              |
| `warn`       | Amarillo | Advertencias                   |
| `error`      | Rojo     | Errores / Honeypot / Danger    |
| `opportunity`| Dorado   | Oportunidad de snipe           |

---

## Interfaz de Usuario

### Layout

```
┌──────────────────────────────────────────────────────────────┐
│  TOPBAR: Trade │ Transactions │ Wallet │ [Sniper] │ Wallet  │
├────────────────┬─────────────────────────────────────────────┤
│                │                                             │
│  SIDEBAR       │  MAIN AREA                                  │
│                │                                             │
│  ┌──────────┐  │  ┌────────────────────────────────────────┐ │
│  │🎯 Sniper │  │  │ 🔬 Pipeline                            │ │
│  │  Bot     │  │  │ 📡→🔍→🛡️→💧→🎯→💰                    │ │
│  │          │  │  │ (con contadores y animación flash)      │ │
│  │ Status   │  │  └────────────────────────────────────────┘ │
│  │ Chain    │  │                                             │
│  │ Start    │  │  ┌────────────────────────────────────────┐ │
│  │ Stop     │  │  │ 🔍 Detected Tokens                     │ │
│  │          │  │  │ Token │ Risk │ Buy% │ Sell% │ Liq │ Act│ │
│  │ Stats:   │  │  │ ------|------|------|-------|-----|----│ │
│  │ Block    │  │  │ PEPE  │ SAFE │  2%  │  3%  │ $8k │ 🎯│ │
│  │ Pairs    │  │  │ SCAM  │ DANG │ 50%  │ 99%  │ $1k │ 🔗│ │
│  │ Active   │  │  └────────────────────────────────────────┘ │
│  │ Price    │  │                                             │
│  └──────────┘  │  ┌────────────────────────────────────────┐ │
│                │  │ 💼 Active Positions                     │ │
│  ┌──────────┐  │  │ Token │ Buy │ Curr │ P&L │ TP/SL │ St │ │
│  │⚙️ Settings│  │  │ ------|-----|------|-----|-------|----│ │
│  │          │  │  │ PEPE  │$0.01│$0.02 │+100%│+40/-15│ ACT│ │
│  │ Min Liq  │  │  └────────────────────────────────────────┘ │
│  │ Max Tax  │  │                                             │
│  │ Buy Amt  │  │  ┌────────────────────────────────────────┐ │
│  │ TP / SL  │  │  │ 📡 Live Feed                           │ │
│  │ Slippage │  │  │ [12:00:01] 🔍 New pair: 0xABC @ block │ │
│  │ OnlySafe │  │  │ [12:00:02] 💧 Liquidity: $8,000 ✅    │ │
│  │ AutoBuy  │  │  │ [12:00:03] 🛡️ PEPE — 🟢 SAFE          │ │
│  │ [💾 Save]│  │  │ [12:00:04] 🎯 OPPORTUNITY: PEPE       │ │
│  └──────────┘  │  └────────────────────────────────────────┘ │
└────────────────┴─────────────────────────────────────────────┘
```

### Componentes Visuales

#### Bot Status
- **Dot verde pulsante**: Bot corriendo.
- **Dot gris**: Bot detenido.
- **Texto**: "Running" / "Stopped".
- **Chain label**: "BSC" / "ETH".

#### Pipeline
- 6 pasos: Mempool → Token Detector → Analyzer → Liquidity → Sniper → Profit.
- Cada paso tiene **icono + label + contador**.
- **Flash animation**: El paso activo parpadea verde durante 600ms.

#### Detected Tokens Table
- **Token**: Símbolo + dirección abreviada.
- **Risk Badge**: `SAFE` (verde) / `WARN` (amarillo) / `DANGER` (rojo) / `???` (gris).
- **Buy Tax / Sell Tax**: Porcentajes del honeypot.is.
- **Liquidity**: Valor USD del liquidity pool.
- **Action**: Botón "🎯 Snipe" (abre DEX Screener) + "🔗" (abre BscScan/Etherscan).

#### Active Positions Table
- **Token**: Símbolo.
- **Buy Price / Current**: Precios en USD.
- **P&L**: Porcentaje con color verde (ganancia) o rojo (pérdida).
- **TP / SL**: Objetivos de Take Profit y Stop Loss.
- **Status**: `ACTIVE` (azul) / `SOLD` (verde) / `STOPPED` (rojo).

#### Live Feed
- Scroll automático al final.
- Máximo 200 líneas (las más viejas se eliminan).
- Color-coded por tipo de evento.

---

## Configuración (Settings)

### Parámetros Configurables

| Setting               | Default | Descripción                                    |
|-----------------------|---------|------------------------------------------------|
| `min_liquidity_usd`  | $5,000  | Liquidez mínima del par para considerar        |
| `max_buy_tax`         | 10%     | Impuesto de compra máximo aceptable            |
| `max_sell_tax`        | 15%     | Impuesto de venta máximo aceptable             |
| `buy_amount_native`   | 0.05    | BNB/ETH a gastar por snipe                     |
| `take_profit`         | 40%     | % de ganancia para alerta de venta             |
| `stop_loss`           | 15%     | % de pérdida para alerta de venta              |
| `slippage`            | 12%     | Tolerancia de slippage para swaps              |
| `only_safe`           | ✅ On   | Solo considerar tokens clasificados como "safe" |
| `auto_buy`            | ❌ Off  | Ejecutar compra automáticamente (¡precaución!) |

### Persistencia
- Los settings se guardan **por sesión WebSocket**.
- Al reconectar, se envía `get_state` para sincronizar.
- El botón "💾 Save Settings" envía `update_settings` al backend.

---

## Pipeline Paso a Paso

### Paso 1: 📡 Mempool Listener (Block Scanner)

```
Cada 3 segundos:
  currentBlock = w3.eth.block_number
  if currentBlock > lastBlock:
    scan blocks [lastBlock+1 ... lastBlock+5]
```

- **Entrada**: Número de bloque actual.
- **Salida**: Logs de `PairCreated` events.
- **Rate**: ~3 segundos por ciclo, máximo 5 bloques por ciclo.

### Paso 2: 🔍 Token Detector

```
PairCreated(token0, token1, pair_address, allPairsLength)
  → Identifica cuál token es NUEVO (no es WBNB/WETH)
  → Emite "new_pair_raw"
```

- **Entrada**: Log de evento PairCreated.
- **Salida**: `{ pair_address, new_token, base_token }`.
- **Filtro**: Ignora pares donde ningún token es WBNB/WETH.

### Paso 3: 💧 Liquidity Detector

```
pair.getReserves() → [reserve0, reserve1, timestamp]
  → Identifica cuál reserve es WBNB/WETH
  → liquidity_usd = native_reserve × native_price × 2
  → Emite "liquidity_check"
```

- **Entrada**: Dirección del par LP.
- **Salida**: `{ liquidity_native, liquidity_usd, passed }`.
- **Filtro**: `liquidity_usd < min_liquidity_usd` → SKIP.

### Paso 4: 🛡️ Contract Analyzer

```
1. Read ERC-20:
   - name(), symbol(), decimals()
   - totalSupply(), owner()

2. Honeypot.is API:
   GET https://api.honeypot.is/v2/IsHoneypot?address={addr}&chainID={id}
   → isHoneypot, buyTax, sellTax

3. Calculate Risk:
   - honeypot → DANGER
   - tax > 30% → DANGER
   - tax > 10% → WARNING
   - has owner → WARNING
   - else → SAFE
```

- **Entrada**: Dirección del token.
- **Salida**: `TokenInfo` completo con risk level.
- **API**: honeypot.is v2 (timeout 10s, gratuito).

### Paso 5: 🎯 Sniper Engine

```
Safety check:
  - !is_honeypot
  - buy_tax ≤ max_buy_tax
  - sell_tax ≤ max_sell_tax
  - !(only_safe && risk == "danger")

Si pasa → Emite "snipe_opportunity"
Si auto_buy → (futuro) ejecutar swap
```

- **Entrada**: `NewPair` con `TokenInfo`.
- **Salida**: Evento `snipe_opportunity` al frontend.
- **Acción**: Actualmente solo notifica. Auto-buy emite alerta pero no ejecuta swap.

### Paso 6: 💰 Profit Manager

```
Cada ~30 segundos:
  Para cada ActiveSnipe con status == "active":
    current_price = router.getAmountsOut(1e18, [token, WBNB])
    pnl = ((current - buy) / buy) × 100

    if pnl ≥ take_profit → Emite "take_profit_alert"
    if pnl ≤ -stop_loss → Emite "stop_loss_alert"
```

- **Entrada**: Lista de posiciones activas.
- **Salida**: Eventos de TP/SL alerts + actualizaciones de P&L.
- **Rate**: Cada ~30 segundos (10 ciclos × 3s).

---

## Análisis de Contratos

### Honeypot.is API

```
GET https://api.honeypot.is/v2/IsHoneypot
  ?address=0x...
  &chainID=56

Response:
{
  "honeypotResult": {
    "isHoneypot": false
  },
  "simulationResult": {
    "buyTax": 2.5,
    "sellTax": 3.0
  }
}
```

### Señales de Riesgo

| Señal                        | Impacto      | Descripción                            |
|------------------------------|-------------|----------------------------------------|
| **Honeypot**                 | 🔴 CRÍTICO  | No se puede vender el token            |
| **Tax > 30%**                | 🔴 ALTO     | Impuesto excesivo = pérdida garantizada|
| **Tax > 10%**                | 🟡 MEDIO    | Impuesto alto pero podría ser legítimo |
| **Owner activo**             | 🟡 MEDIO    | El dueño puede modificar el contrato  |
| **Blacklist**                | 🟡 MEDIO    | Pueden bloquear tu dirección           |

---

## Gestión de Posiciones

### Registrar Posición Manual

Cuando el usuario hace click en "🎯 Snipe" y compra manualmente:

```javascript
// Frontend envía:
sendWS({
    action: "register_snipe",
    token: "0xABC...",
    symbol: "PEPE",
    buy_price: 0.0001,
    amount: 0.05,
    tx: "0xDEF..."
});
```

```python
# Backend registra:
ActiveSnipe(
    token_address="0xABC...",
    symbol="PEPE",
    buy_price_usd=0.0001,
    buy_amount_native=0.05,
    take_profit=40,    # de settings
    stop_loss=15,      # de settings
    status="active"
)
```

### Monitoreo de P&L

```
Cada 30s → _update_active_snipes():
  current_price = router.getAmountsOut()
  pnl = ((current - buy) / buy) × 100

  → Emite "snipe_update" con nuevo pnl
  → Si pnl ≥ TP → Emite "take_profit_alert" 🏆
  → Si pnl ≤ -SL → Emite "stop_loss_alert" 🛑
```

---

## Protocolo WebSocket

### URL
```
ws://localhost:8000/ws/sniper/
```

### Formato de Mensajes

#### Cliente → Servidor
```json
{
    "action": "start | stop | get_state | update_settings | register_snipe",
    "chain_id": 56,
    "settings": { ... },
    "token": "0x...",
    ...
}
```

#### Servidor → Cliente
```json
{
    "type": "heartbeat | new_pair_raw | contract_analysis | snipe_opportunity | ...",
    "timestamp": 1709654400.123,
    "data": { ... }
}
```

### Ejemplo de Sesión Completa

```
CLIENT                              SERVER
  │                                    │
  ├── connect ──────────────────────►  │ accept()
  │                                    │
  ├── { action: "get_state" } ──────►  │
  │                                    ├── { type: "full_state", data: {...} }
  │  ◄──────────────────────────────── │
  │                                    │
  ├── { action: "start",              │
  │    chain_id: 56 } ─────────────►  │ SniperBot(56)
  │                                    │ asyncio.create_task(bot.run())
  │                                    ├── { type: "bot_started" }
  │  ◄──────────────────────────────── │
  │                                    │
  │                                    │ ... scanning blocks ...
  │                                    │
  │                                    ├── { type: "heartbeat", data: { block: 12345 } }
  │  ◄──────────────────────────────── │
  │                                    │
  │                                    │ ... PairCreated found! ...
  │                                    │
  │                                    ├── { type: "new_pair_raw", data: { token: "0x..." } }
  │  ◄──────────────────────────────── │
  │                                    ├── { type: "liquidity_check", data: { passed: true } }
  │  ◄──────────────────────────────── │
  │                                    ├── { type: "contract_analysis", data: { risk: "safe" } }
  │  ◄──────────────────────────────── │
  │                                    ├── { type: "snipe_opportunity", data: { symbol: "PEPE" } }
  │  ◄──────────────────────────────── │
  │                                    │
  ├── { action: "stop" } ──────────►  │ bot.stop()
  │                                    ├── { type: "bot_stopped" }
  │  ◄──────────────────────────────── │
  │                                    │
  ├── disconnect ───────────────────►  │ cleanup
```

---

## Estructura de Archivos

```
TradingWeb/
├── trading/
│   ├── Services/
│   │   └── sniperService.py         # 🧠 Motor del bot (672 líneas)
│   │       ├── Constants & ABIs      #   RPC, Factory, Router, WETH, event topics
│   │       ├── TokenRisk (Enum)      #   safe | warning | danger | unknown
│   │       ├── TokenInfo (dataclass) #   Resultado de análisis de token
│   │       ├── NewPair (dataclass)   #   Par detectado
│   │       ├── ActiveSnipe (dataclass)#  Posición activa
│   │       ├── ContractAnalyzer      #   Analiza contratos ERC-20
│   │       └── SniperBot             #   Motor principal con polling loop
│   │
│   ├── WebSocket/
│   │   ├── routing.py               # ws/sniper/ → SniperConsumer
│   │   └── sniperConsumer.py        # 🔌 Bridge WS ↔ SniperBot (130 líneas)
│   │
│   ├── templates/
│   │   └── sniper.html              # 🖥️ Página del Sniper Bot (220 líneas)
│   │
│   └── static/
│       ├── js/
│       │   └── pageSniper.js        # 🎮 Controlador frontend (492 líneas)
│       └── css/
│           └── main.css             # 🎨 Incluye ~300 líneas de CSS para sniper
│
└── docs/
    ├── TRADE.md                     # Documentación del módulo Trade
    └── SNIPER.md                    # Este archivo
```

---

## Diagramas

### Flujo Completo del Sniper

```
     ┌──────────────────────────────────────────────────────┐
     │                    BLOCKCHAIN                          │
     │                                                        │
     │  Block N     Block N+1     Block N+2                   │
     │  ┌──────┐   ┌──────────┐  ┌──────┐                    │
     │  │ ...  │   │PairCreated│  │ ...  │                    │
     │  └──────┘   │ token0    │  └──────┘                    │
     │             │ token1    │                               │
     │             │ pair_addr │                               │
     │             └─────┬─────┘                               │
     └───────────────────┼────────────────────────────────────┘
                         │
              ┌──────────▼──────────┐
              │   SNIPER BOT ENGINE  │
              │   (sniperService.py) │
              │                      │
              │  1. get_logs()       │
              │  2. decode event     │
              │  3. check liquidity  │──── pair.getReserves()
              │  4. analyze contract │──── honeypot.is API
              │  5. classify risk    │
              │  6. emit events      │
              └──────────┬──────────┘
                         │
              ┌──────────▼──────────┐
              │  SNIPER CONSUMER     │
              │  (sniperConsumer.py) │
              │                      │
              │  Forward events      │
              │  via WebSocket       │
              └──────────┬──────────┘
                         │ ws/sniper/
              ┌──────────▼──────────┐
              │     FRONTEND         │
              │   (pageSniper.js)    │
              │                      │
              │  Pipeline animation  │
              │  Detected table      │
              │  Active positions    │
              │  Live feed           │
              └──────────────────────┘
```

### Ciclo de Vida de un Token Detectado

```
                    PairCreated event
                         │
                         ▼
                  ┌──────────────┐
                  │ Pair detected │
                  │ → new_pair_raw│
                  └──────┬───────┘
                         │
                ┌────────▼────────┐
                │ Liquidity Check  │
                │ → getReserves()  │
                └────────┬────────┘
                    ┌────┴────┐
                    │         │
               < min_liq   ≥ min_liq
                    │         │
                    ▼         ▼
                 SKIP    ┌─────────────┐
                         │  Analyze     │
                         │  Contract    │
                         └──────┬──────┘
                           ┌────┴────┐
                           │         │
                      DANGER/HP    SAFE/WARN
                           │         │
                           ▼         ▼
                        REJECT   ┌───────────┐
                                 │ OPPORTUNITY│
                                 │ (snipe!)   │
                                 └─────┬─────┘
                                       │
                              ┌────────┴────────┐
                              │                 │
                         auto_buy=ON       auto_buy=OFF
                              │                 │
                              ▼                 ▼
                         (futuro:          Notificar
                          execute)         al usuario
                                                │
                                         User clicks
                                         "🎯 Snipe"
                                                │
                                                ▼
                                         DEX Screener
                                         (manual buy)
                                                │
                                                ▼
                                         register_snipe
                                                │
                                                ▼
                                         ┌──────────────┐
                                         │Active Position│
                                         │P&L monitoring │
                                         │TP/SL alerts  │
                                         └──────────────┘
```

---

## Notas Importantes

### Seguridad
- ⚠️ **Auto-buy está desactivado por defecto** — El bot solo detecta y notifica.
- ⚠️ El bot **NO** tiene acceso a las private keys — las transacciones se ejecutan desde el browser via ethers.js.
- ⚠️ **honeypot.is** es una heurística, no es 100% infalible. Siempre DYOR (Do Your Own Research).

### Limitaciones Actuales
- El polling es cada 3 segundos (no mempool real, sino block scanning).
- No ejecuta swaps automáticamente (el usuario abre DEX Screener manualmente).
- Una instancia de bot por conexión WebSocket.
- Los settings no persisten entre sesiones (se resetean al reconectar).

### Mejoras Potenciales
- Auto-buy con ejecución real de swap desde el backend.
- Sell automático al alcanzar TP/SL.
- Soporte para más chains (Polygon, Arbitrum, Base).
- Persistencia de configuración en base de datos.
- Dashboard de rendimiento histórico.
- Alerts via Telegram/Discord.

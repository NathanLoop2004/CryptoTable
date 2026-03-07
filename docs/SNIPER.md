# Sniper Bot — Documentación Técnica Completa

> Motor de detección y trading automático de tokens nuevos en BSC/ETH.  
> Archivo principal: `trading/Services/sniperService.py` (~1860 líneas)

---

## Índice

1. [Arquitectura General](#arquitectura-general)
2. [Pipeline Completo](#pipeline-completo)
3. [Dataclasses](#dataclasses)
4. [ContractAnalyzer — Análisis de Seguridad](#contractanalyzer--análisis-de-seguridad)
5. [5 APIs de Seguridad](#5-apis-de-seguridad)
6. [Sistema de Riesgo](#sistema-de-riesgo)
7. [Enrichment Dual (Fast/Slow)](#enrichment-dual-fastslow)
8. [Buy Gating — Filtros de Compra](#buy-gating--filtros-de-compra)
9. [Validación de LP Lock (Anti Rug-Pull)](#validación-de-lp-lock-anti-rug-pull)
10. [Smart Entry Gate (Anti-Pump)](#smart-entry-gate-anti-pump)
11. [Auto-Buy & Auto-Sell](#auto-buy--auto-sell)
12. [WebSocket Events](#websocket-events)
13. [Main Loop (`run()`)](#main-loop-run)
14. [Settings Configurables](#settings-configurables)
15. [RPC & Blockchain](#rpc--blockchain)
16. [Frontend Integration](#frontend-integration)

---

## Arquitectura General

```
┌─────────────────────────────────────────────────────────────────┐
│                      SniperBot.run()                            │
│                                                                 │
│  ┌──────────┐    ┌──────────────┐    ┌───────────────────────┐  │
│  │ Block    │───▶│ PairCreated  │───▶│ ContractAnalyzer      │  │
│  │ Scanner  │    │ Log Filter   │    │ (5 APIs + on-chain)   │  │
│  │ ~1.5s    │    │              │    │                       │  │
│  └──────────┘    └──────────────┘    └──────────┬────────────┘  │
│                                                 │               │
│  ┌──────────────────────┐    ┌──────────────────▼────────────┐  │
│  │ Enrichment Dual      │    │ Buy Gating                    │  │
│  │ Fast ~3s / Slow ~15s │───▶│ Safety + LP Lock + Price Chk  │  │
│  └──────────────────────┘    └──────────────────┬────────────┘  │
│                                                 │               │
│  ┌──────────────────────┐    ┌──────────────────▼────────────┐  │
│  │ Sync WS Listener     │    │ snipe_opportunity → Frontend  │  │
│  │ (real-time LP price) │    │ Auto-Buy (ethers.js swap)     │  │
│  └──────────────────────┘    └──────────────────┬────────────┘  │
│                                                 │               │
│  ┌──────────────────────────────────────────────▼────────────┐  │
│  │ P&L Monitor (~3s)  ──▶  TP/SL/TimeLimit  ──▶  Auto-Sell  │  │
│  └───────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

---

## Pipeline Completo

1. **Start** → El bot escanea bloques nuevos cada `poll_interval` (~1.5s)
2. **Detecta** → Busca eventos `PairCreated` en PancakeSwap V2 / Uniswap V2
3. **Identifica token** → Separa token nuevo de WBNB/WETH
4. **Verifica liquidez** → `pair.getReserves()` → calcula USD con `native_price_usd`
5. **Analiza** → `ContractAnalyzer.analyze()` ejecuta 5 APIs + lectura on-chain en paralelo
6. **Clasifica riesgo** → `_calculate_risk()` → SAFE 🟢 / WARNING 🟡 / DANGER 🔴
7. **Emite `token_detected`** → Frontend muestra en tabla + feed card
8. **Buy Gating** → Filtros de seguridad, LP lock, precio
9. **Emite `snipe_opportunity`** → Frontend decide comprar (auto o manual)
10. **Auto-Buy** → Frontend ejecuta swap via ethers.js + registra posición en backend
11. **P&L Monitor** → `_update_active_snipes()` cada ~3s
12. **Auto-Sell** → TP (40%), SL (20%), o Max Hold Hours

---

## Dataclasses

### `TokenRisk` (Enum)

```python
class TokenRisk(Enum):
    SAFE = "safe"       # 🟢 Sin flags peligrosos, ≥2 APIs OK
    WARNING = "warning"  # 🟡 Señales de riesgo menores
    DANGER = "danger"    # 🔴 Honeypot, scam, o flags críticos
    UNKNOWN = "unknown"  # ⚪ Sin datos suficientes
```

### `TokenInfo`

Contiene **TODA** la información de un token analizado:

| Campo | Tipo | Descripción |
|---|---|---|
| `address` | str | Dirección del contrato |
| `name`, `symbol`, `decimals` | str/int | Datos básicos ERC-20 |
| `total_supply` | float | Supply total |
| **Security Flags (GoPlus)** | | |
| `has_owner` | bool | Contrato tiene dueño con privilegios |
| `is_honeypot` | bool | No se puede vender |
| `buy_tax`, `sell_tax` | float | % de tax al comprar/vender |
| `has_blacklist` | bool | Puede bloquear wallets |
| `is_mintable` | bool | Owner puede crear más tokens |
| `can_pause_trading` | bool | Puede pausar trading |
| `is_proxy` | bool | Contrato upgradeable |
| `has_hidden_owner` | bool | Owner oculto |
| `can_self_destruct` | bool | Puede autodestruirse |
| `owner_can_change_balance` | bool | Owner modifica balances |
| `cannot_sell_all` | bool | No puedes vender el 100% |
| `is_open_source` | bool | Código verificado |
| **Cross-Platform** | | |
| `listed_coingecko` | bool | Listado en CoinGecko |
| `tokensniffer_score` | int | Score 0-100 (-1 = sin datos) |
| `tokensniffer_is_scam` | bool | Detectado como scam |
| **DexScreener** | | |
| `dexscreener_volume_24h` | float | Volumen en USD |
| `dexscreener_liquidity` | float | Liquidez en USD |
| `dexscreener_buys_24h` / `sells_24h` | int | Transacciones |
| `dexscreener_age_hours` | float | Edad del par en horas |
| `dexscreener_price_change_m5` | float | Cambio precio 5 min (%) |
| `dexscreener_price_change_h1` | float | Cambio precio 1 hora (%) |
| `dexscreener_price_change_h6` | float | Cambio precio 6 horas (%) |
| `dexscreener_price_change_h24` | float | Cambio precio 24 horas (%) |
| **LP Lock** | | |
| `lp_locked` | bool | LP está bloqueada |
| `lp_lock_percent` | float | % de LP bloqueada |
| `lp_lock_hours_remaining` | float | Horas restantes del lock |
| `lp_lock_end_timestamp` | float | Timestamp Unix de expiración |
| `lp_lock_source` | str | PinkLock / Unicrypt / Team.Finance |
| **API Tracking** | | |
| `_goplus_ok` | bool | GoPlus respondió OK |
| `_honeypot_ok` | bool | Honeypot.is respondió OK |
| `_dexscreener_ok` | bool | DexScreener respondió OK |
| `_coingecko_ok` | bool | CoinGecko respondió OK |
| `_tokensniffer_ok` | bool | TokenSniffer respondió OK |
| **Overall** | | |
| `risk` | str | "safe" / "warning" / "danger" / "unknown" |
| `risk_reasons` | list[str] | Lista de razones con emojis |

### `NewPair`

Representa un par LP recién detectado:

| Campo | Tipo | Descripción |
|---|---|---|
| `pair_address` | str | Dirección del par LP |
| `token0`, `token1` | str | Tokens del par |
| `new_token` | str | Token nuevo (no WBNB/WETH) |
| `base_token` | str | WBNB o WETH |
| `chain_id` | int | 56 (BSC) o 1 (ETH) |
| `liquidity_usd` | float | Liquidez en USD |
| `token_info` | TokenInfo? | Info completa del token |

### `ActiveSnipe`

Posición activa después de comprar:

| Campo | Tipo | Default | Descripción |
|---|---|---|---|
| `token_address` | str | — | Token comprado |
| `symbol` | str | — | Símbolo |
| `buy_price_usd` | float | 0 | Precio de compra |
| `buy_amount_native` | float | 0 | BNB/ETH gastados |
| `current_price_usd` | float | 0 | Precio actual |
| `pnl_percent` | float | 0 | Ganancia/pérdida % |
| `take_profit` | float | **40** | Auto-sell al ganar % |
| `stop_loss` | float | **20** | Auto-sell al perder % |
| `max_hold_hours` | float | 0 | Tiempo máximo (0 = sin límite) |
| `status` | str | "active" | active / selling / sold / stopped |

---

## ContractAnalyzer — Análisis de Seguridad

La clase `ContractAnalyzer` ejecuta:

1. **Lectura on-chain** — `name()`, `symbol()`, `decimals()`, `totalSupply()`, `owner()`
2. **5 APIs en paralelo** — GoPlus, Honeypot.is, DexScreener, CoinGecko, TokenSniffer
3. **Rebuild flag reasons** — Genera lista de razones legible
4. **Calculate risk** — Clasifica como SAFE/WARNING/DANGER

```python
async def analyze(self, token_address: str) -> TokenInfo:
    info = TokenInfo(address=token_address)
    await self._read_basic_info(info)          # on-chain
    await asyncio.gather(                       # 5 APIs en paralelo
        self._check_goplus_api(info),
        self._check_honeypot_api(info),
        self._check_dexscreener_api(info),
        self._check_coingecko_api(info),
        self._check_tokensniffer_api(info),
    )
    info.risk_reasons = self._rebuild_flag_reasons(info)
    self._calculate_risk(info)
    return info
```

---

## 5 APIs de Seguridad

### 1. GoPlus Security (`_check_goplus_api`)

- **URL:** `https://api.gopluslabs.io/api/v1/token_security/{chain_id}?contract_addresses={addr}`
- **Qué detecta:** 15+ flags de seguridad + información de LP lock
- **LP Lock Detection:**
  - Itera `lp_holders[].is_locked`, acumula `total_lp_locked_pct`
  - Busca `locked_detail[].end_time` para encontrar el lock más largo (`best_lock_end`)
  - Identifica fuente: **PinkLock**, **Unicrypt**, **Team.Finance** (por nombre del holder)
  - Calcula `lp_lock_hours_remaining` desde `best_lock_end`

### 2. Honeypot.is (`_check_honeypot_api`)

- **URL:** `https://api.honeypot.is/v2/IsHoneypot?address={addr}&chainID={chain_id}`
- **Qué detecta:** Simulación real de compra/venta, tax exacto, honeypot
- **Campos:** `is_honeypot`, `buy_tax`, `sell_tax`

### 3. DexScreener (`_check_dexscreener_api`)

- **URL:** `https://api.dexscreener.com/latest/dex/tokens/{addr}`
- **Qué detecta:** Volumen, liquidez, transacciones, edad del par, precio histórico
- **Campos:** `volume_24h`, `liquidity`, `buys_24h`, `sells_24h`, `age_hours`, `price_change_m5/h1/h6/h24`
- **Nota:** Tarda en indexar tokens nuevos — por eso existe el ciclo "slow" de enrichment

### 4. CoinGecko (`_check_coingecko_api`)

- **URL:** `https://api.coingecko.com/api/v3/coins/{platform}/contract/{addr}`
- **Qué detecta:** Si el token está listado en CoinGecko (señal positiva fuerte)
- **Campos:** `listed_coingecko`, `coingecko_id`, `has_social_links`, `has_website`

### 5. TokenSniffer (`_check_tokensniffer_api`)

- **URL:** `https://tokensniffer.com/api/v2/tokens/{chain_id}/{addr}`
- **Qué detecta:** Score de scam 0-100, patrones fraudulentos
- **Campos:** `tokensniffer_score`, `tokensniffer_is_scam`

---

## Sistema de Riesgo

### `_rebuild_flag_reasons(info)` → `list[str]`

Reconstruye la lista de razones desde las flags almacenadas. Se ejecuta en cada ciclo de enrichment para mantener las razones actualizadas.

**Secciones que genera:**

| Sección | Ejemplos |
|---|---|
| **Honeypot** | 🍯 Honeypot detectado, 💰 Buy tax: 5%, Sell tax: 8% |
| **GoPlus Flags** | 🖨️ Mintable, 🚫 Blacklist, ⏸️ Pausable, 🔄 Proxy... |
| **TokenSniffer** | 🔍 TokenSniffer: 45/100, 🚨 Detectado como SCAM |
| **LP Lock** | 🔒 LP Locked 95% — 720h restantes, 🔐 Lock via: PinkLock |
| **LP Lock Warnings** | ⚠️ Lock insuficiente: solo 30% (mínimo 80%), ⚠️ Lock demasiado corto: 12h (mínimo 24h), 🚨 Lock EXPIRADO |
| **DexScreener** | 📊 Vol 24h: $50K, 🔄 Buys/Sells: 120/89, ⏳ Edad: 2.5h |
| **DexScreener Price** | 📈 5m: +5%, 1h: +12%, 6h: +25%, 24h: +40% |
| **DexScreener Dumps** | 📉 Dump severe 24h: -60%, 📉 6h: -45%, 📉 1h: -30% |

### `_calculate_risk(info)`

Clasifica el token basado en flags activos:

```
DANGER si:
  - is_honeypot
  - tokensniffer_is_scam
  - can_self_destruct
  - ≥3 warning flags combinadas (blacklist, mintable, proxy, hidden owner, etc.)

WARNING si:
  - ≥1 warning flag
  - API coverage insuficiente (<2 APIs OK)

SAFE si:
  - ≥2 APIs respondieron
  - GoPlus O TokenSniffer confirmaron OK
  - Sin flags peligrosas
  - Bonus: listado en CoinGecko → razón "✅ Listado en CoinGecko"
```

---

## Enrichment Dual (Fast/Slow)

Los tokens nuevos a menudo tienen datos incompletos (DexScreener aún no indexó, APIs con timeout). El bot implementa dos ciclos de re-enriquecimiento:

### Fast Cycle (~3s) — `_enrich_detected_tokens(fast_only=True)`

- **Frecuencia:** Cada ~3 segundos (junto con P&L update)
- **Alcance:** Solo tokens con al menos 1 API fallida (`_goplus_ok`, `_honeypot_ok`, `_coingecko_ok`, `_tokensniffer_ok` = False)
- **Acción:** Re-intenta SOLO las APIs que fallaron + siempre re-fetch DexScreener
- **Propósito:** Completar datos de tokens recién detectados rápidamente

### Slow Cycle (~15s) — `_enrich_detected_tokens(fast_only=False)`

- **Frecuencia:** Cada ~15 segundos
- **Alcance:** TODOS los tokens detectados (últimos 50)
- **Acción:** Re-fetch DexScreener completo para todos
- **Propósito:** DexScreener tarda en indexar tokens nuevos; este ciclo captura datos que no existían antes

### Después de cada enrichment:

1. `_rebuild_flag_reasons()` — Recalcula razones con datos frescos
2. `_calculate_risk()` — Reclasifica riesgo
3. Emite `token_updated` — Frontend actualiza tabla + feed card en tiempo real
4. Si liquidez recién apareció → ejecuta Buy Gating → posible `snipe_opportunity`

---

## Buy Gating — Filtros de Compra

Antes de emitir `snipe_opportunity`, el bot ejecuta TODOS estos filtros. Si cualquiera falla, el token se rechaza:

### 1. Filtros de Seguridad Básicos

| Filtro | Condición de rechazo |
|---|---|
| Risk level | `only_safe=True` y `risk == "danger"` |
| Buy tax | `buy_tax > max_buy_tax` (default 10%) |
| Sell tax | `sell_tax > max_sell_tax` (default 15%) |
| Honeypot | `is_honeypot == True` |

### 2. LP Lock Validation (Anti Rug-Pull)

| Condición | Resultado | Log |
|---|---|---|
| LP no bloqueada | ❌ Rechazado | "LP not locked" |
| LP bloqueada < 80% | ❌ Rechazado | "LP lock only X% (need >=80%)" |
| LP lock < 24h restantes | ❌ Rechazado | "LP lock only Xh remaining (need >24h)" |
| LP lock expirado | ❌ Rechazado | (hrs_remaining < 0) |
| LP bloqueada ≥80% Y ≥24h | ✅ Aprobado | — |

### 3. Price History Check (Smart Entry Gate)

| Condición | Resultado | Razón |
|---|---|---|
| 24h ≤ -50% | ❌ Rechazado | Dump masivo — probable rug |
| 6h ≤ -40% | ❌ Rechazado | Dump reciente |
| 1h ≤ -25% | ❌ Rechazado | Caída activa |
| 5m ≥ +30% | ❌ Rechazado | Ya bombeado — mala entrada |
| 1h ≥ +50% | ❌ Rechazado | Ya bombeado — mala entrada |

### 4. Liquidez Mínima

- `liquidity_usd >= min_liquidity_usd` (default $5,000)

> **Nota:** Estos filtros se aplican tanto en `_process_pair()` (detección) como en `_enrich_detected_tokens()` (cuando liquidez aparece después de enriquecimiento).

---

## Validación de LP Lock (Anti Rug-Pull)

### Cómo se detecta el LP Lock

GoPlus devuelve `lp_holders[]` con información de cada holder de LP tokens:

```python
for holder in lp_holders:
    if holder.get("is_locked") == "1":
        locked_detail = holder.get("locked_detail", [])
        for lock in locked_detail:
            end_time = float(lock.get("end_time", "0"))
            if end_time > best_lock_end:
                best_lock_end = end_time
        total_lp_locked_pct += float(holder.get("percent", "0")) * 100
```

**Fuentes de lock reconocidas:**

| Plataforma | Detección |
|---|---|
| **PinkLock** | "Pink" en el tag del holder |
| **Unicrypt** | "Unicrypt" en el tag del holder |
| **Team.Finance** | "Team" en el tag del holder |

### Reglas de validación (3 niveles)

1. **`lp_locked`** — ¿Hay LP tokens bloqueados? (bool)
2. **`lp_lock_percent >= 80`** — ¿Se bloqueó suficiente liquidez? Si solo 10% está bloqueado, el owner puede rug con el 90% restante
3. **`lp_lock_hours_remaining >= 24`** — ¿El lock dura suficiente? Un lock de 24h es una trampa — el owner espera y retira

### Auto Hold Hours

Cuando un token pasa todos los filtros, el bot calcula automáticamente el tiempo máximo de hold:

```python
auto_hold_h = 0
if token_info.lp_lock_hours_remaining > 1:
    auto_hold_h = round(token_info.lp_lock_hours_remaining - 1, 1)
```

El bot vende **1 hora antes** de que expire el lock, protegiéndote de un rug pull al vencimiento.

---

## Smart Entry Gate (Anti-Pump)

El bot protege contra comprar tokens que ya subieron demasiado (mala entrada = pérdida asegurada):

### Rechazo por pump reciente

| Periodo | Umbral | Lógica |
|---|---|---|
| 5 minutos | ≥ +30% | Token en pump activo → mala entrada |
| 1 hora | ≥ +50% | Ya subió mucho → tarde para entrar |

### Rechazo por dump (rug pull signs)

| Periodo | Umbral | Lógica |
|---|---|---|
| 24 horas | ≤ -50% | Dump masivo → probable rug |
| 6 horas | ≤ -40% | Dump reciente → proyecto abandonado |
| 1 hora | ≤ -25% | Caída activa → no entrar |

La idea es **comprar bajo** (cuando el precio está estable o con crecimiento moderado) para maximizar ganancia.

---

## Auto-Buy & Auto-Sell

### Auto-Buy

1. Token pasa TODOS los filtros de Buy Gating
2. Bot emite `snipe_opportunity` al frontend via WebSocket
3. Si `auto_buy: true`, el frontend ejecuta el swap automáticamente via `ethers.js`
4. El frontend llama `register_snipe` al backend con datos de la transacción
5. Backend crea `ActiveSnipe` y comienza P&L monitoring

### P&L Monitoring (`_update_active_snipes`)

- **Frecuencia:** Cada ~3s (en el fast tick del main loop)
- **Para cada posición activa:**
  1. Obtiene precio actual via `_get_token_price_usd()` (on-chain reserves)
  2. Calcula PnL: `((current - buy) / buy) * 100`
  3. Emite `snipe_update` al frontend
  4. Verifica alertas:

### Auto-Sell Triggers

| Trigger | Condición | Evento |
|---|---|---|
| **Take Profit** | `pnl >= take_profit` (40%) | `take_profit_alert` |
| **Stop Loss** | `pnl <= -stop_loss` (20%) | `stop_loss_alert` |
| **Time Limit** | `held_hours >= max_hold_hours` | `time_limit_alert` |

Cuando se dispara un trigger:
1. `snipe.status` cambia a `"selling"` (previene triggers duplicados)
2. Frontend recibe el evento y ejecuta el swap de venta via ethers.js
3. Frontend llama `mark_sold` al backend

---

## WebSocket Events

### Bot → Frontend

| Evento | Cuándo | Datos clave |
|---|---|---|
| `bot_status` | Start/Stop | `status`, `chain_id` |
| `scan_info` | Info general | `chain_name`, `rpc_http`, `factory`, mensaje |
| `scan_block` | Cada ciclo | `from_block`, `to_block`, `behind` |
| `token_detected` | Nuevo token | TokenInfo completa + liquidez |
| `token_updated` | Re-enrichment | TokenInfo actualizada |
| `contract_analysis` | Token líquido | Risk analysis detallado |
| `snipe_opportunity` | Pasa filtros | Token + pair + LP lock + auto_hold |
| `snipe_update` | ~3s (activos) | PnL, precio actual, held_seconds |
| `take_profit_alert` | PnL ≥ TP | Token + pnl% |
| `stop_loss_alert` | PnL ≤ -SL | Token + pnl% |
| `time_limit_alert` | Tiempo expirado | Token + held_hours |
| `error` | Error en loop | message |

### Frontend → Bot (via sniperConsumer)

| Acción | Qué hace |
|---|---|
| `start` | Inicia el bot (`SniperBot.run()`) |
| `stop` | Para el bot |
| `update_settings` | Actualiza settings en caliente |
| `register_snipe` | Registra posición comprada |
| `mark_sold` | Marca posición como vendida |
| `get_state` | Obtiene estado completo |

---

## Main Loop (`run()`)

```python
while self.running:
    # ── Block Scanning ──
    current_block = w3.eth.block_number
    if current_block > last_block:
        logs = eth_getLogs(PairCreated, from_block, to_block)
        for log in logs:
            await _process_pair(log)  # analyze + buy gating

    # ── Fast Tick (~3s): Enrichment + P&L ──
    enrich_tick += 1
    if enrich_tick >= 2:  # ~3s (poll_interval * 2)
        await _enrich_detected_tokens(fast_only=True)
        await _update_active_snipes()     # P&L + TP/SL check
        enrich_tick = 0

    # ── Slow Tick (~15s): Full DexScreener refresh ──
    slow_tick += 1
    if slow_tick >= 10:  # ~15s
        await _enrich_detected_tokens(fast_only=False)
        slow_tick = 0

    # ── Price Tick (~30s): Native price update ──
    price_tick += 1
    if price_tick >= 20:  # ~30s
        await _fetch_native_price()
        price_tick = 0

    await asyncio.sleep(poll_interval)
```

### Background Tasks

- **Sync WS Listener** — WebSocket permanente a BSC node, escucha eventos `Sync` de los pares LP rastreados para detectar cambios de precio en tiempo real sin polling adicional.

---

## Settings Configurables

Configurables desde la UI del frontend y actualizables en caliente (sin reiniciar el bot):

| Setting | Default | Tipo | Descripción |
|---|---|---|---|
| `min_liquidity_usd` | 5,000 | float | Liquidez mínima USD para considerar token |
| `max_buy_tax` | 10 | float | Tax máximo al comprar (%) |
| `max_sell_tax` | 15 | float | Tax máximo al vender (%) |
| `buy_amount_native` | 0.05 | float | BNB/ETH a invertir por snipe |
| `take_profit` | 40 | float | % ganancia para auto-sell |
| `stop_loss` | 20 | float | % pérdida para auto-sell |
| `auto_buy` | false | bool | Ejecutar compras automáticamente |
| `only_safe` | true | bool | Solo comprar tokens SAFE |
| `slippage` | 12 | float | Slippage permitido (%) |
| `max_hold_hours` | 0 | float | Tiempo máximo manual (0 = auto desde LP lock) |
| `max_concurrent` | 5 | int | Análisis paralelos de tokens |
| `block_range` | 5 | int | Bloques por ciclo de escaneo |
| `poll_interval` | 1.5 | float | Segundos entre escaneos |

---

## RPC & Blockchain

### Endpoints con rotación automática

```python
RPC_FALLBACKS = {
    56: [  # BSC
        "https://bsc.drpc.org",
        "https://bsc-rpc.publicnode.com",
        "https://binance.llamarpc.com",
        "https://bsc-pokt.nodies.app",
        "https://bsc.meowrpc.com",
    ],
    1: [  # Ethereum
        "https://eth.drpc.org",
        "https://ethereum-rpc.publicnode.com",
        "https://eth.llamarpc.com",
        "https://eth.meowrpc.com",
    ],
}
```

Cuando un RPC falla (`eth_getLogs` rate limit, timeout), el bot rota automáticamente al siguiente endpoint sin perder bloques.

### WebSocket RPCs (Sync Listener)

| Chain | WS Endpoint |
|---|---|
| BSC | `wss://bsc-ws-node.nariox.org:443` |
| ETH | `wss://ethereum-rpc.publicnode.com` |

Fallbacks WS: `wss://bsc.drpc.org`, `wss://eth.drpc.org`

### Contratos

| Chain | Factory | Router | WETH/WBNB |
|---|---|---|---|
| BSC (56) | PancakeSwap V2 Factory | PancakeSwap V2 Router | WBNB |
| ETH (1) | Uniswap V2 Factory | Uniswap V2 Router | WETH |

### ABIs incluidos

- **ERC20_ABI** — `name`, `symbol`, `decimals`, `totalSupply`, `balanceOf`, `owner`, `transfer`
- **PAIR_ABI** — `getReserves`, `token0`, `token1`
- **FACTORY_ABI** — `getPair`, `PairCreated` event
- **ROUTER_ABI** — `swapExactETHForTokensSupportingFeeOnTransferTokens`, `swapExactTokensForETHSupportingFeeOnTransferTokens`, `getAmountsOut`

---

## Frontend Integration

### Archivos

| Archivo | Líneas | Función |
|---|---|---|
| `trading/static/js/pageSniper.js` | ~1627 | Lógica completa del frontend |
| `trading/templates/sniper.html` | ~384 | Template HTML con controles |
| `trading/WebSocket/sniperConsumer.py` | ~161 | Bridge WebSocket Django ↔ Bot |

### Funciones clave del frontend (`pageSniper.js`)

| Función | Qué hace |
|---|---|
| `buildTokenLogCard()` | Construye card de feed con `data-token` para updates en vivo |
| `executeSnipeBuy()` | Ejecuta swap BNB→Token via ethers.js TxModule |
| `executeSnipeSell()` | Ejecuta swap Token→BNB via ethers.js TxModule |
| `token_updated` handler | Reemplaza feed cards en vivo (`.replaceWith()`) |
| `snipe_opportunity` handler | Muestra oportunidad, ejecuta auto-buy si habilitado |
| `stop_loss_alert` handler | Dispara auto-sell al alcanzar stop loss |
| `take_profit_alert` handler | Dispara auto-sell al alcanzar take profit |
| `time_limit_alert` handler | Dispara auto-sell al expirar tiempo |

### Consumer WebSocket (`sniperConsumer.py`)

- Hereda de `AsyncJsonWebsocketConsumer` (Django Channels)
- Recibe acciones: `start`, `stop`, `update_settings`, `register_snipe`, `mark_sold`, `get_state`
- Pasa `auto_hold_hours` del LP lock al registrar posición
- Mantiene referencia a `SniperBot` instance por usuario/sesión

---

## Resumen de Seguridad Anti Rug-Pull

El bot tiene **múltiples capas** de protección:

```
Capa 1: 5 APIs de seguridad → detecta flags peligrosos
Capa 2: LP Lock ≥80% obligatorio → owner no controla liquidez  
Capa 3: LP Lock ≥24h obligatorio → lock no expira pronto
Capa 4: Smart Entry → no compra tokens ya bombeados
Capa 5: Price dump check → no compra tokens en caída libre
Capa 6: Stop Loss 20% → venta automática si cae
Capa 7: Max Hold Hours → vende 1h antes de que expire el lock
Capa 8: Sync WS Listener → detecta cambios de precio en real-time
```

---

> **Disclaimer:** Este sistema reduce significativamente el riesgo pero NO lo elimina al 100%. El trading de tokens nuevos es inherentemente peligroso. Usa cantidades que puedas permitirte perder.

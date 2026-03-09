# Sniper Bot — Documentación Técnica Completa

> Motor de detección y trading automático de tokens nuevos en BSC/ETH.  
> Archivo principal: `trading/Services/sniperService.py` (~2300 líneas)  
> Módulos profesionales v2: 6 servicios adicionales (~2200 líneas)

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
| `can_take_back_ownership` | bool | Owner puede reclamar ownership |
| `is_airdrop_scam` | bool | Token de airdrop scam |
| `is_true_token` | bool | ERC-20 real (no fake interface) |
| `top_holder_percent` | float | % supply del top holder (no LP/dead) |
| `creator_percent` | float | % supply retenido por el creator |
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
- **Qué detecta:** 18+ flags de seguridad + LP lock + holder concentration
- **LP Lock Detection:**
  - Itera `lp_holders[].is_locked`, acumula `total_lp_locked_pct`
  - Busca `locked_detail[].end_time` para encontrar el lock más largo (`best_lock_end`)
  - Identifica fuente: **PinkLock**, **Unicrypt**, **Team.Finance** (por nombre del holder)
  - Calcula `lp_lock_hours_remaining` desde `best_lock_end`
- **Holder Concentration:**
  - Itera `holders[]`, excluye LP holders, dead addresses y locked wallets
  - Calcula `top_holder_percent` — el % del supply del mayor holder individual
  - Calcula `creator_percent` — cuánto retiene el deployer del contrato
- **Nuevas flags (hardened):** `can_take_back_ownership`, `is_airdrop_scam`, `is_true_token`

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
| **Hardened Flags** | 🚨 Can reclaim ownership, 🚨 Airdrop scam, 🚨 Fake ERC-20, 🐋 Top holder 35%, 👨‍💻 Creator 25% |
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
  - tokensniffer_is_scam / score < 20
  - can_self_destruct
  - owner_can_change_balance / cannot_sell_all
  - ── Hardened (bloqueo inmediato) ──
  - is_proxy (puede cambiar TODA la lógica)
  - !is_open_source (código no verificado = trampa potencial)
  - has_hidden_owner (control invisible)
  - can_take_back_ownership (fake renounce)
  - is_airdrop_scam
  - !is_true_token (fake ERC-20)
  - top_holder_percent >= 30% (1 whale controla el supply)
  - creator_percent >= 20% (deployer retuvo demasiado)
  - ≥3 warning flags combinadas

WARNING si:
  - ≥1 warning flag (mintable, blacklist, pause, external_call, etc.)
  - top_holder >= 15%, creator >= 10%
  - holder_count < 10
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

### 2. Verificación del Contrato (Hardened)

Estas verificaciones requieren que GoPlus haya respondido (`_goplus_ok = True`):

| Condición | Resultado | Razón |
|---|---|---|
| Código no verificado | ❌ Rechazado | Owner puede ocultar funciones maliciosas |
| Contrato proxy | ❌ Rechazado | Puede cambiar TODA la lógica post-compra |
| Owner oculto | ❌ Rechazado | Control invisible del contrato |
| Puede reclamar ownership | ❌ Rechazado | Finge renunciar pero puede reclamar |
| Airdrop scam | ❌ Rechazado | Token de estafa de airdrop |
| Token falso (no ERC-20) | ❌ Rechazado | Interface fake |
| Top holder ≥ 30% supply | ❌ Rechazado | 1 whale puede dumpear todo |
| Creator retiene ≥ 20% | ❌ Rechazado | Deployer puede dumpear |

### 3. LP Lock Validation (Anti Rug-Pull)

| Condición | Resultado | Log |
|---|---|---|
| LP no bloqueada | ❌ Rechazado | "LP not locked" |
| LP bloqueada < 80% | ❌ Rechazado | "LP lock only X% (need >=80%)" |
| LP lock < 24h restantes | ❌ Rechazado | "LP lock only Xh remaining (need >24h)" |
| LP lock expirado | ❌ Rechazado | (hrs_remaining < 0) |
| LP bloqueada ≥80% Y ≥24h | ✅ Aprobado | — |

### 4. Price History Check (Smart Entry Gate)

| Condición | Resultado | Razón |
|---|---|---|
| 24h ≤ -50% | ❌ Rechazado | Dump masivo — probable rug |
| 6h ≤ -40% | ❌ Rechazado | Dump reciente |
| 1h ≤ -25% | ❌ Rechazado | Caída activa |
| 5m ≥ +30% | ❌ Rechazado | Ya bombeado — mala entrada |
| 1h ≥ +50% | ❌ Rechazado | Ya bombeado — mala entrada |

### 5. Liquidez Mínima

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
Capa 1: 5 APIs de seguridad → detecta 18+ flags peligrosos
Capa 2: Código verificado obligatorio → no compra contratos ocultos
Capa 3: Anti-proxy → bloquea contratos que pueden cambiar lógica
Capa 4: Anti-hidden-owner → bloquea control invisible
Capa 5: Anti-fake-renounce → detecta can_take_back_ownership
Capa 6: Holder concentration → top holder < 30%, creator < 20%
Capa 7: LP Lock ≥80% obligatorio → owner no controla liquidez
Capa 8: LP Lock ≥24h obligatorio → lock no expira pronto
Capa 9: Smart Entry → no compra tokens ya bombeados
Capa 10: Price dump check → no compra tokens en caída libre
Capa 11: Stop Loss 20% → venta automática si cae
Capa 12: Max Hold Hours → vende 1h antes de que expire el lock
Capa 13: Sync WS Listener → detecta cambios de precio en real-time
Capa 14: Pump Score 0-100 → rechaza tokens con grade AVOID (<40)
Capa 15: Swap Simulation → verifica on-chain buy+sell con eth_call
Capa 16: Bytecode Analysis → detecta SELFDESTRUCT/DELEGATECALL
Capa 17: Rug Detector → monitoreo post-compra (LP drain, dev sell)
Capa 18: Smart Money → señal positiva si ballenas compran
```

---

## Módulos Profesionales v2

Seis servicios adicionales que elevan la calidad del bot:

### Pump Analyzer (`pumpAnalyzer.py`)

Motor de scoring 0-100 que evalúa el potencial de pump de un token:

| Componente | Peso | Descripción |
|---|---|---|
| Liquidity | 20% | Sweet spot $8k–$120k |
| Holder | 15% | Distribución saludable de holders |
| Activity | 20% | Ratio buy/sell, volumen 24h |
| Whale | 15% | Acumulación de ballenas |
| Momentum | 15% | Patrón de precio gradual |
| Age | 10% | Tokens frescos (1-24h ideal) |
| Social | 5% | Web, redes, CoinGecko |

**Grades:** HIGH (80-100) / MEDIUM (60-79) / LOW (40-59) / AVOID (0-39)

No hace llamadas API adicionales — opera 100% sobre datos existentes de TokenInfo.

### Swap Simulator (`swapSimulator.py`)

Simulación on-chain de compra y venta vía `eth_call`:

1. **Buy simulation:** `swapExactETHForTokensSupportingFeeOnTransferTokens` → calcula buy tax real
2. **Sell simulation:** `approve` + `swapExactTokensForETHSupportingFeeOnTransferTokens` → calcula sell tax
3. Si sell revierte → **honeypot confirmado por simulación**

**BytecodeAnalyzer** (incluido): Detecta opcodes peligrosos:
- `SELFDESTRUCT (0xFF)` — contrato puede autodestruirse
- `DELEGATECALL (0xF4)` — lógica puede ser reemplazada
- EIP-1167 minimal proxy — lógica vive en otro contrato
- Bytecode tamaño anómalo (<100 o >25000 bytes)

### Mempool Service (`mempoolService.py`)

Escucha transacciones pendientes para detectar tokens 10-30s antes de confirmación:

- **Métodos monitoreados:** addLiquidityETH, addLiquidity, removeLiquidity, createPair, approve
- Suscripción: `alchemy_pendingTransactions` → fallback `newPendingTransactions`
- Trackea ~5000 tx hashes para evitar duplicados
- Emite `mempool_event` al frontend en real-time

### Rug Detector (`rugDetector.py`)

Monitoreo post-compra de posiciones activas:

- **25+ method selectors monitoreados:** removeLiquidity, setFee, blacklist, pause, transferOwnership, mint, etc.
- Chequea cada ~3s: Transfer events, LP reserves, balance del creator
- **EMERGENCY (severity 8-10):** liquidity drain → auto-sell inmediato
- **WARNING (severity 5-7):** tax increase, dev dump
- **INFO (severity 1-4):** patrones inusuales

### Pre-Launch Detector (`preLaunchDetector.py`)

Detecta tokens ANTES de listing en DEX:

1. Escanea bloques para contract creation txs (`to=None`)
2. Verifica bytecode por selectores ERC-20 (≥5/9 matches)
3. Monitorea aprobación de router (señal más fuerte de launch)
4. Launch probability 0-100: base 20 + erc20(10) + supply(15) + router(35) + named(10) + launchpad(20)
5. Auto-limpieza: elimina watchlist entries después de 2h sin launch

### Smart Money Tracker (`smartMoneyTracker.py`)

Rastreo de wallets rentables:

- **Análisis automático:** Para tokens pumpeados, identifica compradores tempranos via Transfer logs
- **Base de datos rolling:** Mantiene wallets con historial de trades
- **Smart score:** win_rate(60%) + trade_count(20%) + recency(10%) + known_bonus(10%)
- Emite señal `smart_money_signal` cuando ballenas trackadas compran un token nuevo

### Settings v2

```python
"enable_mempool": False,      # WSS mempool (requiere nodo compatible)
"enable_pump_score": True,    # Pump scoring 0-100
"enable_swap_sim": True,      # Swap simulation vía eth_call
"enable_bytecode": True,      # Bytecode analysis
"enable_rug_detector": True,  # Post-buy rug monitoring
"enable_prelaunch": False,    # Pre-launch detection (experimental)
"enable_smart_money": False,  # Smart money tracking (experimental)
"min_pump_score": 40,         # Mínimo pump score para comprar
```

---

## Professional v3 — Módulos Avanzados

### Arquitectura v3

```
┌─────────────────────────────────────────────────────────────────────┐
│                     SniperBot v3 Pipeline                            │
│                                                                      │
│  v2 Analysis (parallel)            v3 Analysis (sequential)          │
│  ┌──────────────┐                  ┌──────────────────────────────┐  │
│  │ PumpAnalyzer  │─── pump_result  │ PumpAnalyzer v3 Extras       │  │
│  │ SwapSimulator │─── sim_result   │ (mcap, holder_growth, LP)    │  │
│  │ Bytecode      │─── bytecode     │                              │  │
│  │ SmartMoney    │─── smart_money  └──────────────────────────────┘  │
│  └──────────────┘                                                    │
│          ↓                         ┌──────────────────────────────┐  │
│    all results                     │ DevTracker                   │  │
│          ↓                         │ (creator reputation scoring) │  │
│  ┌──────────────────────┐          └──────────┬───────────────────┘  │
│  │ RiskEngine            │←── dev_result ──────┘                     │
│  │ (7 component scoring) │←── pump, sim, bytecode, smart_money      │
│  │ → final_score 0-100   │                                           │
│  │ → action: BUY/WATCH/… │                                           │
│  │ → hard_stop: T/F      │                                           │
│  └──────────┬────────────┘                                           │
│             ↓                                                        │
│  ┌──────────────────────┐                                            │
│  │ TradeExecutor         │← if action=BUY & executor enabled         │
│  │ (backend sign+send)   │                                           │
│  │ multi-RPC broadcast   │                                           │
│  └───────────────────────┘                                           │
└──────────────────────────────────────────────────────────────────────┘
```

### PumpAnalyzer v3 (`pumpAnalyzer.py` — Enhanced)

**10 componentes de scoring** (antes 7):

| Componente | Peso | Descripción |
|---|---|---|
| liquidity | 14 | Score 0-100 basado en liquidez USD |
| holder | 10 | Distribución de holders + flags |
| activity | 15 | Volumen, buys/sells, concentración |
| whale | 10 | Detección de ballenas >5% supply |
| momentum | 12 | Precio 5m/1h/6h/24h + buy ratios |
| age | 7 | Token age sweet spot (1-24h ideal) |
| social | 4 | CoinGecko listing + social links |
| **mcap** | **12** | **Market cap sweet spot ($20k-$400k)** |
| **holder_growth** | **10** | **Crecimiento de holders/minuto** |
| **lp_growth** | **6** | **Cambio en liquidez vs. inicial** |

**Nuevas capacidades:**
- **Time-series tracking:** `_TokenSnapshot` con timestamp + holder_count + liquidity_usd
- **Market cap estimation:** mcap ≈ pair_liquidity_usd × 1.2 (para tokens sin CoinGecko)
- **Holder growth rate:** Mide holders/min desde snapshots históricos
- **LP growth monitoring:** Compara liquidez actual vs. primera lectura
- **Stats persistentes:** `get_stats()` retorna tokens rastreados, snapshots totales
- **Cleanup automático:** `cleanup_old()` elimina data >2h

### DevTracker (`devTracker.py` — ~390 líneas)

**Rastrea deployers y su historial de éxito/fracaso.**

**Dataclasses:**
- `DevLaunch`: token, symbol, launch_time, initial/peak liquidity, was_rug_pull, lp_locked, outcome
- `DevProfile`: wallet, label, launches, reputation_score 0-100, 5 component scores
- `DevCheckResult`: creator, is_tracked, dev_score, is_serial_scammer, signals

**Sistema de Reputación (5 componentes):**

| Componente | Peso | Criterio |
|---|---|---|
| launch_history | 40% | Éxitos vs rugs (≥3 rugs = serial scammer) |
| liquidity_behavior | 20% | Peak LP vs initial LP multiplicador |
| community_trust | 15% | Known dev bonus + label |
| contract_quality | 15% | % tokens con LP locked |
| recency | 10% | Actividad reciente (decay exponencial) |

**Features:**
- `check_creator(token, info)` → busca deployer via `eth_getTransactionCount` + creation tx
- `record_launch()` → registra nuevo lanzamiento para futuro tracking
- `record_outcome()` → actualiza resultado (rug/success) post-facto
- Serial scammer detection: ≥3 rug pulls = bloqueo automático (HARD STOP)
- Known devs: Base de datos de wallets conocidos con labels

### RiskEngine (`riskEngine.py` — ~380 líneas)

**Motor unificado de decisión que combina TODOS los módulos.**

**7 componentes ponderados:**

| Componente | Peso | Fuente |
|---|---|---|
| security | 25% | GoPlus flags, taxes, LP lock |
| pump | 20% | PumpAnalyzer total_score |
| dev | 15% | DevTracker reputation |
| smart_money | 15% | Smart money buyer count |
| simulation | 12% | SwapSimulator honeypot/taxes |
| bytecode | 8% | Bytecode opcode flags |
| market | 5% | Liquidez USD nivel |

**Acciones resultantes:**

| Action | Score | Significado |
|---|---|---|
| STRONG_BUY | ≥80 | Token excelente, ejecutar inmediatamente |
| BUY | ≥65 | Buen token, comprar con confianza |
| WATCH | ≥50 | Monitorear, esperar confirmación |
| WEAK | ≥35 | Riesgoso, no recomendado |
| IGNORE | <35 | Evitar completamente |

**Hard Stops (override cualquier score):**
- Honeypot confirmado (GoPlus o Simulation)
- SELFDESTRUCT opcode encontrado
- Serial scammer (≥3 rugs anteriores)
- TokenSniffer SCAM flag
- Simulation honeypot positivo

**Confidence Boosting:** Más módulos con data = mayor confianza (base 50% + 10% por módulo)

### TradeExecutor (`tradeExecutor.py` — ~470 líneas)

**Ejecución backend de trades con private key (no requiere wallet frontend).**

**Dataclasses:**
- `TradeResult`: success, tx_hash, block_number, gas_used, execution_time_ms, error
- `PreBuiltTx`: tx template pre-firmada con expiración 15s

**Capacidades:**
- `execute_buy(token, amount_native, slippage)` → swap native→token
- `execute_sell(token, slippage, percent)` → swap token→native (con approve)
- `_ensure_approval()` → aprueba router si allowance insuficiente
- `_send_multi_rpc(signed_tx)` → broadcast a TODOS los RPCs en paralelo (velocidad máxima)
- `_wait_confirmation(tx_hash, timeout=60)` → espera bloque de confirmación
- `pre_build_buy_tx()` → pre-construye transacción para ejecución sub-segundo
- Nonce management con lock asyncio
- Gas pricing: Legacy BSC (max 10 gwei) / EIP-1559 ETH (max 200 gwei) con 10% buffer

**Seguridad:**
- Private key desde `os.environ["SNIPER_PRIVATE_KEY"]` (nunca en código)
- No ejecuta sin key configurada
- Habilitado solo con `enable_trade_executor: True` (default: False)

### Settings v3

```python
"enable_dev_tracker": True,         # Dev reputation tracking
"enable_risk_engine": True,         # Unified risk scoring
"enable_trade_executor": False,     # Backend trade execution (requires SNIPER_PRIVATE_KEY)
"risk_engine_min_score": 50,        # Minimum risk engine score to pass
"risk_engine_auto_action": "BUY",   # Minimum action level (STRONG_BUY, BUY, WATCH, WEAK)
```

### WebSocket Events v3

| Evento | Dirección | Descripción |
|---|---|---|
| `token_detected` | server→client | Ahora incluye dev_*, risk_engine_*, pump v3 extras |
| `token_updated` | server→client | Idem con data refrescada |
| `snipe_opportunity` | server→client | Incluye risk_engine_score, dev_score, backend_buy_available |
| `backend_buy_executed` | server→client | Trade executor completó compra exitosa |
| `backend_buy_failed` | server→client | Trade executor falló |
| `scan_info` | server→client | Incluye v3 module startup info |

### Frontend v3 Sections

Cada token log card ahora muestra (si data disponible):

1. **👨‍💻 Dev Tracker** — Score, label, launches, éxitos, rugs, serial scammer warning
2. **🎯 Risk Engine** — Score 0-100, action (STRONG_BUY/BUY/etc), confidence, hard stop, signals
3. **📊 Pump v3 Avanzado** — Market cap, MCap score, holder growth rate, LP growth
4. **⚡ Backend Executor** — Status de disponibilidad

---

> **Disclaimer:** Este sistema reduce significativamente el riesgo pero NO lo elimina al 100%. El trading de tokens nuevos es inherentemente peligroso. Usa cantidades que puedas permitirte perder.

---

## Professional v4 — Monitoreo, Métricas y UX

### Arquitectura v4

```
┌───────────────────────────────────────────────────────────────────┐
│                    SniperBot v4 Monitoring Layer                   │
│                                                                   │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐    │
│  │ Resource     │  │ Alert        │  │ Metrics              │    │
│  │ Monitor      │  │ Service      │  │ Service              │    │
│  │ (CPU/Mem/WS) │  │ (TG/DC/Mail)│  │ (P&L/Speed/Stats)    │    │
│  └──────┬───────┘  └──────┬───────┘  └──────────┬───────────┘    │
│         │                 │                      │                │
│         └─────────────────┴──────────────────────┘                │
│                           │                                       │
│                    ┌──────▼───────┐                                │
│                    │  SniperBot   │                                │
│                    │  get_state() │                                │
│                    │  pipeline    │                                │
│                    └──────┬───────┘                                │
│                           │                                       │
│                    ┌──────▼──────────────┐                         │
│                    │ sniperConsumer.py   │                         │
│                    │ get_dashboard       │                         │
│                    │ update_alert_config │                         │
│                    └──────┬─────────────┘                         │
│                           │                                       │
│                    ┌──────▼──────────────┐                         │
│                    │ Frontend Dashboard  │                         │
│                    │ Chart.js + KPIs     │                         │
│                    │ Profiles + Tutorial │                         │
│                    └────────────────────┘                         │
└───────────────────────────────────────────────────────────────────┘
```

### Resource Monitor (`resourceMonitor.py` — ~248 líneas)

**Monitoreo de recursos del sistema en tiempo real.**

| Métrica | Fuente | Descripción |
|---|---|---|
| `memory_rss_mb` | psutil | Memoria RSS del proceso (MB) |
| `memory_percent` | psutil | Porcentaje de uso de RAM |
| `cpu_percent` | psutil | Porcentaje de uso de CPU |
| `ws_connections` | counter | Conexiones WebSocket activas |
| `ws_messages_sent` | counter | Total de mensajes WS enviados |
| `ws_messages_rate` | time-series | Mensajes/segundo WS |
| `tokens_processed` | counter | Total de tokens procesados |
| `tokens_per_minute` | time-series | Tokens analizados por minuto |
| `avg_analysis_ms` | rolling avg | Tiempo promedio de análisis (ms) |
| `rpc_calls` | counter | Total de llamadas RPC |
| `rpc_errors` | counter | Errores RPC acumulados |
| `rpc_avg_latency_ms` | rolling avg | Latencia promedio RPC (ms) |

**Umbrales de alerta:**

| Recurso | Warning | Critical |
|---|---|---|
| Memory | 512 MB | 1024 MB |
| CPU | 70% | 90% |
| Active Tasks | 200 | — |

**Features:**
- `ResourceSnapshot` dataclass con timestamp + métricas instantáneas
- Histórico rolling de 120 snapshots (2 horas a 1/min)
- Método `get_stats()` retorna diccionario completo con warnings automáticas
- Opción `psutil` (detallado) o fallback (básico) sin dependencia

### Alert Service (`alertService.py` — ~350 líneas)

**Sistema centralizado de alertas multi-canal.**

| Canal | Configuración | Detección |
|---|---|---|
| **Telegram** | Token + Chat ID | Bot API `sendMessage` |
| **Discord** | Webhook URL | POST con content |
| **Email** | SMTP Gmail | `smtplib` con TLS |

**Variables de entorno:**

```bash
# Telegram
SNIPER_TELEGRAM_TOKEN=bot123456:ABCDEF...
SNIPER_TELEGRAM_CHAT_ID=@my_channel

# Discord
SNIPER_DISCORD_WEBHOOK=https://discord.com/api/webhooks/...

# Email
SNIPER_EMAIL_FROM=sniper@gmail.com
SNIPER_EMAIL_TO=alerts@gmail.com
SNIPER_EMAIL_PASSWORD=app_password
```

**Rate Limiting:**
- Cooldown por categoría (5 min por defecto)
- Cap máximo de 100 alertas por hora (configurable)

**Log Rotation:**
- Archivo: `logs/sniper_alerts.log`
- Tamaño máximo: 5 MB por archivo
- Backups: 5 archivos rotados

**Convenience Methods:**

| Método | Nivel | Categoría |
|---|---|---|
| `alert_token_suspicious(addr, reasons)` | WARNING | token_suspicious |
| `alert_rpc_error(rpc_url, error)` | ERROR | rpc_error |
| `alert_trade_executed(symbol, amount, tx)` | INFO | trade_executed |
| `alert_trade_failed(symbol, error)` | ERROR | trade_failed |
| `alert_rug_detected(symbol, alert_type)` | CRITICAL | rug_detected |
| `alert_resource_warning(metric, value)` | WARNING | resource_warning |

### Metrics Service (`metricsService.py` — ~340 líneas)

**Motor de métricas y analytics para el dashboard.**

**Estructuras de datos:**

```python
@dataclass
class TradeRecord:
    symbol: str                 # Token symbol
    token_address: str          # Contract address
    buy_price: float           # Entry price USD
    sell_price: float          # Exit price USD
    pnl_usd: float            # P&L en USD
    pnl_percent: float         # P&L en porcentaje
    hold_seconds: float        # Duración del trade
    timestamp: float           # Hora del trade

@dataclass
class DetectionEvent:
    token_address: str         # Contract address
    detection_ms: float        # Velocidad de detección (ms)
    passed_filters: bool       # ¿Pasó Buy Gating?
    risk_level: str            # safe/warning/danger
    timestamp: float           # Hora de detección
```

**Métricas calculadas:**

| Métrica | Fórmula | Descripción |
|---|---|---|
| `total_trades` | count(trades) | Total de trades cerrados |
| `wins` / `losses` | pnl > 0 / pnl <= 0 | Trades ganadores/perdedores |
| `win_rate` | wins/total × 100 | Porcentaje de victoria |
| `total_pnl` | sum(pnl_usd) | P&L acumulado en USD |
| `avg_pnl_percent` | mean(pnl_percent) | P&L promedio por trade |
| `best_trade` / `worst_trade` | max/min(pnl_percent) | Mejores/peores trades |
| `filter_rate` | !passed/total × 100 | % tokens filtrados (no comprados) |
| `avg_detection_ms` | mean(detection_ms) | Velocidad promedio de análisis |
| `p95_detection_ms` | percentile(95) | Velocidad percentil 95 |

**Time-Series (hourly buckets):**
- `pnl_hourly` — P&L acumulado por hora
- `detections_hourly` — Detecciones por hora
- Últimas 24 horas (24 buckets)

**Dashboard Payload (`get_dashboard()`):**

```json
{
    "trade_stats": { "total_trades": 15, "wins": 10, "win_rate": 66.7, "total_pnl": 125.50 },
    "detection_stats": { "total_detections": 450, "filter_rate": 92.3, "avg_detection_ms": 850 },
    "effectiveness": { "safe_token_pct": 45.0, "avg_gain_pct": 32.5, "avg_loss_pct": -12.3 },
    "hourly_series": { "labels": ["10:00","11:00",...], "pnl": [10.5, -3.2,...], "detections": [45, 52,...] },
    "resource_stats": { "memory_rss_mb": 245, "cpu_percent": 35, ... },
    "alert_stats": { "total_alerts": 28, "last_24h": 12, ... }
}
```

### Test Suite (130 tests — 8 archivos)

Todos los módulos v4 (y anteriores) tienen cobertura de tests automatizados:

| Archivo | Tests | Cobertura |
|---|---|---|
| `test_resourceMonitor.py` | 16 | Token processing, WS tracking, RPC tracking, snapshots, history, stats |
| `test_alertService.py` | 27 | AlertEvent, LevelOrder, rate limiting, send, convenience methods |
| `test_riskEngine.py` | 14 | Init, weights, hard stops, safe token scoring, thresholds |
| `test_pumpAnalyzer.py` | 15 | Scoring, mock attributes, get_stats, cleanup |
| `test_devTracker.py` | 16 | Creator tracking, reputation, serial scammer, known devs |
| `test_rugDetector.py` | 6 | Alert levels, method monitoring, emergency triggers |
| `test_sniperService.py` | 36 | Bot init, settings, state, dataclasses, enums, v2+v3 fields |

**Ejecutar tests:**

```bash
python manage.py test trading.tests -v 2
```

### WebSocket Events v4

| Evento | Dirección | Descripción |
|---|---|---|
| `dashboard` | server→client | Dashboard completo (auto-refresh 30s) |
| `dashboard_response` | server→client | Respuesta a `get_dashboard` action |
| `alert_config_updated` | server→client | Confirmación de config de alertas |

### Frontend → Bot (nuevas acciones)

| Acción | Qué hace |
|---|---|
| `get_dashboard` | Retorna payload completo del dashboard |
| `update_alert_config` | Actualiza Telegram/Discord/Email toggles |
| `mark_snipe_sold` | Ahora acepta `sell_price` para métricas |

### Frontend v4

#### Performance Dashboard

- **6 KPI Cards:** Total trades, Win rate, P&L acumulado, Detecciones, Filter rate, Avg speed
- **2 Charts (Chart.js 4.4.0):** P&L por hora (bar), Detecciones por hora (line)
- **Effectiveness Grid:** % safe tokens, avg gain/loss, tx failure rate, avg hold time, best trade
- **Resource Monitor Bar:** Memory, CPU, Tokens processed, WS connections, RPC calls

#### User Profiles

| Perfil | Min Liquidez | Only Safe | Auto Buy | Risk |
|---|---|---|---|---|
| **Novato** | $10,000 | ✅ | ❌ | Conservador |
| **Intermedio** | $5,000 | ✅ | ❌ | Balanced |
| **Avanzado** | $2,000 | ❌ | ✅ | Agresivo |

Los perfiles pre-configuran TODOS los settings del bot con un click.

#### Tutorial Overlay

Walkthrough de 6 pasos al primer uso:
1. 🔗 Conectar wallet (Trust Wallet / MetaMask)
2. ⚙️ Configurar settings de trading
3. 🚀 Iniciar el bot
4. 📊 Revisar tokens detectados
5. 🎯 Ejecutar trades
6. 📈 Consultar dashboard de rendimiento

Se guarda en `localStorage` con opción "No mostrar de nuevo".

#### Tooltips

Todos los inputs de configuración tienen tooltips informativos (ⓘ) con explicaciones en español de cada parámetro.

### Settings v4

Las alertas se configuran via variables de entorno (ver Alert Service arriba). Los toggles del frontend envían `update_alert_config` via WebSocket para activar/desactivar canales en caliente.

### Integración en Pipeline

v4 se integra de forma no-invasiva en el pipeline existente:

1. **`_process_pair()`:** Registra tiempo de análisis → `resource_monitor.record_token_processed()`, crea `DetectionEvent` → `metrics_service.record_detection()`, alerta si token sospechoso
2. **RPC errors:** `resource_monitor.record_rpc_call(success=False)` + `alert_service.alert_rpc_error()`
3. **`mark_snipe_sold()`:** `metrics_service.record_trade_from_snipe()` con buy/sell prices
4. **`get_state()`:** Incluye `resource_stats`, `alert_stats`, `metrics_stats`, `metrics_dashboard`

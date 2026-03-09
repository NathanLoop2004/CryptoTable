# TradingWeb — Plataforma de Trading + Sniper Bot

Plataforma web estilo Binance con autenticación Trust Wallet, trading en tiempo real (Binance WebSocket), y un **Sniper Bot** profesional que detecta nuevos tokens en BSC/ETH, los analiza con **5 APIs de seguridad + 12 módulos profesionales** (pump scoring 10 componentes, swap simulation, bytecode analysis, mempool listening, rug detection, smart money tracking, dev reputation, unified risk engine, backend trade executor, resource monitoring, alerts multi-canal, performance metrics), ejecuta compras/ventas automáticas con TP/SL, y protege contra rug pulls con **18 capas de seguridad**.

---

## 📋 Requisitos

| Componente | Versión mínima |
|---|---|
| **Python** | 3.12+ (probado con 3.14) |
| **pip** | 23+ |
| **Sistema operativo** | Windows 10/11, Linux, macOS |
| **Navegador** | Chrome, Edge, Firefox (con soporte WebSocket) |

---

## 🚀 Instalación paso a paso

### 1. Clonar el repositorio

```bash
git clone <url-del-repo>
cd TradingWeb
```

### 2. Crear entorno virtual (recomendado)

```bash
# Windows
python -m venv venv
venv\Scripts\activate

# Linux / macOS
python3 -m venv venv
source venv/bin/activate
```

### 3. Instalar dependencias

```bash
pip install -r requirements.txt
```

Esto instalará:

| Paquete | Para qué sirve |
|---|---|
| `Django 6.0.2` | Framework web principal |
| `channels 4.3.2` + `daphne 4.2.1` | WebSocket (ASGI) en tiempo real |
| `web3 7.14.1` | Interacción con blockchain (BSC/ETH) |
| `aiohttp 3.13.3` | Llamadas async a APIs externas (GoPlus, Honeypot.is, DexScreener, etc.) |
| `django-cors-headers 4.9.0` | Permitir peticiones cross-origin |
| `python-dotenv 1.2.1` | Variables de entorno desde `.env` |
| `PyJWT 2.11.0` + `passlib 1.7.4` | Autenticación Trust Wallet |
| `requests 2.32.5` | Peticiones HTTP síncronas |
| `websockets 15.0.1` | Conexión WebSocket a Binance + Sync listener |
| `psycopg2-binary 2.9.11` | PostgreSQL (opcional, usa SQLite por defecto) |
| `pycryptodome 3.23.0` | Criptografía para firmas de wallet |

### 4. Configurar variables de entorno

Crea un archivo `.env` en la raíz del proyecto (opcional, para alertas):

```bash
# Telegram
SNIPER_TELEGRAM_TOKEN=bot123456:ABCDEF...
SNIPER_TELEGRAM_CHAT_ID=@mi_canal

# Discord
SNIPER_DISCORD_WEBHOOK=https://discord.com/api/webhooks/...

# Email (Gmail SMTP)
SNIPER_EMAIL_FROM=sniper@gmail.com
SNIPER_EMAIL_TO=alertas@gmail.com
SNIPER_EMAIL_PASSWORD=app_password

# Backend Trade Executor (opcional, avanzado)
SNIPER_PRIVATE_KEY=0x...
```

### 5. Aplicar migraciones

```bash
python manage.py migrate
```

### 6. Verificar que todo está bien

```bash
python manage.py check
```

Debería mostrar: `System check identified no issues.`

### 7. Ejecutar tests

```bash
python manage.py test trading.tests -v 2
```

130 tests automatizados cubriendo todos los módulos.

---

## ▶️ Iniciar el servidor

> **Importante:** Este proyecto usa **Django Channels** (WebSocket), por lo que necesita un servidor **ASGI** (Daphne), no el `runserver` estándar.

### Opción A — Foreground (ver logs)

```bash
python -m daphne -b 127.0.0.1 -p 8000 TradingWeb.asgi:application
```

### Opción B — Background (Windows PowerShell)

```powershell
Start-Process -FilePath "python" `
  -ArgumentList "-m","daphne","-b","127.0.0.1","-p","8000","TradingWeb.asgi:application" `
  -WorkingDirectory "C:\ruta\a\TradingWeb" `
  -WindowStyle Hidden
```

### Opción C — Background (Linux/macOS)

```bash
nohup python -m daphne -b 127.0.0.1 -p 8000 TradingWeb.asgi:application &
```

Una vez iniciado, abre el navegador en:

```
http://127.0.0.1:8000/
```

---

## 🌐 Páginas disponibles

| URL | Descripción |
|---|---|
| `/` o `/login/` | Login — Conectar Trust Wallet / MetaMask |
| `/dashboard/` | Panel principal de trading (gráficas, orderbook, trades) |
| `/sniper/` | Sniper Bot — detección y auto-trading de tokens nuevos |
| `/transactions/` | Historial de transacciones |
| `/wallet/` | Gestión de wallet (balances, envíos, compra crypto) |

---

## 🎯 Sniper Bot — Resumen

### Pipeline completo (v4)

```
Mempool → Pre-Launch → Block Scanner → PairCreated → ContractAnalyzer (5 APIs)
→ Pump Analyzer (10 comp) → Swap Simulator → Bytecode → Smart Money
→ Dev Tracker → Risk Engine → Buy Gating (18 capas) → snipe_opportunity
→ Auto-Buy (ethers.js) → P&L Monitor → Rug Detector → Auto-Sell (TP/SL/Time)
→ Resource Monitor → Alert Service → Metrics Dashboard
```

### 12 Módulos Profesionales

| Módulo | Archivo | Líneas | Versión | Descripción |
|---|---|---|---|---|
| 🚀 Pump Analyzer | `pumpAnalyzer.py` | 597 | v3 | Scoring 0-100 con **10** componentes ponderados |
| 🧪 Swap Simulator | `swapSimulator.py` | 485 | v2 | Simulación on-chain buy+sell vía `eth_call` + bytecode |
| 📡 Mempool Service | `mempoolService.py` | 394 | v2 | Escucha txs pendientes 10-30s antes de confirmación |
| 🛡️ Rug Detector | `rugDetector.py` | 417 | v2 | Monitoreo post-compra: LP drain, tax increase, dev sell |
| 🔍 Pre-Launch | `preLaunchDetector.py` | 358 | v2 | Detecta tokens antes de listing (contract + router) |
| 🐋 Smart Money | `smartMoneyTracker.py` | 339 | v2 | Trackea wallets rentables y señales de compra |
| 👨‍💻 Dev Tracker | `devTracker.py` | 435 | v3 | Reputación del deployer (historial de éxitos/rugs) |
| 🎯 Risk Engine | `riskEngine.py` | 444 | v3 | Motor unificado de decisión (7 componentes → 0-100) |
| ⚡ Trade Executor | `tradeExecutor.py` | 611 | v3 | Ejecución backend con private key + multi-RPC |
| 📊 Resource Monitor | `resourceMonitor.py` | 211 | v4 | CPU/RAM/WS/RPC metrics en tiempo real |
| 🔔 Alert Service | `alertService.py` | 404 | v4 | Alertas Telegram + Discord + Email + rate limiting |
| 📈 Metrics Service | `metricsService.py` | 329 | v4 | P&L tracking, win rate, detection speed, hourly series |

**Motor principal:** `sniperService.py` — **2,738 líneas** (ContractAnalyzer + SniperBot + main loop + enrichment)

### 5 APIs de seguridad

| API | Qué detecta |
|---|---|
| 🛡️ **GoPlus** | 18+ flags de seguridad, LP lock, holder concentration, fake renounce |
| 🍯 **Honeypot.is** | Simulación real de buy/sell, tax exacto, honeypot |
| 📊 **DexScreener** | Volumen, liquidez, edad, precio histórico (m5/h1/h6/h24) |
| 🦎 **CoinGecko** | Verificación de listing legítimo, social links |
| 🔍 **TokenSniffer** | Score de scam 0-100, detección de patrones fraudulentos |

### Pump Analyzer v3 — 10 componentes

| Componente | Peso | Descripción |
|---|---|---|
| liquidity | 14 | Sweet spot $8k–$120k |
| holder | 10 | Distribución saludable de holders |
| activity | 15 | Ratio buy/sell, volumen 24h |
| whale | 10 | Acumulación de ballenas |
| momentum | 12 | Patrón de precio gradual |
| age | 7 | Tokens frescos (1-24h ideal) |
| social | 4 | Web, CoinGecko, redes sociales |
| mcap | 12 | Market cap sweet spot ($20k-$400k) |
| holder_growth | 10 | Crecimiento de holders/minuto |
| lp_growth | 6 | Cambio en liquidez vs. inicial |

**Grades:** HIGH (80-100) / MEDIUM (60-79) / LOW (40-59) / AVOID (0-39)

Cada componente tiene try/except individual con fallback neutral (40) — un componente que falla NO rompe el score total.

### 18 capas de seguridad anti rug-pull

```
 1. 5 APIs de seguridad → detecta 18+ flags peligrosos
 2. Código verificado obligatorio → no compra contratos ocultos
 3. Anti-proxy → bloquea contratos upgradeable
 4. Anti-hidden-owner → bloquea control invisible
 5. Anti-fake-renounce → detecta can_take_back_ownership
 6. Holder concentration → top holder < 30%, creator < 20%
 7. LP Lock ≥80% obligatorio → owner no controla liquidez
 8. LP Lock ≥24h obligatorio → lock no expira pronto
 9. Smart Entry → no compra tokens ya bombeados (+30% 5m, +50% 1h)
10. Price dump check → no compra tokens en caída (-50% 24h, -40% 6h, -25% 1h)
11. Stop Loss 20% automático
12. Max Hold Hours → vende 1h antes de que expire el lock
13. Sync WS Listener → detecta cambios de precio en real-time
14. Pump Score 0-100 → rechaza tokens con grade AVOID (<40)
15. Swap Simulation → verifica on-chain buy+sell con eth_call
16. Bytecode Analysis → detecta SELFDESTRUCT / DELEGATECALL
17. Rug Detector → monitoreo post-compra (LP drain, dev sell)
18. Risk Engine → score unificado 0-100 con hard stops
```

### Enrichment inteligente (anti-spam)

- **Fast cycle (~3s):** Re-intenta SOLO APIs fallidas para tokens < 5 min de edad
- **Slow cycle (~15s):** Refresca DexScreener para tokens < 10 min de edad
- **Change detection:** Solo emite `token_updated` si liquidez, riesgo o APIs cambiaron
- **Dedup frontend:** `token_detected` actualiza card existente en lugar de duplicar

### RPC resiliente

- **10 RPCs BSC** + **5 RPCs ETH** con rotación automática
- **Retry hasta 3 intentos** por operación con rotación entre RPCs
- **Backoff exponencial** para rate limits (429): base 2s, max 30s, decay 0.8
- **Guard native_price:** Si BNB/ETH price es 0, re-fetch Binance antes de calcular USD

---

## 📁 Estructura del proyecto

```
TradingWeb/
├── manage.py                       # Django CLI
├── requirements.txt                # 13 dependencias pip
├── .env                            # Variables de entorno (alertas, keys)
├── .gitignore                      # Ignora .env, db, cache, logs
├── db.sqlite3                      # Base de datos SQLite
├── README.md                       # Este archivo
│
├── TradingWeb/                     # Configuración Django
│   ├── settings.py                 # Settings (DB, apps, channels, CORS)
│   ├── urls.py                     # URL routing principal
│   ├── asgi.py                     # ASGI config (Channels + WebSocket)
│   └── wsgi.py                     # WSGI fallback
│
├── trading/                        # App principal
│   ├── Controllers/
│   │   ├── viewController.py       # Renders de páginas HTML
│   │   └── walletController.py     # API wallet endpoints
│   │
│   ├── Models/
│   │   └── walletSessionModel.py   # Modelo de sesión de wallet
│   │
│   ├── Routes/
│   │   ├── urls.py                 # URL patterns de la app
│   │   └── walletRouter.py         # Rutas API wallet
│   │
│   ├── Services/                   # 12 módulos profesionales + core
│   │   ├── sniperService.py        # 🎯 Motor del Sniper Bot (2,738 líneas)
│   │   ├── pumpAnalyzer.py         # 🚀 Pump scoring engine v3 (597 líneas)
│   │   ├── swapSimulator.py        # 🧪 Swap simulation + bytecode (485 líneas)
│   │   ├── mempoolService.py       # 📡 Mempool listener (394 líneas)
│   │   ├── rugDetector.py          # 🛡️ Post-buy rug detection (417 líneas)
│   │   ├── preLaunchDetector.py    # 🔍 Pre-launch detection (358 líneas)
│   │   ├── smartMoneyTracker.py    # 🐋 Smart money tracking (339 líneas)
│   │   ├── devTracker.py           # 👨‍💻 Dev reputation v3 (435 líneas)
│   │   ├── riskEngine.py           # 🎯 Risk engine v3 (444 líneas)
│   │   ├── tradeExecutor.py        # ⚡ Trade executor v3 (611 líneas)
│   │   ├── resourceMonitor.py      # 📊 Resource monitor v4 (211 líneas)
│   │   ├── alertService.py         # 🔔 Alert service v4 (404 líneas)
│   │   ├── metricsService.py       # 📈 Metrics service v4 (329 líneas)
│   │   └── walletService.py        # Wallet logic (139 líneas)
│   │
│   ├── WebSocket/
│   │   ├── routing.py              # Rutas WebSocket
│   │   ├── sniperConsumer.py       # Bridge Sniper ↔ Frontend (146 líneas)
│   │   ├── binanceConsumer.py      # Consumer datos Binance (148 líneas)
│   │   └── walletConsumer.py       # Consumer eventos wallet (140 líneas)
│   │
│   ├── static/
│   │   ├── css/main.css            # Estilos Binance dark (2,569 líneas)
│   │   └── js/
│   │       ├── pageSniper.js       # Lógica frontend Sniper (1,862 líneas)
│   │       ├── dashboard.js        # Lógica dashboard trading (2,184 líneas)
│   │       ├── pageWallet.js       # Lógica página wallet (311 líneas)
│   │       ├── transactions.js     # Lógica transacciones (282 líneas)
│   │       └── walletConnect.js    # Trust Wallet connection (185 líneas)
│   │
│   ├── templates/
│   │   ├── base.html               # Template base
│   │   ├── sniper.html             # Sniper Bot UI (519 líneas)
│   │   ├── dashboard.html          # Trading panel (435 líneas)
│   │   ├── wallet.html             # Página wallet (172 líneas)
│   │   ├── transactions.html       # Transacciones (123 líneas)
│   │   └── login.html              # Login Trust Wallet (63 líneas)
│   │
│   └── tests/                      # 130 tests automatizados
│       ├── test_sniperService.py   # 36 tests — bot init, settings, state
│       ├── test_alertService.py    # 27 tests — events, rate limiting, send
│       ├── test_riskEngine.py      # 14 tests — weights, hard stops, scoring
│       ├── test_devTracker.py      # 16 tests — reputation, serial scammer
│       ├── test_pumpAnalyzer.py    # 15 tests — 10 components, stats
│       ├── test_resourceMonitor.py # 16 tests — CPU, WS, RPC tracking
│       └── test_rugDetector.py     # 6 tests — alert levels, triggers
│
├── docs/
│   ├── SNIPER.md                   # Documentación técnica del Sniper Bot
│   └── TRADE.md                    # Documentación del módulo de Trading
│
└── logs/                           # Logs de alertas (auto-rotados, 5MB max)
    └── sniper_alerts.log
```

**Totales:** ~17,000 líneas de código fuente (backend + frontend + tests + styles + templates)

---

## ⚙️ Configuración

### RPCs de blockchain

El bot usa RPCs públicos (sin API key) con rotación automática y backoff exponencial:

| Chain | RPCs | Principales |
|---|---|---|
| BSC (56) | **10 endpoints** | `publicnode`, `llamarpc`, `nodies`, `meowrpc`, `drpc`, 5× `binance.org` |
| Ethereum (1) | **5 endpoints** | `publicnode`, `llamarpc`, `drpc`, `meowrpc`, `ankr` |

### Settings del Sniper (desde la UI)

| Setting | Default | Descripción |
|---|---|---|
| Min Liquidity | $5,000 | Liquidez mínima para considerar un token |
| Max Buy Tax | 10% | Tax máximo aceptable al comprar |
| Max Sell Tax | 15% | Tax máximo aceptable al vender |
| Buy Amount | 0.05 BNB/ETH | Cantidad a invertir por snipe |
| Take Profit | 40% | % de ganancia para auto-sell |
| Stop Loss | 20% | % de pérdida para auto-sell |
| Slippage | 12% | Slippage permitido en trades |
| Only Safe | ✅ | Solo comprar tokens SAFE |
| Auto-Buy | ❌ | Ejecutar compras automáticamente (off por seguridad) |
| Min Pump Score | 40 | Mínimo pump score para comprar |
| Max Concurrent | 5 | Análisis paralelos de tokens |
| Block Range | 5 | Bloques por ciclo de escaneo |
| Poll Interval | 1.5s | Segundos entre escaneos |

### Settings de módulos

| Setting | Default | Descripción |
|---|---|---|
| `enable_pump_score` | ✅ | Pump scoring 0-100 (10 componentes) |
| `enable_swap_sim` | ✅ | Swap simulation vía eth_call |
| `enable_bytecode` | ✅ | Bytecode opcode analysis |
| `enable_rug_detector` | ✅ | Post-buy rug monitoring |
| `enable_dev_tracker` | ✅ | Developer reputation tracking |
| `enable_risk_engine` | ✅ | Unified risk scoring |
| `enable_mempool` | ❌ | Mempool listener (requiere WSS) |
| `enable_prelaunch` | ❌ | Pre-launch detection (experimental) |
| `enable_smart_money` | ❌ | Smart money tracking (experimental) |
| `enable_trade_executor` | ❌ | Backend execution (requiere private key) |

### User Profiles (1-click)

| Perfil | Min Liquidez | Only Safe | Auto Buy | Risk |
|---|---|---|---|---|
| **Novato** | $10,000 | ✅ | ❌ | Conservador |
| **Intermedio** | $5,000 | ✅ | ❌ | Balanced |
| **Avanzado** | $2,000 | ❌ | ✅ | Agresivo |

---

## 🔧 Solución de problemas

### El puerto 8000 está ocupado

```powershell
# Windows
Get-NetTCPConnection -LocalPort 8000 | Select-Object -ExpandProperty OwningProcess | ForEach-Object { Stop-Process -Id $_ -Force }
```

```bash
# Linux/macOS
lsof -ti:8000 | xargs kill -9
```

### Error `NativeCommandError` en PowerShell

Daphne escribe logs en stderr, PowerShell los interpreta como error. Usa `Start-Process -WindowStyle Hidden` para evitarlo.

### Error `hex string without 0x prefix`

Verificar que `PAIR_CREATED_TOPIC` en `sniperService.py` incluye el prefijo `0x`.

### Error `limit exceeded` en RPC

Los RPCs `bsc-dataseed*.binance.org` no soportan `eth_getLogs`. El bot rota automáticamente entre los 10 fallbacks con backoff exponencial.

### Token detectado con datos en 0

- Tokens nuevos tardan ~30s-2min en ser indexados por DexScreener/CoinGecko
- El enrichment dual reintenta automáticamente cada 3-15s
- Liquidez 0 en tokens sin liquidez real es comportamiento esperado

---

## 📚 Documentación adicional

- [docs/SNIPER.md](docs/SNIPER.md) — Documentación técnica completa del Sniper Bot (módulos v2+v3+v4)
- [docs/TRADE.md](docs/TRADE.md) — Documentación del módulo de Trading (dashboard, wallet, DEX swaps)

---

## 🛡️ Disclaimers

- Este proyecto es **educativo**. El trading de criptomonedas conlleva riesgo de pérdida.
- **Auto-Buy** está desactivado por defecto. Actívalo bajo tu propia responsabilidad.
- Los RPCs públicos pueden tener límites de velocidad. Para uso intensivo, considera un nodo propio o Alchemy/QuickNode.
- La clasificación "SAFE" no garantiza que un token sea seguro al 100%.
- La verificación de LP lock reduce pero no elimina el riesgo de rug pull.
- El **Trade Executor** (backend) requiere una private key — úsalo solo si entiendes los riesgos.

# TradingWeb — BSC Sniper Bot + Trading Platform

Plataforma web estilo Binance con autenticación Trust Wallet, trading en tiempo real (Binance WebSocket), y un **Sniper Bot** profesional que detecta nuevos tokens en BSC/ETH, los analiza con **5 APIs de seguridad + 28 módulos profesionales**, ejecuta compras/ventas automáticas con TP/SL, y protege contra rug pulls con **25 capas de seguridad**.

**v8** agrega: circuit breakers, API caching, retry automático, health dashboard, y **despliegue Docker con un solo comando**.

---

## Inicio rápido (Docker)

```bash
git clone <url-del-repo>
cd TradingWeb
cp .env.example .env        # edita con tus claves
docker compose up -d         # listo
```

Abre **http://localhost:8000** — el bot está corriendo.

---

## Tabla de contenidos

- [Requisitos](#requisitos)
- [Instalación con Docker (recomendado)](#instalación-con-docker-recomendado)
- [Instalación manual](#instalación-manual)
- [Configuración (.env)](#configuración-env)
- [Iniciar el servidor](#iniciar-el-servidor)
- [Pipeline del Sniper Bot](#pipeline-del-sniper-bot)
- [28 módulos profesionales](#28-módulos-profesionales)
- [5 APIs de seguridad + API Resilience (v8)](#5-apis-de-seguridad--api-resilience-v8)
- [25 capas de seguridad](#25-capas-de-seguridad)
- [Settings y perfiles](#settings-y-perfiles)
- [Tests](#tests)
- [Estructura del proyecto](#estructura-del-proyecto)
- [Solución de problemas](#solución-de-problemas)
- [Disclaimers](#disclaimers)

---

## Requisitos

| Componente | Versión mínima |
|---|---|
| **Docker + Docker Compose** | 24+ (para instalación Docker) |
| **Python** | 3.12+ (para instalación manual) |
| **Sistema operativo** | Windows 10/11, Linux, macOS |
| **Navegador** | Chrome, Edge, Firefox |

---

## Instalación con Docker (recomendado)

La forma más fácil. No necesitas instalar Python, Redis ni PostgreSQL — Docker lo maneja todo.

### 1. Clonar repositorio

```bash
git clone <url-del-repo>
cd TradingWeb
```

### 2. Configurar variables de entorno

```bash
cp .env.example .env
```

Edita `.env` con tus datos (ver [Configuración (.env)](#configuración-env) abajo).

### 3. Construir e iniciar

```bash
docker compose up -d
```

Esto levanta 5 contenedores:

| Servicio | Puerto | Descripción |
|---|---|---|
| **web** | 8000 | Django + Daphne (HTTP + WebSocket) |
| **redis** | 6379 | Channels + Celery + Cache |
| **db** | 5432 | PostgreSQL 16 |
| **celery** | — | Worker para tareas en background |
| **celery-beat** | — | Scheduler de tareas periódicas |

### 4. Verificar

```bash
docker compose ps           # ver estado de contenedores
docker compose logs -f web  # seguir logs del bot en tiempo real
```

Abre **http://localhost:8000** en tu navegador.

### Comandos útiles

```bash
docker compose down          # detener todo
docker compose up -d --build # reconstruir tras cambios
docker compose exec web python manage.py test trading.tests -v 2  # tests
docker compose exec web python manage.py createsuperuser           # admin
docker compose logs -f web   # ver logs en tiempo real
```

### Solo SQLite (sin PostgreSQL)

Si prefieres algo más ligero, cambia en `.env`:

```env
DB_ENGINE=sqlite3
```

Y comenta o elimina el servicio `db` en `docker-compose.yml`.

---

## Instalación manual

### Linux / macOS

```bash
git clone <url-del-repo>
cd TradingWeb
chmod +x install.sh run_bot.sh
./install.sh        # crea venv, instala deps, migra DB
# edita .env con tus claves
./run_bot.sh        # inicia Daphne en http://0.0.0.0:8000
```

### Windows (PowerShell)

```powershell
git clone <url-del-repo>
cd TradingWeb
powershell -ExecutionPolicy Bypass -File install.ps1
# edita .env con tus claves
powershell -ExecutionPolicy Bypass -File run_bot.ps1
```

### Paso a paso manual

```bash
# 1. Entorno virtual
python -m venv venv
source venv/bin/activate   # Linux/Mac
# venv\Scripts\activate    # Windows

# 2. Dependencias
pip install -r requirements.txt

# 3. Variables de entorno
cp .env.example .env       # edita con tus datos

# 4. Migraciones
python manage.py migrate

# 5. Verificar
python manage.py check

# 6. Iniciar
daphne -b 0.0.0.0 -p 8000 TradingWeb.asgi:application
```

---

## Configuración (.env)

Copia `.env.example` a `.env` y edita:

```env
# ── Django ──
SECRET_KEY=change-me-to-a-random-50-char-string
DEBUG=False
ALLOWED_HOSTS=localhost,127.0.0.1

# ── Database ──
DB_ENGINE=sqlite3           # o "postgresql" con Docker
DB_NAME=tradingweb
DB_USER=tradingweb
DB_PASSWORD=change_me
DB_HOST=db                  # "db" en Docker, "localhost" manual
DB_PORT=5432

# ── Redis ──
REDIS_URL=redis://redis:6379/0     # "redis://localhost:6379/0" manual
CELERY_BROKER_URL=redis://redis:6379/0

# ── Blockchain RPC ──
RPC_BSC=https://bsc-dataseed.binance.org/
RPC_BSC_FALLBACKS=https://bsc-dataseed1.defibit.io/,https://bsc-dataseed1.ninicoin.io/

# ── Sniper Bot ──
SNIPER_PRIVATE_KEY=         # sin 0x, para auto-compra (opcional)
AUTO_BUY=False
TAKE_PROFIT=50
STOP_LOSS=20

# ── Alertas ──
SNIPER_DISCORD_WEBHOOK=https://discord.com/api/webhooks/...
SNIPER_TELEGRAM_TOKEN=bot123456:ABCDEF...
SNIPER_TELEGRAM_CHAT_ID=@mi_canal
SNIPER_EMAIL_FROM=sniper@gmail.com
SNIPER_EMAIL_TO=alertas@gmail.com
SNIPER_EMAIL_PASSWORD=app_password

# ── MEV Protection ──
FLASHBOTS_SIGNER_KEY=
BSC_48CLUB_URL=https://api.48.club/v1/sendRawTransaction
```

> **Mínimo para funcionar:** Solo `RPC_BSC` es necesario. Todo lo demás es opcional.

---

## Iniciar el servidor

> **Importante:** Este proyecto usa **Django Channels** (WebSocket), requiere Daphne (ASGI), no `runserver`.

| Método | Comando |
|---|---|
| **Docker** | `docker compose up -d` |
| **Script Linux** | `./run_bot.sh` |
| **Script Windows** | `.\run_bot.ps1` |
| **Manual** | `daphne -b 0.0.0.0 -p 8000 TradingWeb.asgi:application` |

Dashboard: **http://localhost:8000**

### Páginas disponibles

| URL | Descripción |
|---|---|
| `/` o `/login/` | Login — Trust Wallet / MetaMask |
| `/dashboard/` | Trading (gráficas, orderbook, trades) |
| `/sniper/` | Sniper Bot — detección y auto-trading |
| `/transactions/` | Historial de transacciones |
| `/wallet/` | Gestión de wallet |

---

## Pipeline del Sniper Bot

```
Mempool → Pre-Launch → Block Scanner → PairCreated → ContractAnalyzer (5 APIs)
→ Phase 1:
  ├─ Pump Analyzer (10 componentes)
  ├─ Swap Simulator
  ├─ Bytecode Analysis
  ├─ Smart Money Tracker
  ├─ Dev Tracker (+ ML reputation)
  └─ Risk Engine (7 componentes → 0-100)
→ Phase 2 (v5 parallel):
  ├─ Proxy Detection (upgradeable / timelock / multisig)
  ├─ Stress Test (multi-amount slippage curve)
  ├─ ML Pump/Dump Predictor (online learning)
  ├─ Social Sentiment (Twitter/Telegram/Discord/Reddit)
  ├─ Anomaly Detection (volume spike / holder explosion)
  ├─ Whale Activity Analysis (coordinated buying / dev dumping)
  └─ Volatility Slippage (dynamic recommendation)
→ Phase 3 (v6):
  ├─ MEV Threat Analysis (frontrun/sandwich risk)
  ├─ Multi-DEX Route Finding (5 chains, 15+ DEXes)
  └─ AI Market Regime Detection (bull/bear/sideways)
→ Phase 4 (v7):
  ├─ Reinforcement Learner (Q-Learning + Bayesian optimization)
  ├─ Orderflow Analyzer (bot/whale/insider detection)
  ├─ Market Simulator (Monte Carlo + MEV modeling)
  ├─ Auto Strategy Generator (genetic programming)
  └─ Whale Network Graph (Sybil detection, cluster mapping)
→ Priority Gates (v8: skips heavy modules if confirmed honeypot)
→ Buy Gating (25 capas) → snipe_opportunity
→ MEV-Protected Auto-Buy → P&L Monitor → Rug Detector → Auto-Sell
→ Copy Trading → AI Strategy Optimizer → Backtest Engine
→ API Health Dashboard → Resource Monitor → Alert Service → Metrics
```

---

## 28 módulos profesionales

| # | Módulo | Archivo | v | Líneas | Descripción |
|---|---|---|---|---|---|
| 1 | Pump Analyzer | `pumpAnalyzer.py` | v3 | 597 | Scoring 0-100, 10 componentes ponderados |
| 2 | Swap Simulator | `swapSimulator.py` | v5 | 1,084 | Simulación on-chain + proxy + stress + volatility |
| 3 | Mempool Service | `mempoolService.py` | v2 | 394 | Escucha txs pendientes 10-30s antes |
| 4 | Rug Detector | `rugDetector.py` | v2 | 417 | Post-compra: LP drain, tax increase, dev sell |
| 5 | Pre-Launch | `preLaunchDetector.py` | v2 | 358 | Detecta tokens antes de listing |
| 6 | Smart Money | `smartMoneyTracker.py` | v5 | 650 | Whale tracking + activity analysis |
| 7 | Dev Tracker | `devTracker.py` | v5 | 582 | Reputación + ML clustering |
| 8 | Risk Engine | `riskEngine.py` | v3 | 444 | Motor unificado 0-100 con hard stops |
| 9 | Trade Executor | `tradeExecutor.py` | v3 | 611 | Ejecución con private key + multi-RPC |
| 10 | Resource Monitor | `resourceMonitor.py` | v4 | 211 | CPU/RAM/WS/RPC metrics en tiempo real |
| 11 | Alert Service | `alertService.py` | v4 | 768 | Telegram + Discord + Email + rate limiting |
| 12 | Metrics Service | `metricsService.py` | v4 | 329 | P&L tracking, win rate, hourly series |
| 13 | ML Predictor | `mlPredictor.py` | v5 | 620 | Pump/Dump predictor + online learning |
| 14 | Social Sentiment | `socialSentiment.py` | v5 | 604 | Multi-platform (Twitter/TG/Discord/Reddit) |
| 15 | Dynamic Scanner | `dynamicContractScanner.py` | v5 | 512 | Monitoreo continuo (bytecode/tax/owner) |
| 16 | MEV Protector | `mevProtection.py` | v6 | 500 | Anti-sandwich/frontrun (Flashbots, 48Club) |
| 17 | Copy Trader | `copyTrader.py` | v6 | 460 | Whale wallet following |
| 18 | Multi-DEX Router | `multiDexRouter.py` | v6 | 470 | Best route: 5 chains, 15+ DEXes |
| 19 | AI Strategy | `strategyOptimizer.py` | v6 | 500 | ML parameter tuning + regime detection |
| 20 | Backtest Engine | `backtestEngine.py` | v6 | 430 | Historical simulation + P&L analysis |
| 21 | Predictive Launch | `predictiveLaunchScanner.py` | v2 | — | Scanner predictivo de lanzamientos |
| 22 | Mempool Analyzer | `mempoolAnalyzer.py` | v2 | — | Análisis profundo del mempool |
| 23 | Reinforcement Learner | `reinforcementLearner.py` | v7 | 996 | Q-Learning + Bayesian optimization |
| 24 | Orderflow Analyzer | `orderflowAnalyzer.py` | v7 | 731 | Bot/whale/insider detection on-chain |
| 25 | Market Simulator | `marketSimulator.py` | v7 | 889 | Monte Carlo + MEV + gas war modeling |
| 26 | Auto Strategy Gen | `autoStrategyGenerator.py` | v7 | 751 | Genetic programming para strategies |
| 27 | Whale Network Graph | `whaleNetworkGraph.py` | v7 | 897 | Graph theory: Sybil, clusters, funding |
| 28 | API Resilience | `apiResilience.py` | v8 | 626 | Circuit breakers, cache, retry, health |

**Motor principal:** `sniperService.py` — **~3,970 líneas**

---

## 5 APIs de seguridad + API Resilience (v8)

### APIs

| API | Qué detecta |
|---|---|
| **GoPlus** | 18+ flags de seguridad, LP lock, holder concentration |
| **Honeypot.is** | Simulación real buy/sell, tax exacto, honeypot |
| **DexScreener** | Volumen, liquidez, edad, precio histórico |
| **CoinGecko** | Verificación de listing legítimo |
| **TokenSniffer** | Score de scam 0-100, patrones fraudulentos |

### API Resilience System (v8)

Todas las APIs externas ahora tienen tolerancia a fallos completa:

| Componente | Descripción |
|---|---|
| **Circuit Breaker** | CLOSED → OPEN → HALF_OPEN. Desactiva API tras 3+ fallos con timeout escalable (30s → 300s) |
| **Response Cache** | TTL por API (DexScreener 30s, CoinGecko 600s). Max 5000 entradas con eviction automático |
| **Retry + Backoff** | Reintentos por API con backoff exponencial + jitter |
| **Health Monitor** | Métricas: success rate, latency, status (healthy/degraded/down) |
| **Stale Fallback** | Si la API falla, devuelve la última respuesta cacheada |
| **Priority Gates** | Si un token es confirmed honeypot, salta los módulos pesados |

**Health Dashboard** visible en la UI del Sniper Bot — muestra estado de cada API en tiempo real.

---

## 25 capas de seguridad

```
 1. 5 APIs de seguridad → detecta 18+ flags peligrosos
 2. Código verificado obligatorio
 3. Anti-proxy → bloquea contratos upgradeable
 4. Anti-hidden-owner → bloquea control invisible
 5. Anti-fake-renounce → detecta can_take_back_ownership
 6. Holder concentration → top holder < 30%, creator < 20%
 7. LP Lock ≥80% obligatorio
 8. LP Lock ≥24h obligatorio
 9. Smart Entry → no compra tokens ya bombeados
10. Price dump check → no compra tokens en caída
11. Stop Loss 20% automático
12. Max Hold Hours → vende antes de que expire el lock
13. Sync WS Listener → precio en real-time
14. Pump Score 0-100 → rechaza AVOID (<40)
15. Swap Simulation → verifica buy+sell on-chain
16. Bytecode Analysis → SELFDESTRUCT / DELEGATECALL
17. Rug Detector → monitoreo post-compra
18. Risk Engine → score unificado 0-100
19. Proxy Danger Gate → bloquea proxy sin multisig
20. ML Pump/Dump Gate → bloquea si ML < 30
21. Anomaly Gate → bloquea si anomaly ≥ 0.8
22. Whale Dev-Dump Gate → bloquea dev dumping
23. MEV Critical Gate → bloquea threat_level "critical"
24. MEV Sandwich Gate → bloquea sandwich_risk > 70%
25. MEV-Protected Execution → Flashbots/48Club/gas boost
```

---

## Settings y perfiles

### Desde la UI del Sniper

| Setting | Default | Descripción |
|---|---|---|
| Min Liquidity | $5,000 | Liquidez mínima |
| Max Buy Tax | 10% | Tax máximo compra |
| Max Sell Tax | 15% | Tax máximo venta |
| Buy Amount | 0.05 BNB | Cantidad por snipe |
| Take Profit | 40% | Auto-sell ganancia |
| Stop Loss | 20% | Auto-sell pérdida |
| Slippage | 12% | Slippage permitido |
| Only Safe | Yes | Solo tokens SAFE |
| Auto-Buy | No | Compra automática (off por seguridad) |

### Perfiles 1-click

| Perfil | Min Liquidez | Only Safe | Auto Buy | Risk |
|---|---|---|---|---|
| **Novato** | $10,000 | Yes | No | Conservador |
| **Intermedio** | $5,000 | Yes | No | Balanced |
| **Avanzado** | $2,000 | No | Yes | Agresivo |

### Módulos habilitables

| Módulo | Default | Versión |
|---|---|---|
| Pump Score | On | v3 |
| Swap Simulation | On | v5 |
| Bytecode Analysis | On | v5 |
| Proxy Detector | On | v5 |
| Stress Test | On | v5 |
| ML Predictor | On | v5 |
| Social Sentiment | On | v5 |
| Anomaly Detector | On | v5 |
| Whale Activity | On | v5 |
| Dynamic Scanner | On | v5 |
| Volatility Slippage | On | v5 |
| Multi-DEX Router | On | v6 |
| AI Strategy | On | v6 |
| RL Learner | On | v7 |
| Orderflow Analyzer | On | v7 |
| Market Simulator | On | v7 |
| Auto Strategy Gen | On | v7 |
| Whale Network Graph | On | v7 |
| Mempool | Off | v2 |
| Pre-Launch | Off | v2 |
| Smart Money | Off | v2 |
| Trade Executor | Off | v3 |
| MEV Protection | Off | v6 |
| Copy Trading | Off | v6 |
| Backtesting | Off | v6 |

---

## Tests

```bash
# Manual
python manage.py test trading.tests -v 2

# Docker
docker compose exec web python manage.py test trading.tests -v 2
```

**413+ tests** cubriendo todos los módulos:

| Suite | Tests | Cubre |
|---|---|---|
| `test_sniperService.py` | 36 | Bot init, settings, state |
| `test_alertService.py` | 30 | Events, rate limiting, Discord/Telegram/Email |
| `test_riskEngine.py` | 14 | Weights, hard stops, scoring |
| `test_devTracker.py` | 16 | Reputation, serial scammer |
| `test_pumpAnalyzer.py` | 15 | 10 components, stats |
| `test_resourceMonitor.py` | 16 | CPU, WS, RPC tracking |
| `test_rugDetector.py` | 6 | Alert levels, triggers |
| `test_v5_modules.py` | 36 | ML, proxy, social, scanner |
| `test_v6_modules.py` | 138 | MEV, copy trading, multi-DEX, AI, backtest |
| `test_v7_modules.py` | 72 | RL, orderflow, simulator, strategy, whale graph |
| `test_apiResilience.py` | 34 | Circuit breaker, cache, retry, health, manager |

---

## Estructura del proyecto

```
TradingWeb/
├── manage.py
├── requirements.txt
├── .env.example              ← copiar a .env
├── .gitignore
├── .dockerignore
├── Dockerfile                ← multi-stage build
├── docker-compose.yml        ← 5 servicios (web, redis, db, celery, beat)
├── install.sh                ← instalador Linux/Mac
├── install.ps1               ← instalador Windows
├── run_bot.sh                ← arranque Linux/Mac
├── run_bot.ps1               ← arranque Windows
├── README.md
│
├── TradingWeb/               # Configuración Django
│   ├── settings.py           # DB, Channels, Celery, Redis, CORS, Logging
│   ├── urls.py
│   ├── asgi.py               # ASGI (Channels + WebSocket)
│   ├── celery.py             # Celery app factory
│   └── wsgi.py
│
├── trading/                  # App principal
│   ├── Controllers/
│   │   ├── viewController.py
│   │   └── walletController.py
│   │
│   ├── Models/
│   │   └── walletSessionModel.py
│   │
│   ├── Routes/
│   │   ├── urls.py
│   │   └── walletRouter.py
│   │
│   ├── Services/             # 28 módulos profesionales
│   │   ├── sniperService.py          # Motor principal (~3,970 líneas)
│   │   ├── apiResilience.py          # v8: Circuit breakers + cache + retry
│   │   ├── pumpAnalyzer.py
│   │   ├── swapSimulator.py
│   │   ├── mempoolService.py
│   │   ├── mempoolAnalyzer.py
│   │   ├── rugDetector.py
│   │   ├── preLaunchDetector.py
│   │   ├── predictiveLaunchScanner.py
│   │   ├── smartMoneyTracker.py
│   │   ├── devTracker.py
│   │   ├── riskEngine.py
│   │   ├── tradeExecutor.py
│   │   ├── resourceMonitor.py
│   │   ├── alertService.py
│   │   ├── metricsService.py
│   │   ├── mlPredictor.py
│   │   ├── socialSentiment.py
│   │   ├── dynamicContractScanner.py
│   │   ├── mevProtection.py
│   │   ├── copyTrader.py
│   │   ├── multiDexRouter.py
│   │   ├── strategyOptimizer.py
│   │   ├── backtestEngine.py
│   │   ├── reinforcementLearner.py   # v7
│   │   ├── orderflowAnalyzer.py      # v7
│   │   ├── marketSimulator.py        # v7
│   │   ├── autoStrategyGenerator.py  # v7
│   │   ├── whaleNetworkGraph.py      # v7
│   │   └── walletService.py
│   │
│   ├── WebSocket/
│   │   ├── routing.py
│   │   ├── sniperConsumer.py
│   │   ├── binanceConsumer.py
│   │   └── walletConsumer.py
│   │
│   ├── static/
│   │   ├── css/main.css
│   │   └── js/
│   │       ├── pageSniper.js
│   │       ├── dashboard.js
│   │       ├── pageWallet.js
│   │       ├── transactions.js
│   │       └── walletConnect.js
│   │
│   ├── templates/
│   │   ├── base.html
│   │   ├── sniper.html
│   │   ├── dashboard.html
│   │   ├── wallet.html
│   │   ├── transactions.html
│   │   └── login.html
│   │
│   └── tests/                # 413+ tests
│       ├── test_sniperService.py
│       ├── test_alertService.py
│       ├── test_riskEngine.py
│       ├── test_devTracker.py
│       ├── test_pumpAnalyzer.py
│       ├── test_resourceMonitor.py
│       ├── test_rugDetector.py
│       ├── test_v5_modules.py
│       ├── test_v6_modules.py
│       ├── test_v7_modules.py
│       └── test_apiResilience.py
│
├── docs/
│   ├── SNIPER.md
│   └── TRADE.md
│
└── logs/
    └── sniper_alerts.log
```

---

## Dependencias

| Paquete | Para qué |
|---|---|
| `Django 6.0.2` | Framework web |
| `channels 4.3.2` + `daphne 4.2.1` | WebSocket (ASGI) |
| `web3 7.14.1` | Blockchain (BSC/ETH) |
| `aiohttp 3.13.3` | Async HTTP (APIs externas) |
| `celery[redis] 5.4.0` | Task queue |
| `numpy` + `scikit-learn` + `pandas` | ML / Data |
| `flashbots 2.1.1` | MEV protection |
| `psycopg2-binary` | PostgreSQL (opcional) |
| `python-dotenv` | Variables de entorno |
| `PyJWT` + `passlib` + `pycryptodome` | Auth + crypto |

---

## RPC resiliente

| Chain | RPCs | Comportamiento |
|---|---|---|
| BSC (56) | 10 endpoints | Rotación automática + backoff exponencial |
| ETH (1) | 5 endpoints | Rotación automática + backoff exponencial |

Para mayor estabilidad usa un nodo privado (Alchemy, QuickNode) vía `RPC_BSC` en `.env`.

---

## Solución de problemas

### Puerto 8000 ocupado

```powershell
# Windows
Get-NetTCPConnection -LocalPort 8000 | Select-Object -ExpandProperty OwningProcess | ForEach-Object { Stop-Process -Id $_ -Force }
```

```bash
# Linux/macOS
lsof -ti:8000 | xargs kill -9
```

### Docker: contenedor no arranca

```bash
docker compose logs web    # ver error exacto
docker compose down        # limpiar
docker compose up -d       # reintentar
```

### Error `NativeCommandError` en PowerShell

Daphne escribe logs en stderr. Usa los scripts `.ps1` que manejan esto automáticamente.

### Token detectado con datos en 0

Tokens nuevos tardan ~30s-2min en ser indexados por DexScreener/CoinGecko. El enrichment reintenta automáticamente cada 3-15s.

### API "down" en el Health Dashboard

El circuit breaker se recupera automáticamente (timeout: 30s → 300s). No requiere acción manual.

---

## Disclaimers

- Este proyecto es **educativo**. El trading de criptomonedas conlleva riesgo de pérdida.
- **Auto-Buy** está desactivado por defecto. Actívalo bajo tu propia responsabilidad.
- Los RPCs públicos pueden tener límites de velocidad.
- La clasificación "SAFE" no garantiza que un token sea seguro al 100%.
- El **Trade Executor** requiere una private key — úsalo solo si entiendes los riesgos.

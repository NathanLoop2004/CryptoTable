# TradingWeb — Plataforma de Trading + Sniper Bot

Plataforma web estilo Binance con autenticación Trust Wallet, trading en tiempo real (Binance WebSocket), y un **Sniper Bot** inteligente que detecta nuevos tokens en BSC/ETH, los analiza con **5 APIs de seguridad**, ejecuta compras/ventas automáticas con TP/SL, y protege contra rug pulls verificando LP lock.

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
| `Django` | Framework web principal |
| `channels` + `daphne` | WebSocket (ASGI) en tiempo real |
| `web3` | Interacción con blockchain (BSC/ETH) |
| `aiohttp` | Llamadas async a APIs externas (GoPlus, honeypot.is, DexScreener, etc.) |
| `django-cors-headers` | Permitir peticiones cross-origin |
| `python-dotenv` | Variables de entorno desde `.env` |
| `PyJWT` + `passlib` | Autenticación Trust Wallet |
| `requests` | Peticiones HTTP síncronas |
| `websockets` | Conexión WebSocket a Binance + Sync listener |
| `psycopg2-binary` | PostgreSQL (opcional, usa SQLite por defecto) |
| `pycryptodome` | Criptografía para firmas de wallet |

### 4. Aplicar migraciones

```bash
python manage.py migrate
```

### 5. Verificar que todo está bien

```bash
python manage.py check
```

Debería mostrar: `System check identified no issues.`

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
| `/` o `/dashboard/` | Panel principal de trading (gráficas, orderbook, trades) |
| `/sniper/` | Sniper Bot — detección y auto-trading de tokens nuevos |
| `/transactions/` | Historial de transacciones |
| `/wallet/` | Gestión de wallet |
| `/login/` | Login con Trust Wallet |

---

## 🎯 Sniper Bot — Cómo funciona

### Pipeline completo

1. **Start** → El bot escanea bloques nuevos en BSC (o ETH) cada ~1.5s
2. **Detecta** → Busca eventos `PairCreated` en PancakeSwap/Uniswap
3. **Verifica liquidez** → `pair.getReserves()` → mínimo $5,000 USD
4. **Analiza** → Para CADA token detectado, consulta **5 APIs de seguridad**:
   - **GoPlus Security** — 15+ flags de seguridad + LP lock info
   - **Honeypot.is** — Detecta honeypots, buy/sell tax
   - **DexScreener** — Volumen, liquidez, edad, precio histórico (m5, h1, h6, h24)
   - **CoinGecko** — Verificación de listing legítimo
   - **TokenSniffer** — Score de scam (0-100), detección de estafa
5. **Clasifica riesgo** → SAFE 🟢 / WARNING 🟡 / DANGER 🔴
6. **Smart Entry Gate** → Rechaza tokens ya bombeados (+30% en 5m, +50% en 1h)
7. **LP Lock Gate** → Requiere LP bloqueado ≥80% por ≥24h
8. **Auto-Buy** → Ejecuta swap via ethers.js desde el navegador
9. **P&L Monitoring** → Actualiza cada ~3s con TP/SL automático
10. **Auto-Sell** → Vende automáticamente al alcanzar TP, SL, o límite de tiempo

### 5 APIs de seguridad

| API | Qué detecta |
|---|---|
| 🛡️ **GoPlus** | Honeypot, mintable, blacklist, proxy, hidden owner, LP lock, holder count |
| 🍯 **Honeypot.is** | Simulación real de buy/sell, tax exacto, honeypot |
| 📊 **DexScreener** | Volumen 24h, liquidez, buys/sells, edad del par, cambio de precio (m5/h1/h6/h24) |
| 🦎 **CoinGecko** | Verificación de listing, links sociales, website |
| 🔍 **TokenSniffer** | Score de scam 0-100, detección de patrones fraudulentos |

### Enrichment dual (retry inteligente)

- **Fast cycle (~3s):** Re-intenta SOLO las APIs que fallaron (GoPlus, Honeypot, CoinGecko, TokenSniffer)
- **Slow cycle (~15s):** Refresca TODOS los tokens con DexScreener completo (tarda en indexar tokens nuevos)

### Flags de seguridad que detecta

| Flag | Significado |
|---|---|
| 🍯 Honeypot | No puedes vender |
| 🖨️ Mintable | El dueño puede crear más tokens |
| 🚫 Blacklist | Puede bloquear wallets |
| ⏸️ Pausable | Puede pausar el trading |
| 🔐 Can't sell all | No puedes vender todos tus tokens |
| ⚠️ Ctrl balance | El dueño puede modificar balances |
| 💣 Self-destruct | El contrato puede autodestruirse |
| 🔄 Proxy | Puede cambiar la lógica del contrato |
| 👤 Hidden owner | Owner oculto |
| 👑 Has owner | Contrato tiene dueño con privilegios |
| 🔒 Not verified | Código fuente no publicado |
| 🔑 LP Lock | Liquidez bloqueada (PinkLock, Unicrypt, Team.Finance) |

### Validación de LP Lock (anti rug-pull)

El bot verifica el bloqueo de liquidez antes de comprar:

| Condición | Resultado |
|---|---|
| LP no bloqueada | ❌ **Rechazado** — riesgo de rug pull |
| LP bloqueada < 80% | ❌ **Rechazado** — owner controla la mayoría |
| LP lock < 24h restantes | ❌ **Rechazado** — lock demasiado corto |
| LP lock expirado | ❌ **Rechazado** + alerta "Lock EXPIRADO" |
| LP bloqueada ≥ 80%, ≥ 24h | ✅ **Aprobado** |

Fuentes de lock detectadas: **PinkLock**, **Unicrypt**, **Team.Finance**.

### Smart Entry Gate (protección anti-pump)

El bot rechaza tokens que ya subieron demasiado antes de comprar:

| Condición | Resultado |
|---|---|
| Precio 5min ≥ +30% | ❌ **Rechazado** — ya bombeado |
| Precio 1h ≥ +50% | ❌ **Rechazado** — ya bombeado |
| Precio 24h ≤ -50% | ❌ **Rechazado** — dump masivo |
| Precio 6h ≤ -40% | ❌ **Rechazado** — dump reciente |
| Precio 1h ≤ -25% | ❌ **Rechazado** — caída activa |

### Auto-Buy & Auto-Sell

- **Auto-Buy**: El bot compra automáticamente tokens que pasan TODOS los filtros de seguridad
- **Auto-Sell por TP**: Vende cuando la ganancia alcanza el Take Profit (default 40%)
- **Auto-Sell por SL**: Vende cuando la pérdida alcanza el Stop Loss (default 20%)
- **Auto-Sell por tiempo**: Vende cuando se cumple el tiempo máximo de hold (basado en LP lock)
- **P&L Update**: Precio actualizado cada ~3s para reacción rápida

### WebSocket Sync Listener

El bot mantiene un listener WebSocket en tiempo real para eventos `Sync` de los pares LP rastreados. Esto permite detectar cambios de precio sin polling adicional.

---

## 📁 Estructura del proyecto

```
TradingWeb/
├── manage.py                    # Django CLI
├── requirements.txt             # Dependencias pip
├── db.sqlite3                   # Base de datos SQLite
├── README.md                    # Este archivo
│
├── TradingWeb/                  # Configuración Django
│   ├── settings.py              # Settings (DB, apps, channels, CORS)
│   ├── urls.py                  # URL routing principal
│   ├── asgi.py                  # ASGI config (Channels + WebSocket)
│   └── wsgi.py                  # WSGI fallback
│
├── trading/                     # App principal
│   ├── Controllers/             # Vistas / controladores
│   ├── Models/                  # Modelos de datos
│   ├── Routes/                  # URL patterns de la app
│   ├── Services/                # Lógica de negocio
│   │   └── sniperService.py     # 🎯 Motor del Sniper Bot (~1860 líneas)
│   ├── WebSocket/               # WebSocket consumers
│   │   └── sniperConsumer.py    # Bridge Sniper ↔ Frontend (~161 líneas)
│   ├── static/
│   │   ├── css/main.css         # Estilos (tema Binance dark)
│   │   └── js/
│   │       ├── pageSniper.js    # Lógica frontend del Sniper (~1627 líneas)
│   │       ├── walletConnect.js # Trust Wallet connection
│   │       └── ...
│   └── templates/
│       ├── base.html            # Template base
│       ├── sniper.html          # Sniper Bot UI (~384 líneas)
│       ├── dashboard.html       # Trading panel
│       └── ...
│
└── docs/
    ├── TRADE.md                 # Documentación del módulo de trading
    └── SNIPER.md                # Documentación detallada del sniper bot
```

---

## ⚙️ Configuración

### RPCs de blockchain

El bot usa estos RPCs públicos (sin API key) con rotación automática en caso de fallo:

| Chain | RPC principal | Fallbacks |
|---|---|---|
| BSC (56) | `bsc-rpc.publicnode.com` | `bsc.drpc.org`, `binance.llamarpc.com`, `bsc-pokt.nodies.app`, `bsc.meowrpc.com` |
| Ethereum (1) | `eth.llamarpc.com` | `eth.drpc.org`, `ethereum-rpc.publicnode.com`, `eth.meowrpc.com` |

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
| Only Safe | ✅ | Solo comprar tokens clasificados como SAFE |
| Auto-Buy | ❌ | Ejecutar compras automáticamente (desactivado por seguridad) |
| Max Hold Hours | Auto | Basado en LP lock duration (o disabled) |
| Max Concurrent | 5 | Análisis paralelos de tokens |
| Block Range | 5 | Bloques por ciclo de escaneo |
| Poll Interval | 1.5s | Segundos entre escaneos |

---

## 🔧 Solución de problemas

### El puerto 8000 está ocupado

```powershell
# Windows PowerShell
Get-NetTCPConnection -LocalPort 8000 | Select-Object -ExpandProperty OwningProcess | ForEach-Object { Stop-Process -Id $_ -Force }
```

```bash
# Linux/macOS
lsof -ti:8000 | xargs kill -9
```

### Error `NativeCommandError` en PowerShell

Daphne escribe logs en stderr, PowerShell los interpreta como error. Usa `Start-Process -WindowStyle Hidden` (Opción B) para evitarlo.

### Error `hex string without 0x prefix`

Verificar que `PAIR_CREATED_TOPIC` en `sniperService.py` incluye el prefijo `0x`:
```python
PAIR_CREATED_TOPIC = "0x" + Web3.keccak(text="PairCreated(...)").hex()
```

### Error `limit exceeded` en RPC

Los RPCs `bsc-dataseed*.binance.org` no soportan `eth_getLogs`. El bot rota automáticamente entre fallbacks.

---

## 📚 Documentación adicional

- [docs/TRADE.md](docs/TRADE.md) — Documentación del módulo de trading
- [docs/SNIPER.md](docs/SNIPER.md) — Documentación detallada del sniper bot

---

## 🛡️ Disclaimers

- Este proyecto es **educativo**. El trading de criptomonedas conlleva riesgo de pérdida.
- **Auto-Buy** está desactivado por defecto. Actívalo bajo tu propia responsabilidad.
- Los RPCs públicos pueden tener límites de velocidad. Para uso intensivo, considera un nodo propio o un servicio como Alchemy/QuickNode.
- La clasificación de "SAFE" no garantiza que un token sea seguro al 100%.
- La verificación de LP lock reduce pero no elimina el riesgo de rug pull.

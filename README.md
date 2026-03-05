# TradingWeb — Plataforma de Trading + Sniper Bot

Plataforma web estilo Binance con autenticación Trust Wallet, trading en tiempo real (Binance WebSocket), y un **Sniper Bot** que detecta nuevos tokens en BSC/ETH, los analiza automáticamente y clasifica por seguridad.

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
| `aiohttp` | Llamadas async a APIs externas (GoPlus, honeypot.is) |
| `django-cors-headers` | Permitir peticiones cross-origin |
| `python-dotenv` | Variables de entorno desde `.env` |
| `PyJWT` + `passlib` | Autenticación Trust Wallet |
| `requests` | Peticiones HTTP síncronas |
| `websockets` | Conexión WebSocket a Binance |
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
| `/sniper/` | Sniper Bot — detección de tokens nuevos |
| `/transactions/` | Historial de transacciones |
| `/wallet/` | Gestión de wallet |
| `/login/` | Login con Trust Wallet |

---

## 🎯 Sniper Bot — Cómo funciona

1. **Start** → El bot empieza a escanear bloques nuevos en BSC (o ETH)
2. **Detecta** → Busca eventos `PairCreated` en PancakeSwap/Uniswap
3. **Analiza** → Para CADA token detectado, consulta:
   - **honeypot.is** — Detecta honeypots, buy/sell tax
   - **GoPlus Security** — Detecta 15+ flags de seguridad
4. **Clasifica** → SAFE / WARNING / DANGER
5. **Muestra** → Todo aparece en el modal "Detectados" con filtros

### Flags de seguridad que detecta

| Flag | Significado |
|---|---|
| 🍯 Honeypot | No puedes vender |
| 🖨️ Mintable | El dueño puede crear más tokens |
| 🚫 Blacklist | Puede bloquear wallets |
| ⏸️ Pausable | Puede pausar el trading |
| 🔐 Can't sell | No puedes vender todos tus tokens |
| ⚠️ Ctrl balance | El dueño puede modificar balances |
| 💣 Self-destruct | El contrato puede autodestruirse |
| 🔄 Proxy | Puede cambiar la lógica del contrato |
| 👤 Hidden owner | Owner oculto |
| 👑 Has owner | Contrato tiene dueño con privilegios |
| 🔒 Not verified | Código fuente no publicado |
| ✅ Safe | Sin flags peligrosos |
| 📝 Verified | Código verificado y auditable |

---

## 📁 Estructura del proyecto

```
TradingWeb/
├── manage.py                    # Django CLI
├── requirements.txt             # Dependencias pip
├── db.sqlite3                   # Base de datos SQLite
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
│   │   └── sniperService.py     # 🎯 Motor del Sniper Bot
│   ├── WebSocket/               # WebSocket consumers
│   │   └── sniperConsumer.py    # Bridge Sniper ↔ Frontend
│   ├── static/
│   │   ├── css/main.css         # Estilos (tema Binance dark)
│   │   └── js/
│   │       ├── pageSniper.js    # Lógica frontend del Sniper
│   │       ├── walletConnect.js # Trust Wallet connection
│   │       └── ...
│   └── templates/
│       ├── base.html            # Template base
│       ├── sniper.html          # Sniper Bot UI
│       ├── dashboard.html       # Trading panel
│       └── ...
│
└── docs/
    ├── TRADE.md                 # Documentación de trading
    └── SNIPER.md                # Documentación del sniper
```

---

## ⚙️ Configuración

### RPCs de blockchain

El bot usa estos RPCs públicos (sin API key) configurados en `trading/Services/sniperService.py`:

| Chain | RPC principal | Fallbacks |
|---|---|---|
| BSC (56) | `bsc.drpc.org` | `bsc-rpc.publicnode.com`, `binance.llamarpc.com`, `bsc-pokt.nodies.app`, `bsc.meowrpc.com` |
| Ethereum (1) | `eth.drpc.org` | `ethereum-rpc.publicnode.com`, `eth.llamarpc.com` |

### Settings del Sniper (desde la UI)

| Setting | Default | Descripción |
|---|---|---|
| Min Liquidity | $5,000 | Liquidez mínima para considerar un token |
| Max Buy Tax | 10% | Tax máximo aceptable al comprar |
| Max Sell Tax | 15% | Tax máximo aceptable al vender |
| Buy Amount | 0.05 BNB/ETH | Cantidad a invertir por snipe |
| Take Profit | 40% | % de ganancia para vender automáticamente |
| Stop Loss | 15% | % de pérdida para vender automáticamente |
| Slippage | 12% | Slippage permitido en trades |
| Only Safe | ✅ | Solo comprar tokens clasificados como SAFE |
| Auto-Buy | ❌ | Ejecutar compras automáticamente (desactivado por seguridad) |

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

Los RPCs `bsc-dataseed*.binance.org` no soportan `eth_getLogs`. Usar `bsc.drpc.org` u otro de la lista de fallbacks.

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

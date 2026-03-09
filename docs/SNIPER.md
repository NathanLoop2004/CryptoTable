# Sniper Bot — Documentación Técnica

Documentación técnica detallada del módulo **Sniper Bot** de TradingWeb: detección automática de nuevos tokens en BSC/ETH, análisis multi-capa con 5 APIs + 21 módulos profesionales (incluye ML, sentimiento social, escaneo dinámico, MEV protection, copy trading, multi-DEX routing, AI strategy optimizer, backtesting engine), y ejecución de trades con 25 capas de protección.

---

## Índice

1. [Arquitectura](#1-arquitectura)
2. [Pipeline de detección](#2-pipeline-de-detección)
3. [Módulos profesionales](#3-módulos-profesionales)
4. [APIs de seguridad](#4-apis-de-seguridad)
5. [Sistema de scoring](#5-sistema-de-scoring)
6. [Ejecución de trades](#6-ejecución-de-trades)
7. [WebSocket events](#7-websocket-events)
8. [Enrichment & anti-spam](#8-enrichment--anti-spam)
9. [Resiliencia RPC](#9-resiliencia-rpc)
10. [Frontend dedup](#10-frontend-dedup)
11. [Configuración avanzada](#11-configuración-avanzada)
12. [Tests](#12-tests)
13. [Troubleshooting](#13-troubleshooting)

---

## 1. Arquitectura

### Diagrama de flujo

```
┌────────────────────┐     ┌─────────────────────────┐     ┌──────────────────┐
│   sniper.html      │◄────│   sniperConsumer.py      │◄────│  SniperBot       │
│   pageSniper.js    │────►│   (WebSocket bridge)     │────►│  sniperService   │
│   (2,166 + 519 ln) │     │   (146 líneas)           │     │  (3,103 líneas)  │
└────────────────────┘     └─────────────────────────┘     └──────┬───────────┘
                                                                  │
                           ┌──────────────────────────────────────┤
                           │                                      │
                    ┌──────▼──────┐  ┌────────────────┐  ┌───────▼────────┐
                    │ ContractAn. │  │ PumpAnalyzer   │  │ SwapSimulator  │
                    │ (5 APIs)    │  │ v3 (10 comp)   │  │ v5 +Proxy/Stress│
                    └─────────────┘  └────────────────┘  └────────────────┘
                           │                │                     │
                    ┌──────▼──────┐  ┌──────▼─────────┐  ┌───────▼────────┐
                    │ RiskEngine  │  │ DevTracker     │  │ MempoolService │
                    │ v3 (unified)│  │ v5 (+ML reputa)│  │ (pending txs)  │
                    └─────────────┘  └────────────────┘  └────────────────┘
                           │                │                     │
                    ┌──────▼──────┐  ┌──────▼─────────┐  ┌───────▼────────┐
                    │ TradeExec.  │  │ SmartMoney     │  │ PreLaunch Det. │
                    │ v3 (backend)│  │ v5 (+whale act)│  │ (pre-listing)  │
                    └─────────────┘  └────────────────┘  └────────────────┘
                           │                │                     │
                    ┌──────▼──────┐  ┌──────▼─────────┐  ┌───────▼────────┐
                    │ RugDetector │  │ AlertService   │  │ MetricsService │
                    │ (post-buy)  │  │ v4 (multi-ch)  │  │ v4 (P&L track) │
                    └─────────────┘  └────────────────┘  └────────────────┘
                           │                │                     │
                    ┌──────▼──────┐  ┌──────▼─────────┐  ┌───────▼────────┐
                    │ ResourceMon.│  │ MLPredictor    │  │ SocialSentim.  │
                    │ v4 (CPU/RPC)│  │ v5 (pump/dev)  │  │ v5 (4 platafs) │
                    └─────────────┘  └────────────────┘  └────────────────┘
                                            │
                                     ┌──────▼─────────┐
                                     │ DynContractScan│
                                     │ v5 (continuo)  │
                                     └────┬───────────┘
                                          │
              ┌───────────────────────────┤ v6 Modules
              │                           │
       ┌──────▼──────┐  ┌────────────────▼┐  ┌───────────────┐
       │ MEVProtector │  │ MultiDexRouter  │  │ StrategyOpt.  │
       │ v6 (anti-MW)│  │ v6 (5 chains)   │  │ v6 (AI/ML)    │
       └─────────────┘  └─────────────────┘  └───────────────┘
              │                    │                 │
       ┌──────▼──────┐  ┌────────▼────────┐  ┌─────▼─────────┐
       │ CopyTrader   │  │ BacktestEngine  │  │ Celery Tasks  │
       │ v6 (whales)  │  │ v6 (simulation) │  │ (scheduled)   │
       └──────────────┘  └─────────────────┘  └───────────────┘
```

### Archivos principales

| Archivo | Líneas | Rol |
|---|---|---|
| `Services/sniperService.py` | ~3,360 | Motor principal: `ContractAnalyzer` + `SniperBot` + enrichment + scan loop + v6 integration |
| `Services/swapSimulator.py` | 1,084 | Simulación on-chain + bytecode + proxy + stress + volatilidad |
| `Services/smartMoneyTracker.py` | 650+ | Tracking de wallets rentables + whale activity analysis |
| `Services/mlPredictor.py` | 621 | **v5** ML: pump/dump predictor, dev reputation ML, anomaly detector |
| `Services/tradeExecutor.py` | 611 | Ejecución backend con private key |
| `Services/socialSentiment.py` | 604 | **v5** Sentimiento social: Twitter/Telegram/Discord/Reddit |
| `Services/pumpAnalyzer.py` | 597 | Scoring con 10 componentes ponderados |
| `Services/devTracker.py` | 582 | Reputación del deployer + ML reputation blending |
| `Services/dynamicContractScanner.py` | 512 | **v5** Escaneo continuo de contratos con scoring dinámico |
| `Services/mevProtection.py` | ~500 | **v6** Anti-sandwich/frontrun (Flashbots, 48Club, gas boost, tx split) |
| `Services/strategyOptimizer.py` | ~500 | **v6** AI/ML strategy optimization + market regime detection |
| `Services/multiDexRouter.py` | ~470 | **v6** Best route across 5 chains, 15+ DEXes |
| `Services/copyTrader.py` | ~460 | **v6** Whale wallet following + auto-copy trades |
| `Services/backtestEngine.py` | ~430 | **v6** Historical backtesting with virtual portfolio |
| `Services/riskEngine.py` | 444 | Motor unificado de riesgo (7 señales → 0-100) |
| `Services/rugDetector.py` | 417 | Monitoreo post-compra continuo |
| `Services/alertService.py` | 404 | Multi-canal: Telegram/Discord/Email |
| `Services/mempoolService.py` | 394 | Listener de transacciones pendientes |
| `Services/preLaunchDetector.py` | 358 | Detección antes de listing |
| `Services/metricsService.py` | 329 | P&L tracking, win rate, series temporales |
| `Services/resourceMonitor.py` | 211 | CPU/RAM/WebSocket/RPC metrics |
| `tasks.py` | ~170 | **v6** Celery periodic + on-demand tasks |
| `WebSocket/sniperConsumer.py` | 146 | Bridge Django Channels ↔ Frontend |
| `static/js/pageSniper.js` | ~2,240 | Lógica frontend completa con dedup + v5/v6 UI |
| `templates/sniper.html` | ~610 | UI del Sniper Bot con v6 modules panel |
| `static/css/main.css` | ~2,830 | Estilos Binance dark theme + v6 chips/badges |

---

## 2. Pipeline de detección

### Ciclo principal (`_scan_blocks`)

```python
while self.running:
    # 1. Obtener bloque actual (con safe_get_block_number y rotación RPC)
    current_block = self._safe_get_block_number()

    # 2. Calcular rango (max BLOCK_RANGE bloques = 5 por defecto)
    from_block = self.last_block + 1
    to_block   = min(current_block, from_block + self.BLOCK_RANGE - 1)

    # 3. Filtrar logs PairCreated en PancakeSwap V2 Factory
    logs = w3.eth.get_logs({
        'fromBlock': from_block,
        'toBlock':   to_block,
        'address':   PANCAKE_V2_FACTORY,
        'topics':    [PAIR_CREATED_TOPIC]
    })

    # 4. Para cada par nuevo → analizar token
    for log in logs:
        token0, token1 = decode_log(log)
        # Identifica cuál es el token nuevo (el que NO es WBNB/WETH)
        await self._analyze_new_pair(token0, token1, pair_address)

    # 5. Avanzar bloque
    self.last_block = to_block

    # 6. Enrichment de tokens ya detectados (dual cycle)
    await self._enrich_detected_tokens()

    # 7. Sleep configurable (1.5s default)
    await asyncio.sleep(self.poll_interval)
```

### Análisis de token nuevo (`_analyze_new_pair`)

```
1. Verificar que no sea WBNB/WETH/stablecoins → skip
2. Verificar que no esté ya analizado → skip
3. ContractAnalyzer.analyze(token_address)
   a. GoPlus Security API
   b. Honeypot.is API
   c. DexScreener API
   d. CoinGecko API
   e. TokenSniffer API
4. Calcular pump_score (PumpAnalyzer v3, 10 componentes)
5. Swap simulation + bytecode analysis (SwapSimulator)
6. Proxy detection + stress test + volatility slippage (SwapSimulator v5)
7. Dev reputation (DevTracker v5 + ML reputation blending)
8. ML pump/dump prediction + anomaly detection (MLPredictor v5)
9. Social sentiment analysis — Twitter/Telegram/Discord/Reddit (SocialSentiment v5)
10. Whale activity analysis (SmartMoneyTracker v5)
11. Risk scoring (RiskEngine v3)
12. 22 capas de seguridad → SAFE / UNSAFE / UNKNOWN
13. Dynamic contract scanner registration (DynamicContractScanner v5)
14. Emit `token_detected` via WebSocket
15. Si Auto-Buy ON + pasa 22 gates → emit `snipe_opportunity`
```

---

## 3. Módulos profesionales

### 3.1 PumpAnalyzer v3 (`pumpAnalyzer.py` — 597 líneas)

Scoring de 0 a 100 basado en 10 componentes ponderados. Cada componente tiene su propio try/except con fallback neutral (40) para evitar que un error en un componente arruine el score total.

#### Componentes

| # | Componente | Peso | Ideal | Descripción |
|---|---|---|---|---|
| 1 | `liquidity` | 14 | $8k-$120k | Liquidez USD del par |
| 2 | `activity` | 15 | Vol/liq ratio 0.3-2.0 | Volumen 24h vs liquidez, ratio buy/sell |
| 3 | `momentum` | 12 | Gradual +5%→+40% | Patrón de precio en timeframes m5/h1/h6/h24 |
| 4 | `mcap` | 12 | $20k-$400k | Market cap sweet spot |
| 5 | `holder` | 10 | 100-2000 holders | Distribución saludable |
| 6 | `whale` | 10 | Top10 holders <40% | Concentración de ballenas |
| 7 | `holder_growth` | 10 | >0.5/min growth | Crecimiento de holders por minuto |
| 8 | `age` | 7 | 1-24 horas | Tokens frescos |
| 9 | `lp_growth` | 6 | LP creciendo vs inicial | Cambio en liquidez |
| 10 | `social` | 4 | Web + redes presentes | Web, CoinGecko listing, socials |

**Total pesos = 100**

#### Cálculo del score

```python
def analyze(self, token_data: dict) -> dict:
    scores = {}
    for name, weight in self.WEIGHTS.items():
        try:
            score_fn = getattr(self, f'_score_{name}')
            scores[name] = score_fn(token_data) * (weight / 100)
        except Exception:
            scores[name] = 40 * (weight / 100)  # Neutral fallback

    total = sum(scores.values())
    # Clamp 0-100
    total = max(0, min(100, total))

    # Grade assignment
    if total >= 80: grade = "HIGH"
    elif total >= 60: grade = "MEDIUM"
    elif total >= 40: grade = "LOW"
    else: grade = "AVOID"

    return {
        'score': round(total),
        'grade': grade,
        'components': scores,
        'stats': self._build_stats(token_data)
    }
```

#### Safety net

Si el top-level `analyze()` falla completamente:

```python
try:
    return self.analyze(token_data)
except Exception:
    return {'score': 0, 'grade': 'UNKNOWN', 'components': {}, 'stats': {}}
```

---

### 3.2 SwapSimulator v5 (`swapSimulator.py` — 1,084 líneas)

Simula compra/venta on-chain vía `eth_call` (sin gastar gas), analiza el bytecode del contrato, y en v5 añade **proxy detection**, **stress testing** y **volatility-aware slippage**.

#### Funciones principales

| Función | Descripción |
|---|---|
| `simulate_swap()` | Simula buy + sell del token vía PancakeSwap V2 Router |
| `analyze_bytecode()` | Lee el bytecode del contrato y busca opcodes peligrosos |
| `full_analysis()` | Ejecuta ambos análisis y devuelve resultado combinado |
| `ProxyDetector.analyze()` | **v5** Detecta proxy upgradeable, implementation slot, admin slot |
| `StressTester.stress_test()` | **v5** Multi-amount stress test (0.01-10 BNB) con tax consistency |
| `VolatilitySlippageCalc.calculate()` | **v5** Slippage dinámico basado en volatilidad (DexScreener) |

#### Bytecode analysis — Opcodes detectados

| Opcode | Hex | Riesgo |
|---|---|---|
| `SELFDESTRUCT` | `0xFF` | 🔴 CRÍTICO — El contrato puede auto-destruirse |
| `DELEGATECALL` | `0xF4` | 🔴 CRÍTICO — Puede ejecutar código externo arbitrario |
| `CREATE` / `CREATE2` | `0xF0` / `0xF5` | 🟡 MEDIO — Puede crear contratos hijos |
| `CALLCODE` | `0xF2` | 🟡 MEDIO — Deprecated, patrón sospechoso |

#### Resultado

```python
{
    'simulation': {
        'can_buy': True,
        'can_sell': True,
        'buy_tax_estimated': 5.2,
        'sell_tax_estimated': 8.1,
        'slippage_impact': 2.3,
    },
    'bytecode': {
        'has_selfdestruct': False,
        'has_delegatecall': True,
        'has_create': False,
        'risk_level': 'MEDIUM',
        'warnings': ['DELEGATECALL detected'],
    }
}
```

#### v5 — Proxy Detection (`ProxyDetector`)

Detecta si un contrato es un proxy upgradeable (potencialmente peligroso):

| Check | Descripción |
|---|---|
| EIP-1967 slots | Busca implementation slot (`0x360894...`) y admin slot (`0xb531...`) |
| Storage slots | Lee storage slot 0/1/2 para detectar implementation address |
| Bytecode patterns | Busca `DELEGATECALL` como indicador de proxy |
| Transparent proxy | Detecta patrón de proxy transparente con admin |

```python
# ProxyAnalysis dataclass
{
    'is_proxy': True,
    'proxy_type': 'EIP-1967 Transparent',
    'implementation_address': '0x...',
    'admin_address': '0x...',
    'risk_level': 'high',  # low/medium/high/critical
    'warnings': ['Implementation can be changed by admin']
}
```

#### v5 — Stress Testing (`StressTester`)

Simula swaps con múltiples montos para detectar tax inconsistencies:

```python
# Montos de prueba: [0.01, 0.05, 0.1, 0.5, 1.0, 5.0, 10.0] BNB
# Para cada monto:
#   1. Simula buy + sell via eth_call
#   2. Calcula buy_tax y sell_tax
#   3. Detecta si tax cambia con el monto (honeypot pattern)

# StressTestResult dataclass
{
    'token_address': '0x...',
    'results': [...],           # Lista de resultados por monto
    'tax_consistent': True,     # ¿Tax es igual para todos los montos?
    'max_buy_tax': 5.2,
    'max_sell_tax': 8.1,
    'risk_level': 'low',
}
```

#### v5 — Volatility-Aware Slippage (`VolatilitySlippageCalc`)

Calcula slippage óptimo basado en la volatilidad real del token:

```python
# Inputs: DexScreener price changes (m5, h1, h6)
# Cálculo: volatility_score (0-100) basado en magnitud de cambios
# recommended_slippage_pct: 5% (baja vol) a 25% (alta vol)

# VolatilitySlippage dataclass
{
    'volatility_score': 45.0,       # 0-100 (más alto = más volátil)
    'recommended_slippage_pct': 12.0,
    'price_change_m5': -2.3,
    'price_change_h1': 15.7,
    'price_change_h6': 42.1,
}
```

---

### 3.3 MempoolService (`mempoolService.py` — 394 líneas)

Escucha transacciones pendientes en el mempool 10-30 segundos antes de que se confirmen.

#### Eventos detectados

| Tipo | Descripción |
|---|---|
| `pending_add_liquidity` | Alguien está por agregar liquidez a un par |
| `pending_remove_liquidity` | ⚠️ Alguien está por REMOVER liquidez |
| `pending_large_swap` | Swap >$1000 detectado |
| `pending_pair_creation` | ¡Nuevo par por crearse! |

#### Filtrado inteligente

- Solo muestra métodos conocidos del Router: `addLiquidity`, `addLiquidityETH`, `removeLiquidity`, `removeLiquidityETH`, `swapExactTokensForETH`, etc.
- Métodos desconocidos se filtran del frontend para no generar spam.
- Conversión HexBytes→hex para evitar output garbled en el UI.

---

### 3.4 RugDetector (`rugDetector.py` — 417 líneas)

Monitoreo post-compra continuo. Mientras tienes un token, verifica en cada ciclo:

| Check | Trigger | Acción |
|---|---|---|
| LP Drain | LP bajó >50% desde compra | 🔴 Alert + Force Sell |
| Tax Increase | Buy/sell tax subió >30% | 🟡 Warning |
| Dev Dump | Deployer vendió >20% de su supply | 🔴 Alert + Force Sell |
| Ownership Change | Owner fue transferido | 🟡 Warning |
| Contract Upgrade | Proxy implementación cambió | 🔴 Alert |

#### Alertas

```python
# Nivel INFO — solo logging
'tax_increased': 'Sell tax increased from 5% to 15%'

# Nivel WARNING — push WebSocket
'dev_selling': 'Deployer sold 25% of supply'

# Nivel CRITICAL — auto-sell si enabled
'lp_drain': 'LP dropped 60% — possible rug pull'
```

---

### 3.5 PreLaunchDetector (`preLaunchDetector.py` — 358 líneas)

Detecta tokens ANTES de que aparezcan en PancakeSwap:

1. Monitorea deployments de contratos nuevos (opcode `CREATE`)
2. Verifica si el contrato es un token ERC-20
3. Busca interacciones con el Router de PancakeSwap
4. Si hay `approve()` al Router → alerta pre-launch

---

### 3.6 SmartMoneyTracker v5 (`smartMoneyTracker.py` — 650+ líneas)

Trackea wallets históricamente rentables y en v5 añade **whale activity analysis**:

| Métrica | Descripción |
|---|---|
| Win Rate | % de tokens comprados que dieron ganancia |
| Avg ROI | ROI promedio por trade |
| Activity | Frecuencia de compras recientes |
| Portfolio Score | Score compuesto de la wallet |
| **Whale Activity** | **v5** Análisis de actividad de ballenas en un token |

Cuando una wallet "smart money" compra un token nuevo → señal de compra positiva para el scoring.

#### v5 — Whale Activity Analysis

Analiza transacciones de ballenas para detectar patrones sospechosos:

```python
# WhaleActivity dataclass
{
    'token_address': '0x...',
    'whale_alerts': [
        WhaleAlert(
            tx_hash='0x...',
            whale_address='0x...',
            action='buy',        # buy/sell/transfer
            amount_usd=50000.0,
            timestamp=1719...,
            is_suspicious=False
        )
    ],
    'total_whale_buys': 3,
    'total_whale_sells': 1,
    'net_whale_flow_usd': 120000.0,  # positivo = más compras
    'whale_concentration': 15.2,      # % del supply en ballenas
    'risk_level': 'low'              # low/medium/high/critical
}
```

| Señal | Riesgo | Descripción |
|---|---|---|
| Net flow negativo | 🟡 MEDIO | Ballenas vendiendo más que comprando |
| Concentración >30% | 🔴 ALTO | Demasiado supply en pocas wallets |
| Dev es whale + venta | 🔴 CRÍTICO | Deployer vendiendo su posición |
| Multiple whale buys correlated | 🟡 MEDIO | Wash trading pattern |

---

### 3.7 DevTracker v5 (`devTracker.py` — 582 líneas)

Analiza la reputación del deployer del contrato. En v5 integra **ML reputation blending** via `MLPredictor.DevReputationML`:

| Señal | Impacto |
|---|---|
| Desplegó 5+ tokens que hicieron rug | 🔴 Serial scammer → BLOCK |
| Tokens previos con sell tax >50% | 🔴 Patrón honeypot |
| 3/5 tokens anteriores exitosos | 🟢 Dev legítimo (+bonus score) |
| Wallet nueva (<1 semana) | 🟡 Sin historial |
| >$100k en balance | 🟢 Skin in the game |
| **ML cluster = "scammer"** | 🔴 **v5** ML detecta patrón de scammer |
| **ML cross_wallet_risk > 0.7** | 🟡 **v5** Wallets vinculadas sospechosas |

#### Scoring

```python
reputation = {
    'address': '0x...',
    'total_tokens_deployed': 8,
    'rug_count': 2,
    'success_count': 4,
    'reputation_score': 58,         # 0-100 (blended con ML si enable_ml=True)
    'is_serial_scammer': False,     # True si rug_count >= 3
    'risk_level': 'MEDIUM',
    'wallet_age_days': 45,
    'ml_reputation_score': 52,      # v5: ML-based score
    'ml_cluster': 'unknown',        # v5: ML cluster (scammer/neutral/legit/unknown)
    'ml_cross_wallet_risk': 0.3,    # v5: Cross-wallet funding risk
}
```

#### v5 — ML Reputation Blending

Cuando `enable_ml=True`, el score final combina el scoring tradicional con ML:

```python
# Traditional score (basado en historial on-chain)
traditional_score = self._calculate_traditional_score(dev_profile)

# ML score (basado en patrones de comportamiento)
ml_result = self._ml_predictor.predict(dev_profile, dev_check_result)

# Blending: 70% tradicional + 30% ML
final_score = int(traditional_score * 0.7 + ml_result.ml_reputation_score * 0.3)

# Señales adicionales de ML
if ml_result.cluster == "scammer":
    signals.append("🔴 ML detecta patrón de scammer")
if ml_result.cross_wallet_risk > 0.7:
    signals.append("🟡 Riesgo de wallets vinculadas")
```

---

### 3.8 RiskEngine v3 (`riskEngine.py` — 444 líneas)

Motor de decisión unificado que combina 7 señales en un score final.

#### Componentes del Risk Score

| Componente | Peso | Fuente |
|---|---|---|
| `security_score` | 25% | GoPlus + Honeypot.is flags |
| `liquidity_score` | 20% | DexScreener LP + lock |
| `pump_score` | 15% | PumpAnalyzer v3 |
| `dev_score` | 15% | DevTracker v3 |
| `simulation_score` | 10% | SwapSimulator results |
| `bytecode_score` | 10% | Opcode analysis |
| `smart_money_score` | 5% | SmartMoneyTracker signals |

#### Hard stops (override el score)

```python
HARD_STOPS = [
    ('is_honeypot', True, 'Honeypot detected'),
    ('has_proxy', True, 'Upgradeable proxy contract'),
    ('is_blacklisted', True, 'Blacklist function detected'),
    ('self_destruct', True, 'SELFDESTRUCT opcode found'),
    ('serial_scammer', True, 'Deployer is serial scammer'),
]
# Si algún hard stop se activa → risk_score = 0, classification = CRITICAL
```

#### Output

```python
{
    'risk_score': 72,               # 0-100 (higher = safer)
    'classification': 'MODERATE',   # SAFE/MODERATE/HIGH/CRITICAL
    'components': { ... },
    'hard_stops_triggered': [],
    'recommendation': 'PROCEED_WITH_CAUTION'
}
```

---

### 3.9 TradeExecutor v3 (`tradeExecutor.py` — 611 líneas)

Ejecución de trades backend-side con private key (no requiere frontend wallet).

#### Features

| Feature | Descripción |
|---|---|
| Multi-RPC | Envía tx a múltiples RPCs en paralelo para minimizar latencia |
| Gas optimization | Calcula gas dinámico basado en gas actual de la red |
| Nonce management | Manejo de nonce con lock para evitar conflictos |
| Slippage calc | `amountOutMin` calculado dinámicamente |
| Deadline | Deadline de 60s para evitar tx colgadas |
| Receipt wait | Espera confirmación y verifica status |

#### Flujo

```
1. Load private key from .env
2. Build swap transaction (PancakeSwap V2 Router)
3. Calculate gas price (fast gas × 1.1)
4. Sign transaction locally
5. Send to 3 RPCs simultaneously
6. Wait for receipt (timeout 60s)
7. Verify tx status == 1
8. Record in metricsService
```

#### Seguridad

- Private key NUNCA se loggea
- Solo se carga de `.env`, no de la base de datos
- Auto-disabilitado si `SNIPER_PRIVATE_KEY` no está configurado

---

### 3.10 ResourceMonitor v4 (`resourceMonitor.py` — 211 líneas)

Monitoreo de recursos del sistema en tiempo real.

#### Métricas

| Métrica | Descripción |
|---|---|
| `cpu_percent` | Uso promedio de CPU |
| `memory_mb` | RAM usada por el proceso |
| `websocket_connections` | Conexiones WS activas |
| `rpc_calls_total` | Total de llamadas RPC acumuladas |
| `rpc_errors_total` | Errores RPC acumulados |
| `rpc_avg_latency_ms` | Latencia promedio de RPCs |
| `tokens_tracked` | Tokens actualmente en tracking |
| `uptime_seconds` | Tiempo desde inicio del bot |

#### API de registro

```python
# Registrar una llamada RPC exitosa
resource_monitor.record_rpc_call(latency_ms=150.0)

# Registrar una llamada RPC fallida
resource_monitor.record_rpc_call(latency_ms=0.0, error=True)

# Registrar conexión/desconexión WS
resource_monitor.record_ws_connect()
resource_monitor.record_ws_disconnect()
```

> **Nota:** El parámetro es `error: bool = False`, NO `success`. Usar `success=False` causa `TypeError`.

---

### 3.11 AlertService v4 (`alertService.py` — 404 líneas)

Sistema de alertas multi-canal con rate limiting.

#### Canales

| Canal | Configuración | Rate Limit |
|---|---|---|
| Telegram | `SNIPER_TELEGRAM_TOKEN` + `CHAT_ID` | 20/min |
| Discord | `SNIPER_DISCORD_WEBHOOK` | 30/min |
| Email (SMTP) | `SNIPER_EMAIL_FROM/TO/PASSWORD` | 5/min |
| Log File | `logs/sniper_alerts.log` | Sin límite |

#### Tipos de evento

| Evento | Canales | Descripción |
|---|---|---|
| `token_detected` | Telegram, Discord | Nuevo token encontrado |
| `snipe_opportunity` | Telegram, Discord, Email | Token pasa todas las gates |
| `buy_executed` | Todos | Compra ejecutada |
| `sell_executed` | Todos | Venta ejecutada (TP/SL) |
| `rug_alert` | Todos + **urgent** | Posible rug pull detectado |
| `system_error` | Log, Email | Error del sistema |

#### Rate limiting

```python
# Ventana deslizante de 60 segundos
# Si se excede el límite, el mensaje se descarta con warning en log
```

---

### 3.12 MetricsService v4 (`metricsService.py` — 329 líneas)

Tracking de rendimiento del bot.

#### Métricas trackeadas

| Métrica | Descripción |
|---|---|
| Total trades | Compras + ventas ejecutadas |
| Win rate | % de trades con ganancia |
| Total P&L | Ganancia/pérdida total en USD |
| Avg ROI | ROI promedio por trade |
| Best trade | Mayor ganancia en un trade |
| Worst trade | Mayor pérdida en un trade |
| Detection speed | Promedio ms desde PairCreated hasta análisis completo |
| Hourly series | Datos por hora para gráficas |

#### Output (snapshot)

```python
{
    'total_trades': 47,
    'wins': 28,
    'losses': 19,
    'win_rate': 59.6,
    'total_pnl_usd': 342.50,
    'avg_roi_percent': 12.3,
    'best_trade_roi': 156.0,
    'worst_trade_roi': -20.0,
    'avg_detection_ms': 1850,
    'hourly_pnl': [...]
}
```

---

### 3.13 MLPredictor v5 (`mlPredictor.py` — 621 líneas)

Módulo de Machine Learning con 3 sub-modelos para predicción avanzada.

#### 3.13.1 PumpDumpPredictor

Predice la probabilidad de pump o dump basado en features on-chain:

```python
# Features extraídas:
# - liquidity_usd, holder_count, buy_sell_ratio
# - top_holder_pct, is_verified, buy_tax, sell_tax
# - has_proxy, has_mint, lp_lock_pct
# - smart_money_buys, smart_money_sells
# - holder_growth_rate

# PumpDumpPrediction dataclass
{
    'token_address': '0x...',
    'pump_probability': 0.72,   # 0.0-1.0
    'dump_probability': 0.28,   # 0.0-1.0
    'ml_score': 72,             # 0-100 (pump_probability * 100)
    'confidence': 0.85,         # 0.0-1.0
    'features_used': 14,
    'model_version': '1.0'
}
```

| ml_score | Label derivado | Significado |
|---|---|---|
| ≥ 70 | SAFE | Alta probabilidad de pump saludable |
| 40-69 | NEUTRAL | Señales mixtas |
| 20-39 | WARNING | Señales de precaución |
| < 20 | DANGER | Alta probabilidad de dump/scam |

#### 3.13.2 DevReputationML

Analiza patrones de comportamiento del deployer con ML:

```python
# DevReputationPrediction dataclass
{
    'wallet_address': '0x...',
    'ml_reputation_score': 52,  # 0-100
    'cluster': 'unknown',       # scammer/neutral/legit/unknown
    'cross_wallet_risk': 0.3,   # 0.0-1.0 (riesgo de wallets vinculadas)
}
```

#### 3.13.3 AnomalyDetector

Detecta anomalías en el comportamiento del token:

```python
# AnomalyResult dataclass
{
    'token_address': '0x...',
    'is_anomalous': True,       # True si anomalies ≥ 2 o total_score > 0.5
    'anomaly_type': 'volume_spike',  # Tipo principal de anomalía
    'total_score': 0.65,        # 0.0-1.0 (score compuesto)
    'anomalies': [              # Lista de anomalías detectadas
        {'type': 'volume_spike', 'score': 0.4, 'details': '...'},
        {'type': 'buy_sell_ratio_anomaly', 'score': 0.25, 'details': '...'}
    ]
}
```

| Anomalía | Trigger | Score |
|---|---|---|
| `volume_spike` | Volumen > 5× liquidez | 0.4 |
| `buy_sell_ratio_anomaly` | Ratio buy/sell > 10 o < 0.1 | 0.25 |
| `holder_concentration` | Top holder > 50% | 0.35 |
| `price_manipulation` | Cambio m5 > 50% o < -30% | 0.3 |

---

### 3.14 SocialSentimentAnalyzer v5 (`socialSentiment.py` — 604 líneas)

Analiza sentimiento social en 4 plataformas: Twitter, Telegram, Discord y Reddit.

```python
# SocialSentimentResult dataclass
{
    'token_address': '0x...',
    'sentiment_score': 50,      # 0-100 (50 = neutral)
    'sentiment_label': 'neutral', # positive/neutral/negative/unknown
    'mentions_count': 23,
    'platforms_analyzed': 4,
    'platform_scores': {
        'twitter': 65,
        'telegram': 42,
        'discord': 55,
        'reddit': 38
    },
    'trending': False,          # True si mentions > threshold
    'warnings': []              # Alertas de sentimiento
}
```

| sentiment_score | Label | Significado |
|---|---|---|
| ≥ 70 | positive | Sentimiento muy positivo — posible hype |
| 40-69 | neutral | Sentimiento mixto o insuficiente data |
| < 40 | negative | Sentimiento negativo — precaución |

#### Keyword detection

Busca keywords positivos y negativos por plataforma:
- **Positivos**: "moon", "gem", "100x", "bullish", "pump", "airdrop"
- **Negativos**: "scam", "rug", "honeypot", "dump", "fake", "avoid"

---

### 3.15 DynamicContractScanner v5 (`dynamicContractScanner.py` — 512 líneas)

Escaneo continuo en background de contratos registrados con scoring dinámico.

#### Flujo

```
1. register(token_address) → agrega a lista de monitoreo
2. Cada 30s: toma snapshot del contrato
   a. Lee total_supply, owner, balances
   b. Compara con snapshot anterior
   c. Detecta cambios sospechosos
3. Si cambio peligroso → block(token) + emit alerta
4. unregister(token) cuando se vende o expira
```

#### ContractSnapshot

```python
{
    'token_address': '0x...',
    'total_supply': 1000000000,
    'owner': '0x...',
    'top_holder_pct': 15.2,
    'timestamp': 1719...,
    'score': 75,                    # Score dinámico 0-100
    'changes_detected': [],
    'risk_level': 'low'
}
```

#### Cambios monitoreados

| Cambio | Severidad | Acción |
|---|---|---|
| Owner cambió | 🟡 MEDIO | Warning en WebSocket |
| Total supply aumentó >10% | 🔴 ALTO | Block token + alerta |
| Top holder concentración subió | 🟡 MEDIO | Reduce score |
| Contrato se auto-destruyó | 🔴 CRÍTICO | Emergency sell + block |
| Implementation cambió (proxy) | 🔴 ALTO | Block + alerta |

#### API

```python
# Registrar token para monitoreo continuo
await scanner.register(token_address, symbol="TOKEN", pair_address="0x...", is_proxy=False)

# Verificar si token está bloqueado
is_blocked, reason = scanner.is_blocked(token_address)

# Des-registrar token
scanner.unregister(token_address)
```

---

### 3.16 Nuevas Safety Gates v5

Las 22 capas de protección incluyen 4 gates adicionales en v5:

| # | Gate | Módulo | Trigger de bloqueo |
|---|---|---|---|
| 19 | ML Pump/Dump | MLPredictor | ml_score < 20 (DANGER) |
| 20 | Anomaly Detection | MLPredictor | is_anomalous = True con score > 0.7 |
| 21 | Social Sentiment | SocialSentiment | sentiment_score < 25 (muy negativo) |
| 22 | Dynamic Scanner | DynContractScanner | is_blocked = True |

Estas gates se evalúan DESPUÉS de las 18 gates originales y ANTES de emitir `snipe_opportunity`.

---

## 4. APIs de seguridad

### 4.1 GoPlus Security API

```
GET https://api.gopluslabs.io/api/v1/token_security/{chain_id}?contract_addresses={token}
```

#### Flags verificados

| Flag | Valor peligroso | Descripción |
|---|---|---|
| `is_honeypot` | `1` | No se puede vender |
| `is_proxy` | `1` | Contrato upgradeable |
| `is_open_source` | `0` | Código no verificado |
| `can_take_back_ownership` | `1` | Fake renounce |
| `hidden_owner` | `1` | Owner oculto |
| `is_blacklisted` | `1` | Usa blacklist |
| `is_mintable` | `1` | Puede crear tokens infinitos |
| `external_call` | `1` | Llama contratos externos |
| `transfer_pausable` | `1` | Puede pausar transfers |
| `trading_cooldown` | `1` | Cooldown entre trades |
| `is_anti_whale` | `1` | Límite de compra |
| `cannot_sell_all` | `1` | No permite vender 100% |
| `owner_change_balance` | `1` | Owner puede modificar balances |
| `selfdestruct` | `1` | Puede auto-destruirse |
| `buy_tax` | String | Tax de compra (%) |
| `sell_tax` | String | Tax de venta (%) |
| `lp_holder_count` | String | Cantidad de LP holders |
| `holder_count` | String | Cantidad de holders |

### 4.2 Honeypot.is API

```
GET https://api.honeypot.is/v2/IsHoneypot?address={token}&chainId={chain_id}
```

Simula una compra y venta real para determinar:
- `simulationSuccess` — ¿La simulación funcionó?
- `buyTax` / `sellTax` — Tax real medido
- `isHoneypot` — ¿Es imposible vender?
- `buyGas` / `sellGas` — Gas requerido

### 4.3 DexScreener API

```
GET https://api.dexscreener.com/latest/dex/tokens/{token}
```

Retorna datos de mercado en tiempo real:
- Liquidez USD
- Volumen 24h
- Precio actual + cambios (m5, h1, h6, h24)
- Cantidad de buys/sells
- Market cap (si disponible)
- Metadata: nombre, símbolo, website, socials

### 4.4 CoinGecko API

```
GET https://api.coingecko.com/api/v3/coins/{chain}/contract/{token}
```

Verifica si el token está listado legítimamente y provee links sociales.

### 4.5 TokenSniffer API

```
GET https://tokensniffer.com/api/v2/tokens/{chain_id}/{token}
```

Retorna un score general (0-100) y detección de patrones de scam conocidos.

---

## 5. Sistema de scoring

### 5.1 Pump Score (0-100)

Calculado por `PumpAnalyzer.analyze()`. Ver sección 3.1 para detalle de los 10 componentes.

| Grade | Rango | Significado |
|---|---|---|
| HIGH | 80-100 | Alta probabilidad de pump. ¡Oportunidad! |
| MEDIUM | 60-79 | Potencial moderado. Proceder con cautela |
| LOW | 40-59 | Bajo potencial. Probablemente no vale la pena |
| AVOID | 0-39 | Token muerto, honeypot, o scam. NO comprar |

### 5.2 Risk Score (0-100)

Calculado por `RiskEngine.analyze()`. Ver sección 3.8.

| Classification | Rango | Acción |
|---|---|---|
| SAFE | 80-100 | Auto-buy si enabled |
| MODERATE | 50-79 | Auto-buy con cautela |
| HIGH | 20-49 | No auto-buy, solo alerta |
| CRITICAL | 0-19 | Bloqueado, hard stop activado |

### 5.3 Safety Assessment

La clasificación final SAFE/UNSAFE/UNKNOWN se basa en las 22 capas:

```python
# SAFE si:
# - Código verificado (is_open_source = 1)
# - NO es honeypot
# - NO es proxy
# - NO tiene hidden owner
# - NO tiene fake renounce
# - Buy tax ≤ max_buy_tax
# - Sell tax ≤ max_sell_tax
# - LP lock ≥ 80% y ≥ 24h
# - Top holder < 30%
# - Creator holding < 20%
# - Pump score ≥ min_pump_score (default 40)
# - No hard stops de RiskEngine
# - Swap simulation exitosa
# - No SELFDESTRUCT/DELEGATECALL en bytecode
# - Proxy analysis risk_level != "critical"           (v5)
# - Stress test tax_consistent = True                 (v5)
# - ML pump/dump ml_score >= 20                       (v5)
# - Anomaly detector is_anomalous = False             (v5)
# - Social sentiment_score >= 25                      (v5)
# - Dynamic scanner is_blocked = False                (v5)
# - Whale activity risk_level != "critical"           (v5)
# - Volatility slippage applied correctly             (v5)

# UNSAFE si falla alguna condición crítica
# UNKNOWN si no hay suficiente data
```

---

## 6. Ejecución de trades

### 6.1 Frontend (ethers.js)

Cuando el usuario hace clic en "Buy" o el bot emite `snipe_opportunity`:

```javascript
// pageSniper.js
async executeBuy(token) {
    const router = new ethers.Contract(ROUTER_ADDRESS, ROUTER_ABI, signer);
    const path   = [WBNB_ADDRESS, token.address];
    const amount = ethers.parseEther(buyAmount);
    const deadline = Math.floor(Date.now() / 1000) + 60;

    const tx = await router.swapExactETHForTokensSupportingFeeOnTransferTokens(
        0,           // amountOutMin (calculado con slippage)
        path,
        userAddress,
        deadline,
        { value: amount }
    );
    await tx.wait();
}
```

### 6.2 Backend (TradeExecutor)

Si `enable_trade_executor = True` y `SNIPER_PRIVATE_KEY` está configurado:

```python
# tradeExecutor.py
async def execute_buy(self, token_address, amount_eth):
    tx = self.router.functions.swapExactETHForTokensSupportingFeeOnTransferTokens(
        amount_out_min, path, self.wallet_address, deadline
    ).build_transaction({
        'from': self.wallet_address,
        'value': amount_wei,
        'gas': estimated_gas,
        'gasPrice': fast_gas_price,
        'nonce': self.get_next_nonce(),
    })
    signed = self.w3.eth.account.sign_transaction(tx, self.private_key)
    tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
```

### 6.3 Auto-Sell (TP/SL/Time)

```python
# Verificado en cada ciclo de enrichment
if current_price >= buy_price * (1 + take_profit / 100):
    # Take Profit → SELL
elif current_price <= buy_price * (1 - stop_loss / 100):
    # Stop Loss → SELL
elif hours_held >= max_hold_hours - 1:
    # Time limit → SELL (1h antes de expire)
```

---

## 7. WebSocket events

### Frontend → Backend (via `sniperConsumer.py`)

| Evento | Payload | Acción |
|---|---|---|
| `start_sniper` | `{chain, settings, modules}` | Inicia el bot con config del usuario |
| `stop_sniper` | `{}` | Detiene el bot |
| `update_settings` | `{settings}` | Actualiza settings sin restart |
| `request_status` | `{}` | Solicita estado actual |
| `request_token_list` | `{}` | Solicita lista de tokens detectados |
| `execute_buy` | `{token_address, amount}` | Ejecutar compra vía backend |
| `execute_sell` | `{token_address, amount}` | Ejecutar venta vía backend |
| `dismiss_token` | `{token_address}` | Eliminar token de la UI |

### Backend → Frontend

| Evento | Payload | Trigger |
|---|---|---|
| `status_update` | `{state, blocks, tokens, rpc_url}` | Cada ciclo de scan |
| `token_detected` | `{token_data completo}` | Nuevo token encontrado |
| `token_updated` | `{token_address, updated_fields}` | Enrichment cambió datos |
| `snipe_opportunity` | `{token_data, reason}` | Token pasa 22 gates |
| `mempool_event` | `{type, from, to, value}` | Evento mempool relevante |
| `error` | `{message}` | Error del bot |
| `resource_metrics` | `{cpu, ram, rpc, ws}` | Cada 30s |
| `performance_metrics` | `{trades, pnl, win_rate}` | Cada 60s |

---

## 8. Enrichment & anti-spam

### Ciclo dual de enrichment

El bot re-analiza tokens ya detectados con dos velocidades:

#### Fast cycle (~3s) — APIs fallidas

```python
for token in detected_tokens:
    if token.age < timedelta(minutes=5):
        # Solo re-intenta APIs que fallaron (status None/error)
        if not token.goplus_data:
            await self._fetch_goplus(token)
        if not token.honeypot_data:
            await self._fetch_honeypot(token)
        # etc.
```

#### Slow cycle (~15s) — Refresh DexScreener

```python
for token in detected_tokens:
    if token.age < timedelta(minutes=10):
        # Refresca liquidez, volumen, precio
        await self._fetch_dexscreener(token)
```

### Límites de tiempo

| Fase | Edad máxima | Descripción |
|---|---|---|
| Fast refresh | < 5 min | Re-intenta APIs fallidas |
| Full refresh | < 10 min | Refresca DexScreener |
| > 10 min | Stop | Token se congela, no más updates |

### Change detection

Antes de emitir `token_updated`:

```python
changes = {}
if old_liquidity != new_liquidity:
    changes['liquidity'] = new_liquidity
if old_risk_level != new_risk_level:
    changes['risk_level'] = new_risk_level
# ... check all fields

if changes:  # Solo emitir si algo cambió
    self._emit('token_updated', {
        'token_address': token.address,
        **changes
    })
```

Esto evita que el frontend reciba cientos de updates idénticos (anti-spam).

---

## 9. Resiliencia RPC

### Multi-endpoint rotation

```python
RPC_FALLBACKS = {
    56: [  # BSC — 10 endpoints
        'https://bsc-rpc.publicnode.com',
        'https://bsc.llamarpc.com',
        'https://bsc.nodies.app',
        'https://bsc.meowrpc.com',
        'https://bsc.drpc.org',
        'https://bsc-dataseed1.binance.org',
        'https://bsc-dataseed2.binance.org',
        'https://bsc-dataseed3.binance.org',
        'https://bsc-dataseed4.binance.org',
        'https://bsc-dataseed.bnbchain.org',
    ],
    1: [  # Ethereum — 5 endpoints
        'https://ethereum-rpc.publicnode.com',
        'https://eth.llamarpc.com',
        'https://eth.drpc.org',
        'https://eth.meowrpc.com',
        'https://rpc.ankr.com/eth',
    ]
}
```

### Safe get block number

```python
def _safe_get_block_number(self):
    """Obtiene bloque actual con triple fallback."""
    for attempt in range(3):
        try:
            return self.w3.eth.block_number
        except Exception:
            self._rotate_rpc()  # Cambia al siguiente endpoint
            time.sleep(2 ** attempt)  # Backoff: 1s, 2s, 4s
    return self.last_block  # Fallback: mantener bloque actual
```

### Exponential backoff

```python
BACKOFF_BASE  = 2.0   # Base del backoff
BACKOFF_MAX   = 30.0  # Máximo delay
BACKOFF_DECAY = 0.8   # Factor de decay entre intentos exitosos

# Backoff por endpoint individual
endpoint_delays = {url: 0.0 for url in rpcs}

# Al fallar:
endpoint_delays[url] = min(delay * BACKOFF_BASE, BACKOFF_MAX)

# Al tener éxito:
endpoint_delays[url] *= BACKOFF_DECAY
```

### RPC retry para operaciones críticas

```python
async def _get_pair_liquidity(self, pair_address):
    """3 intentos con rotación RPC."""
    for attempt in range(3):
        try:
            reserves = pair_contract.functions.getReserves().call()
            # Guard: verificar native_price > 0
            if self.native_price_usd <= 0:
                self.native_price_usd = await self._fetch_native_price()
            return reserves[0] * self.native_price_usd * 2 / 1e18
        except Exception:
            self._rotate_rpc()
            await asyncio.sleep(1)
    return 0.0
```

---

## 10. Frontend dedup

### Token table — upsert

```javascript
// pageSniper.js — addAllDetected()
function addAllDetected(tokenData) {
    const idx = allDetected.findIndex(t => t.address === tokenData.address);
    if (idx >= 0) {
        allDetected[idx] = { ...allDetected[idx], ...tokenData };  // UPDATE
    } else {
        allDetected.push(tokenData);  // INSERT
    }
    renderTable();
}
```

### Feed cards — in-place update

```javascript
// token_detected handler
const existing = document.querySelector(`.feed-card[data-token="${token.address}"]`);
if (existing) {
    // Update card in-place
    existing.querySelector('.feed-safety').textContent = token.safety;
    existing.querySelector('.feed-liq').textContent = formatUSD(token.liquidity);
    // ... update all fields
} else {
    // Create new card
    feedContainer.prepend(createFeedCard(token));
}
```

Esto evita:
- Filas duplicadas en la tabla de tokens
- Cards duplicadas en el feed de actividad
- Scroll jumps al recibir updates

---

## 11. Configuración avanzada

### Constantes en `sniperService.py`

```python
# Factories monitoreados
PANCAKE_V2_FACTORY = '0xcA143Ce32Fe78f1f7019d7d551a6402fC5350c73'
UNISWAP_V2_FACTORY = '0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f'

# Topic del evento PairCreated(address,address,address,uint256)
PAIR_CREATED_TOPIC = '0x0d3648bd0f6ba80134a33ba9275ac585d9d315f0ad8355cddefde31afa28d0e9'

# Tokens nativos wrapped
WBNB = '0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c'
WETH = '0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2'

# Stablecoins (excluidos del scanner)
STABLECOINS = {'0x55d398326f99059fF775485246999027B3197955', ...}  # USDT, USDC, BUSD, DAI
```

### Timeouts por API

| API | Timeout | Retries |
|---|---|---|
| GoPlus | 10s | 1 |
| Honeypot.is | 15s | 1 |
| DexScreener | 10s | 2 |
| CoinGecko | 10s | 1 |
| TokenSniffer | 10s | 1 |
| RPC call | 30s | 3 (con rotación) |

---

## 12. Tests

```bash
python manage.py test trading.tests -v 2
```

### 304 tests en 9 archivos

| Archivo | Tests | Cobertura |
|---|---|---|
| `test_v6_modules.py` | 138 | **v6** MEV protector, copy trader, multi-DEX, AI optimizer, backtest engine |
| `test_v5_modules.py` | 36 | **v5** ML predictor, social sentiment, proxy, stress, volatility, anomaly, scanner |
| `test_sniperService.py` | 36 | Bot init, settings, state management, chains |
| `test_alertService.py` | 27 | Events, rate limiting, send, formatting |
| `test_devTracker.py` | 16 | Reputation, serial scammer, scoring, ML blending |
| `test_resourceMonitor.py` | 16 | CPU, WS, RPC tracking, snapshots |
| `test_pumpAnalyzer.py` | 15 | 10 components, grades, stats |
| `test_riskEngine.py` | 14 | Weights, hard stops, classification |
| `test_rugDetector.py` | 6 | Alert levels, trigger conditions |

**Total: 304 tests — OK (~1.7s)**

---

## 13. Troubleshooting

### Score siempre 0

- **Causa:** Todos los componentes fallaban con datos vacíos
- **Fix:** Cada componente tiene try/except con fallback neutral (40)
- **Verificar:** Score debería ser ~40 para tokens sin datos

### Tokens con liquidez 0 USD

- **Causa 1:** Token realmente no tiene liquidez
- **Causa 2:** BNB/ETH price es 0 → se necesita re-fetch de Binance
- **Causa 3:** RPC falló en `getReserves()` → retry con rotación (3 intentos)
- **Fix:** Guard `native_price` + RPC retry implementados

### Tokens duplicados en la tabla

- **Causa:** `addAllDetected` hacía push siempre
- **Fix:** Ahora hace upsert por `token_address`

### `token_updated` spam

- **Causa:** Emitía update aunque nada hubiera cambiado
- **Fix:** Change detection antes de emitir + time limits (5min/10min)

### `record_rpc_call() got an unexpected keyword argument 'success'`

- **Causa:** Se llamaba con `success=False` pero el parámetro es `error=True`
- **Fix:** Cambiado a `error=True` en `sniperService.py`
- **Impacto:** Sin el fix, el error hacía que `last_block = to_block` nunca se ejecutara, re-escaneando los mismos bloques infinitamente

### Mempool muestra caracteres garbled

- **Causa:** `HexBytes` se serializaba con bytes crudos
- **Fix:** Conversión `.hex()` antes de enviar al frontend

---

*Última actualización: Junio 2025 — v5 (16 módulos, 166 tests, 22 capas, ML + Social + Dynamic Scanner)*

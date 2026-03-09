# ─── Sniper Bot — Instalación automática (Windows) ──────────
# Ejecutar: powershell -ExecutionPolicy Bypass -File install.ps1
# ────────────────────────────────────────────────────────────

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "==================================================" -ForegroundColor Cyan
Write-Host "  Sniper Bot — Instalador (Windows)" -ForegroundColor Cyan
Write-Host "==================================================" -ForegroundColor Cyan
Write-Host ""

# ── 1. Detectar Python ──
$python = $null
foreach ($cmd in @("python", "python3", "py")) {
    try {
        $ver = & $cmd --version 2>&1
        if ($ver -match "Python 3\.\d+") {
            $python = $cmd
            break
        }
    } catch {}
}

if (-not $python) {
    Write-Host "ERROR: Python 3.10+ no encontrado. Instalalo desde https://python.org" -ForegroundColor Red
    exit 1
}

$pyVer = & $python --version 2>&1
Write-Host "[1/5] Python detectado: $pyVer" -ForegroundColor Green

# ── 2. Crear entorno virtual ──
if (-not (Test-Path "venv")) {
    Write-Host "[2/5] Creando entorno virtual..." -ForegroundColor Yellow
    & $python -m venv venv
} else {
    Write-Host "[2/5] Entorno virtual ya existe." -ForegroundColor Green
}

# Activar venv
& .\venv\Scripts\Activate.ps1

# ── 3. Instalar dependencias ──
Write-Host "[3/5] Instalando dependencias..." -ForegroundColor Yellow
pip install --upgrade pip -q
pip install -r requirements.txt -q
Write-Host "     Dependencias instaladas." -ForegroundColor Green

# ── 4. Crear carpetas ──
Write-Host "[4/5] Creando carpetas de logs y cache..." -ForegroundColor Yellow
New-Item -ItemType Directory -Path "logs" -Force | Out-Null
New-Item -ItemType Directory -Path "cache" -Force | Out-Null

# ── 5. Configuracion .env ──
if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "[5/5] Se ha creado .env a partir de .env.example" -ForegroundColor Green
    Write-Host ""
    Write-Host "  >>> IMPORTANTE: Edita .env con tus claves antes de ejecutar <<<" -ForegroundColor Red
    Write-Host "      notepad .env" -ForegroundColor Yellow
} else {
    Write-Host "[5/5] .env ya existe — no se sobrescribe." -ForegroundColor Green
}

# ── Migraciones ──
Write-Host ""
Write-Host "Ejecutando migraciones..." -ForegroundColor Yellow
python manage.py migrate --noinput

Write-Host ""
Write-Host "==================================================" -ForegroundColor Cyan
Write-Host "  Instalacion completada" -ForegroundColor Green
Write-Host ""
Write-Host "  Siguiente paso:" -ForegroundColor White
Write-Host "    1. Edita .env con tus claves" -ForegroundColor White
Write-Host "    2. Ejecuta: .\run_bot.ps1" -ForegroundColor White
Write-Host "==================================================" -ForegroundColor Cyan

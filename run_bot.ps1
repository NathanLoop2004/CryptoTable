# ─── Sniper Bot — Script de ejecución (Windows) ────────────
# Ejecutar: powershell -ExecutionPolicy Bypass -File run_bot.ps1
# ────────────────────────────────────────────────────────────

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "==================================================" -ForegroundColor Cyan
Write-Host "  Sniper Bot — Iniciando" -ForegroundColor Cyan
Write-Host "==================================================" -ForegroundColor Cyan
Write-Host ""

# Verificar .env
if (-not (Test-Path ".env")) {
    Write-Host "ERROR: No se encontro .env" -ForegroundColor Red
    Write-Host "Ejecuta primero: .\install.ps1" -ForegroundColor Yellow
    exit 1
}

# Activar venv
if (Test-Path ".\venv\Scripts\Activate.ps1") {
    & .\venv\Scripts\Activate.ps1
} else {
    Write-Host "ERROR: No se encontro entorno virtual." -ForegroundColor Red
    Write-Host "Ejecuta primero: .\install.ps1" -ForegroundColor Yellow
    exit 1
}

# Migraciones pendientes
Write-Host "[1/3] Aplicando migraciones..." -ForegroundColor Yellow
python manage.py migrate --noinput 2>$null

# Recoger archivos estaticos
Write-Host "[2/3] Recogiendo archivos estaticos..." -ForegroundColor Yellow
python manage.py collectstatic --noinput 2>$null

# Iniciar Daphne
Write-Host "[3/3] Iniciando servidor Daphne en http://0.0.0.0:8000" -ForegroundColor Green
Write-Host ""
Write-Host "  Dashboard:  http://localhost:8000" -ForegroundColor White
Write-Host "  WebSocket:  ws://localhost:8000/ws/sniper/" -ForegroundColor White
Write-Host ""
Write-Host "  Presiona Ctrl+C para detener." -ForegroundColor Yellow
Write-Host "==================================================" -ForegroundColor Cyan
Write-Host ""

python -m daphne -b 0.0.0.0 -p 8000 TradingWeb.asgi:application

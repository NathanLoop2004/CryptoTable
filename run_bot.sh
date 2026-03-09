#!/usr/bin/env bash
# ─── Sniper Bot — Script de ejecución (Linux/Mac) ──────────
set -e

echo "══════════════════════════════════════════════════"
echo "  Sniper Bot — Iniciando"
echo "══════════════════════════════════════════════════"
echo ""

# Verificar .env
if [ ! -f .env ]; then
    echo "ERROR: No se encontró .env"
    echo "Ejecuta primero: ./install.sh"
    exit 1
fi

# Activar venv
if [ -d "venv" ]; then
    source venv/bin/activate
else
    echo "ERROR: No se encontró entorno virtual."
    echo "Ejecuta primero: ./install.sh"
    exit 1
fi

# Migraciones pendientes
echo "[1/3] Aplicando migraciones..."
python manage.py migrate --noinput 2>/dev/null

# Recoger archivos estáticos
echo "[2/3] Recogiendo archivos estáticos..."
python manage.py collectstatic --noinput 2>/dev/null || true

# Iniciar Daphne (ASGI — HTTP + WebSocket)
echo "[3/3] Iniciando servidor Daphne en http://0.0.0.0:8000"
echo ""
echo "  Dashboard:  http://localhost:8000"
echo "  WebSocket:  ws://localhost:8000/ws/sniper/"
echo ""
echo "  Presiona Ctrl+C para detener."
echo "══════════════════════════════════════════════════"
echo ""

daphne -b 0.0.0.0 -p 8000 TradingWeb.asgi:application

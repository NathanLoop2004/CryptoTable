#!/usr/bin/env bash
# ─── Sniper Bot — Instalación automática (Linux/Mac) ───────
set -e

echo "══════════════════════════════════════════════════"
echo "  Sniper Bot — Instalador"
echo "══════════════════════════════════════════════════"
echo ""

# ── 1. Detectar Python ──
PYTHON=""
for cmd in python3.12 python3.11 python3 python; do
    if command -v "$cmd" &>/dev/null; then
        PYTHON="$cmd"
        break
    fi
done

if [ -z "$PYTHON" ]; then
    echo "ERROR: Python 3.10+ no encontrado. Instálalo primero."
    exit 1
fi

PY_VER=$($PYTHON --version 2>&1)
echo "[1/5] Python detectado: $PY_VER"

# ── 2. Crear entorno virtual ──
if [ ! -d "venv" ]; then
    echo "[2/5] Creando entorno virtual..."
    $PYTHON -m venv venv
else
    echo "[2/5] Entorno virtual ya existe."
fi

source venv/bin/activate

# ── 3. Instalar dependencias ──
echo "[3/5] Instalando dependencias..."
pip install --upgrade pip -q
pip install -r requirements.txt -q
echo "     Dependencias instaladas."

# ── 4. Crear carpetas ──
echo "[4/5] Creando carpetas de logs y cache..."
mkdir -p logs cache

# ── 5. Configuración .env ──
if [ ! -f .env ]; then
    cp .env.example .env
    echo "[5/5] Se ha creado .env a partir de .env.example"
    echo ""
    echo "  >>> IMPORTANTE: Edita .env con tus claves antes de ejecutar <<<"
    echo "      nano .env"
else
    echo "[5/5] .env ya existe — no se sobrescribe."
fi

# ── Migrar base de datos ──
echo ""
echo "Ejecutando migraciones..."
python manage.py migrate --noinput

echo ""
echo "══════════════════════════════════════════════════"
echo "  Instalación completada"
echo ""
echo "  Siguiente paso:"
echo "    1. Edita .env con tus claves"
echo "    2. Ejecuta: ./run_bot.sh"
echo "══════════════════════════════════════════════════"

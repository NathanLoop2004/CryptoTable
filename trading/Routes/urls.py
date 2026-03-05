"""
urls.py - Archivo principal de rutas de TradingWeb.
Equivalente al app.use() de Express.js.
Cada módulo tiene su propio Router y aquí solo se hace el include con su prefijo.
"""

from django.urls import path, include
from django.http import JsonResponse


def health(request):
    """Simple health-check endpoint."""
    return JsonResponse({"status": True, "message": "OK"})


urlpatterns = [
    # ─── Health check ─────────────────────────────────────────────
    path('health/', health, name='api-health'),

    # ─── API Routers (equivalente a app.use('/api/xxx', xxxRouter)) ─
    path('wallet/', include('trading.Routes.walletRouter')),
]

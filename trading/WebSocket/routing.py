"""
routing.py - Rutas WebSocket de TradingWeb.
Equivalente a definir los endpoints ws:// del servidor.
"""

from django.urls import re_path

from trading.WebSocket.binanceConsumer import BinanceConsumer
from trading.WebSocket.walletConsumer import WalletConsumer
from trading.WebSocket.sniperConsumer import SniperConsumer

websocket_urlpatterns = [
    re_path(r"ws/binance/$", BinanceConsumer.as_asgi()),
    re_path(r"ws/wallet/$", WalletConsumer.as_asgi()),
    re_path(r"ws/sniper/$", SniperConsumer.as_asgi()),
]

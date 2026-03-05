"""
walletRouter.py - Rutas para autenticación de wallets.
Equivalente a un express.Router() individual.
"""

from django.urls import path
from django.views.decorators.csrf import csrf_exempt

from trading.Controllers.walletController import WalletController

urlpatterns = [
    # POST /api/wallet/nonce/
    path('nonce/', csrf_exempt(WalletController.nonce), name='api-wallet-nonce'),
    # POST /api/wallet/verify/
    path('verify/', csrf_exempt(WalletController.verify), name='api-wallet-verify'),
    # POST /api/wallet/disconnect/
    path('disconnect/', csrf_exempt(WalletController.disconnect), name='api-wallet-disconnect'),
    # GET  /api/wallet/status/?address=0x…
    path('status/', WalletController.status, name='api-wallet-status'),
]

"""
walletController.py - Controlador para autenticación de wallets.
Estructura de clase con métodos estáticos (estilo Express.js).
El controlador recibe la request, valida input básico, y delega al Service.
"""

import json

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from trading.Services import walletService


class WalletController:

    # ------------------------------------------------------------------ #
    #  Helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _json_body(request) -> dict | None:
        try:
            return json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return None

    @staticmethod
    def _bad(msg: str, status: int = 400) -> JsonResponse:
        return JsonResponse({"status": False, "message": msg}, status=status)

    # ------------------------------------------------------------------ #
    #  POST /api/wallet/nonce/
    # ------------------------------------------------------------------ #

    @staticmethod
    @csrf_exempt
    def nonce(request):
        """POST: Genera un nonce para que el usuario firme en su wallet."""
        try:
            body = WalletController._json_body(request)
            if not body:
                return WalletController._bad("Invalid JSON body.")

            address = walletService.validate_address(body.get("address", ""))
            if not address:
                return WalletController._bad("Invalid Ethereum address.")

            provider = body.get("provider", "trust_wallet")
            result = walletService.generate_nonce(address, provider)

            return JsonResponse({"status": True, "data": result})
        except Exception as e:
            return WalletController._bad(str(e), 500)

    # ------------------------------------------------------------------ #
    #  POST /api/wallet/verify/
    # ------------------------------------------------------------------ #

    @staticmethod
    @csrf_exempt
    def verify(request):
        """POST: Verifica la firma del nonce y activa la sesión del wallet."""
        try:
            body = WalletController._json_body(request)
            if not body:
                return WalletController._bad("Invalid JSON body.")

            address = walletService.validate_address(body.get("address", ""))
            if not address:
                return WalletController._bad("Invalid Ethereum address.")

            signature = (body.get("signature") or "").strip()
            if not signature:
                return WalletController._bad("Signature is required.")

            provider = body.get("provider")
            chain_id = body.get("chain_id")

            result = walletService.verify_signature(address, signature, provider, chain_id)
            return JsonResponse({"status": True, "data": result})
        except ValueError as e:
            return WalletController._bad(str(e), 400)
        except Exception as e:
            return WalletController._bad(str(e), 500)

    # ------------------------------------------------------------------ #
    #  POST /api/wallet/disconnect/
    # ------------------------------------------------------------------ #

    @staticmethod
    @csrf_exempt
    def disconnect(request):
        """POST: Desconecta la sesión del wallet."""
        try:
            body = WalletController._json_body(request)
            if not body:
                return WalletController._bad("Invalid JSON body.")

            address = walletService.validate_address(body.get("address", ""))
            if not address:
                return WalletController._bad("Invalid Ethereum address.")

            result = walletService.disconnect_wallet(address)
            return JsonResponse({"status": True, "data": result})
        except ValueError as e:
            return WalletController._bad(str(e), 404)
        except Exception as e:
            return WalletController._bad(str(e), 500)

    # ------------------------------------------------------------------ #
    #  GET /api/wallet/status/?address=0x…
    # ------------------------------------------------------------------ #

    @staticmethod
    def status(request):
        """GET: Consulta el estado de conexión del wallet."""
        try:
            address = walletService.validate_address(request.GET.get("address", ""))
            if not address:
                return WalletController._bad("Invalid Ethereum address.")

            result = walletService.get_wallet_status(address)
            return JsonResponse({"status": True, "data": result})
        except Exception as e:
            return WalletController._bad(str(e), 500)

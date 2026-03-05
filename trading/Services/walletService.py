"""
walletService.py - Capa de servicio para operaciones de wallet.
Contiene toda la lógica de negocio (equivalente a un Service de Express.js).
El controlador delega aquí; el servicio interactúa con el modelo.
"""

import logging
import re

from eth_account.messages import encode_defunct
from web3 import Web3

from trading.Models.walletSessionModel import WalletSession

logger = logging.getLogger(__name__)

# Reusable Web3 instance (no provider needed – only used for ecrecover)
w3 = Web3()

# Regex for validating Ethereum addresses
ETH_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")

SIGN_MESSAGE_TEMPLATE = (
    "Welcome to TradingWeb!\n\n"
    "Sign this message to verify your wallet.\n\n"
    "Nonce: {nonce}"
)


def validate_address(address: str) -> str | None:
    """
    Valida y normaliza una dirección Ethereum.
    Retorna la dirección checksum o None si es inválida.
    """
    address = (address or "").strip()
    if not ETH_ADDRESS_RE.match(address):
        return None
    return Web3.to_checksum_address(address)


def generate_nonce(address: str, provider: str = "trust_wallet") -> dict:
    """
    Genera (o rota) un nonce único para la dirección dada.
    Retorna dict con address, nonce y message a firmar.
    """
    session, _created = WalletSession.objects.get_or_create(
        address=address,
        defaults={"provider": provider},
    )
    nonce = session.rotate_nonce()

    return {
        "address": address,
        "nonce": nonce,
        "message": SIGN_MESSAGE_TEMPLATE.format(nonce=nonce),
    }


def verify_signature(address: str, signature: str, provider: str = None, chain_id: int = None) -> dict:
    """
    Verifica la firma del nonce y activa la sesión del wallet.
    Retorna dict con resultado o lanza ValueError.
    """
    try:
        session = WalletSession.objects.get(address=address)
    except WalletSession.DoesNotExist:
        raise ValueError("Request a nonce first.")

    if not session.nonce:
        raise ValueError("No pending nonce. Request a new one.")

    # Reconstruct the message the user should have signed
    message = SIGN_MESSAGE_TEMPLATE.format(nonce=session.nonce)
    encoded = encode_defunct(text=message)

    try:
        recovered = w3.eth.account.recover_message(encoded, signature=signature)
    except Exception as exc:
        logger.warning("Signature recovery failed for %s: %s", address, exc)
        raise ValueError("Invalid signature.")

    if recovered.lower() != address.lower():
        raise ValueError("Signature does not match the address.")

    # Update session
    if provider:
        session.provider = provider
    if chain_id is not None:
        session.chain_id = int(chain_id)

    session.mark_connected()
    session.rotate_nonce()  # Invalidate used nonce

    logger.info("Wallet connected: %s (%s, chain %s)", address, session.provider, session.chain_id)

    return {
        "authenticated": True,
        "address": address,
        "provider": session.get_provider_display(),
        "chain_id": session.chain_id,
    }


def disconnect_wallet(address: str) -> dict:
    """
    Desconecta una sesión de wallet.
    Retorna dict con resultado o lanza ValueError.
    """
    try:
        session = WalletSession.objects.get(address=address)
        session.mark_disconnected()
        logger.info("Wallet disconnected: %s", address)
        return {"disconnected": True, "address": address}
    except WalletSession.DoesNotExist:
        raise ValueError("Wallet session not found.")


def get_wallet_status(address: str) -> dict:
    """
    Retorna el estado de conexión del wallet.
    """
    try:
        session = WalletSession.objects.get(address=address)
        return {
            "address": address,
            "is_active": session.is_active,
            "provider": session.get_provider_display(),
            "chain_id": session.chain_id,
            "connected_at": session.connected_at.isoformat() if session.connected_at else None,
        }
    except WalletSession.DoesNotExist:
        return {
            "address": address,
            "is_active": False,
        }


# ------------------------------------------------------------------ #
#  Helpers para el WebSocket (async-safe usando ORM sync)
# ------------------------------------------------------------------ #

def ws_connect_wallet(address: str, provider: str, chain_id: int):
    """Persiste la conexión del wallet (llamado desde el consumer WS)."""
    session, _ = WalletSession.objects.get_or_create(
        address__iexact=address,
        defaults={"address": address},
    )
    session.provider = provider
    session.chain_id = chain_id
    session.mark_connected()


def ws_set_active(address: str, active: bool):
    """Marca el wallet como activo/inactivo (llamado desde el consumer WS)."""
    try:
        s = WalletSession.objects.get(address__iexact=address)
        if active:
            s.mark_connected()
        else:
            s.mark_disconnected()
    except WalletSession.DoesNotExist:
        pass


def ws_switch_chain(address: str, chain_id: int):
    """Cambia el chain_id del wallet (llamado desde el consumer WS)."""
    try:
        s = WalletSession.objects.get(address__iexact=address)
        s.chain_id = chain_id
        s.save(update_fields=["chain_id"])
    except WalletSession.DoesNotExist:
        pass

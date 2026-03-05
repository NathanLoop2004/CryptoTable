"""
walletSessionModel.py - Modelo para sesiones de wallet.
Estructura de modelo Django (equivalente a un Schema de Mongoose/Sequelize).
"""

import secrets

from django.db import models
from django.utils import timezone


class WalletSession(models.Model):
    """
    Tracks a connected crypto wallet (Trust Wallet, MetaMask, etc.).

    Flow:
      1. Frontend requests a nonce via POST /api/wallet/nonce/
      2. User signs the nonce in Trust Wallet (via WalletConnect).
      3. Frontend sends address + signature to POST /api/wallet/verify/
      4. Backend recovers the signer address, creates/updates session.
    """

    WALLET_PROVIDERS = [
        ("trust_wallet", "Trust Wallet"),
        ("metamask", "MetaMask"),
        ("walletconnect", "WalletConnect"),
        ("other", "Other"),
    ]

    address = models.CharField(
        max_length=42,
        unique=True,
        help_text="Ethereum-compatible wallet address (0x…)",
    )
    provider = models.CharField(
        max_length=20,
        choices=WALLET_PROVIDERS,
        default="trust_wallet",
    )
    nonce = models.CharField(
        max_length=64,
        default="",
        help_text="One-time nonce the user must sign to authenticate.",
    )
    is_active = models.BooleanField(default=False)
    connected_at = models.DateTimeField(null=True, blank=True)
    last_seen = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    # Optional: chain id the wallet is connected to
    chain_id = models.IntegerField(
        default=1,
        help_text="EVM chain id (1=Ethereum, 56=BSC, 137=Polygon …)",
    )

    class Meta:
        ordering = ["-last_seen"]
        verbose_name = "Wallet Session"
        verbose_name_plural = "Wallet Sessions"
        app_label = "trading"

    def __str__(self):
        return f"{self.get_provider_display()} – {self.short_address}"

    @property
    def short_address(self) -> str:
        return f"{self.address[:6]}…{self.address[-4:]}" if self.address else ""

    def rotate_nonce(self) -> str:
        """Generate and save a fresh nonce."""
        self.nonce = secrets.token_hex(32)
        self.save(update_fields=["nonce"])
        return self.nonce

    def mark_connected(self):
        self.is_active = True
        self.connected_at = timezone.now()
        self.save(update_fields=["is_active", "connected_at"])

    def mark_disconnected(self):
        self.is_active = False
        self.save(update_fields=["is_active"])

"""
viewController.py - Controlador para servir páginas HTML.
Equivalente a los controller renders de Express.js.
"""

from django.shortcuts import render


class ViewController:

    @staticmethod
    def login(request):
        """GET / – Login page to connect wallet."""
        return render(request, "login.html")

    @staticmethod
    def dashboard(request):
        """GET /dashboard/ – Main trading dashboard."""
        return render(request, "dashboard.html")

    @staticmethod
    def transactions(request):
        """GET /transactions/ – Full transactions page."""
        return render(request, "transactions.html")

    @staticmethod
    def wallet_page(request):
        """GET /wallet/ – Full wallet info page."""
        return render(request, "wallet.html")

    @staticmethod
    def sniper_page(request):
        """GET /sniper/ – Sniper bot page."""
        return render(request, "sniper.html")

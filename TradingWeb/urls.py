"""
URL configuration for TradingWeb project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import include, path
from django.conf import settings
from django.contrib.staticfiles.urls import staticfiles_urlpatterns

from trading.Controllers.viewController import ViewController

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/', include('trading.Routes.urls')),

    # ─── Pages ────────────────────────────────────────────
    path('', ViewController.login, name='login'),
    path('dashboard/', ViewController.dashboard, name='dashboard'),
    path('transactions/', ViewController.transactions, name='transactions'),
    path('wallet/', ViewController.wallet_page, name='wallet'),
    path('sniper/', ViewController.sniper_page, name='sniper'),
]

# Serve static files in development (needed for Daphne/ASGI)
if settings.DEBUG:
    urlpatterns += staticfiles_urlpatterns()

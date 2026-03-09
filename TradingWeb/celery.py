"""
celery.py — Celery application configuration for TradingWeb.

Usage:
    celery -A TradingWeb worker -l info
    celery -A TradingWeb beat -l info

Discovers tasks from trading/tasks.py automatically.
"""

import os
from celery import Celery

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'TradingWeb.settings')

app = Celery('TradingWeb')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    """Debugging helper — prints request info."""
    print(f'Request: {self.request!r}')

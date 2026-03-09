"""
tasks.py — Celery Async Tasks v6.
══════════════════════════════════

Defines the periodic and on-demand tasks that run in Celery workers.
Referenced by CELERY_BEAT_SCHEDULE in settings.py.

Periodic tasks:
  - daily_backtest_summary: Run backtest on last 24h and report stats
  - run_strategy_optimization: AI optimizer cycle
  - cleanup_old_data: Purge old signals, metrics, and trade records

On-demand tasks:
  - run_backtest: Execute a full backtest (can take minutes)
  - optimize_strategy_now: Trigger immediate optimization
"""

import logging
import time

from celery import shared_task

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
#  Periodic tasks (referenced in CELERY_BEAT_SCHEDULE)
# ═══════════════════════════════════════════════════════════════════

@shared_task(name="daily_backtest_summary", bind=True, max_retries=1, soft_time_limit=600)
def daily_backtest_summary(self):
    """
    Run a lightweight backtest on the last 24h of pair data.
    Produces a summary report and sends it via WebSocket/Discord.
    """
    try:
        from web3 import Web3
        from trading.Services.backtestEngine import BacktestEngine, BacktestConfig
        import asyncio
        import os

        rpc = os.environ.get("SNIPER_RPC_BSC", "https://bsc-dataseed1.binance.org")
        w3 = Web3(Web3.HTTPProvider(rpc))

        config = BacktestConfig(
            chain_id=56,
            buy_amount_native=0.05,
            take_profit_pct=40.0,
            stop_loss_pct=15.0,
            max_hold_hours=24.0,
            name="daily_auto",
        )

        engine = BacktestEngine(w3, chain_id=56)
        result = asyncio.run(engine.run(config))

        summary = {
            "date": time.strftime("%Y-%m-%d"),
            "pairs_found": result.pairs_found,
            "trades": result.total_trades,
            "win_rate": round(result.win_rate, 1),
            "total_pnl_usd": round(result.total_pnl_usd, 2),
            "sharpe": result.sharpe_ratio,
            "max_drawdown": result.max_drawdown_pct,
        }

        logger.info(f"Daily backtest: {summary}")

        # Try to emit via alertService
        try:
            from trading.Services.alertService import alert_manager

            msg = (
                f"📊 **Daily Backtest Summary**\n"
                f"• Pairs found: {result.pairs_found}\n"
                f"• Trades: {result.total_trades}\n"
                f"• Win rate: {result.win_rate:.1f}%\n"
                f"• P&L: ${result.total_pnl_usd:.2f}\n"
                f"• Sharpe: {result.sharpe_ratio}\n"
                f"• Max DD: {result.max_drawdown_pct:.1f}%"
            )
            asyncio.run(alert_manager.send_alert(msg, level="info"))
        except Exception:
            pass

        return summary

    except Exception as exc:
        logger.error(f"Daily backtest task failed: {exc}")
        raise self.retry(exc=exc, countdown=300)


@shared_task(name="run_strategy_optimization", bind=True, max_retries=1, soft_time_limit=300)
def run_strategy_optimization(self):
    """
    Run the AI strategy optimizer.
    Takes recent trade history, trains model, proposes new params.
    """
    try:
        from trading.Services.strategyOptimizer import StrategyOptimizer, TradeOutcome
        import asyncio

        optimizer = StrategyOptimizer()

        # Load trade history from metrics (mock for now — real integration reads from DB)
        try:
            from trading.Services.metricsService import MetricsCollector
            metrics = MetricsCollector()
            trade_data = metrics.get_trade_history(limit=200)
            for td in trade_data:
                outcome = TradeOutcome(
                    token_address=td.get("token", ""),
                    pnl_percent=td.get("pnl_pct", 0),
                    pnl_usd=td.get("pnl_usd", 0),
                    hold_seconds=td.get("hold_seconds", 0),
                    exit_reason=td.get("exit_reason", ""),
                    timestamp=td.get("timestamp", time.time()),
                )
                optimizer.record_trade(outcome)
        except Exception as me:
            logger.debug(f"Could not load trade history from metrics: {me}")

        result = asyncio.run(optimizer.optimize())

        summary = {
            "status": result.status,
            "confidence": round(result.confidence, 2),
            "improvement": round(result.improvement_predicted_pct, 1),
            "market_regime": result.market_regime,
            "candidates_evaluated": result.candidates_evaluated,
        }

        logger.info(f"Strategy optimization: {summary}")
        return summary

    except Exception as exc:
        logger.error(f"Strategy optimization task failed: {exc}")
        raise self.retry(exc=exc, countdown=120)


@shared_task(name="cleanup_old_data", soft_time_limit=120)
def cleanup_old_data():
    """
    Purge data older than 30 days:
    - Old Celery task results
    - Old WebSocket signal logs
    - Stale metric snapshots
    """
    try:
        cleaned = {}

        # Clean Celery results
        try:
            from django_celery_results.models import TaskResult
            from django.utils import timezone
            import datetime

            cutoff = timezone.now() - datetime.timedelta(days=30)
            deleted, _ = TaskResult.objects.filter(date_done__lt=cutoff).delete()
            cleaned["celery_results"] = deleted
        except Exception:
            cleaned["celery_results"] = 0

        logger.info(f"Cleanup: {cleaned}")
        return cleaned

    except Exception as e:
        logger.error(f"Cleanup task failed: {e}")
        return {"error": str(e)}


# ═══════════════════════════════════════════════════════════════════
#  On-demand tasks
# ═══════════════════════════════════════════════════════════════════

@shared_task(name="run_backtest", bind=True, soft_time_limit=900)
def run_backtest(self, config_dict: dict):
    """
    Run a full backtest with custom config (on-demand).
    Called from frontend: run_backtest.delay(config_dict)
    """
    try:
        from web3 import Web3
        from trading.Services.backtestEngine import BacktestEngine, BacktestConfig
        import asyncio
        import os

        rpc = os.environ.get("SNIPER_RPC_BSC", "https://bsc-dataseed1.binance.org")
        w3 = Web3(Web3.HTTPProvider(rpc))

        config = BacktestConfig(**{
            k: v for k, v in config_dict.items() if hasattr(BacktestConfig, k)
        })

        engine = BacktestEngine(w3, chain_id=config.chain_id)

        def progress_cb(pct, msg):
            self.update_state(state="PROGRESS", meta={"percent": pct, "message": msg})

        engine.set_progress_callback(progress_cb)
        result = asyncio.run(engine.run(config))

        return result.to_dict()

    except Exception as exc:
        logger.error(f"Backtest task failed: {exc}")
        raise self.retry(exc=exc, countdown=60, max_retries=0)


@shared_task(name="optimize_strategy_now", bind=True, soft_time_limit=300)
def optimize_strategy_now(self):
    """Trigger immediate strategy optimization (on-demand)."""
    return run_strategy_optimization()

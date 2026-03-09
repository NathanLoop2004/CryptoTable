"""
metricsService.py — Performance Metrics & Analytics Engine v4
═══════════════════════════════════════════════════════════════
Tracks detection speed, bot effectiveness, P&L history, and
aggregated stats for the dashboard visualization layer.

Key metrics tracked:
  - Detection speed (time from pair creation → analysis complete)
  - Alert counter per category/level
  - Historical P&L per trade
  - Bot effectiveness (% safe tokens detected, avg gains, failures)
  - Module performance (analysis times per module)
"""
from __future__ import annotations

import time
import logging
import statistics
from dataclasses import dataclass, field, asdict
from collections import defaultdict
from typing import Optional

logger = logging.getLogger("metricsService")

# ─── Data Structures ──────────────────────────────────────────────

@dataclass
class TradeRecord:
    """Immutable record of a completed trade."""
    token_address: str
    symbol: str
    chain_id: int
    buy_price_usd: float = 0.0
    sell_price_usd: float = 0.0
    buy_amount_native: float = 0.0
    pnl_percent: float = 0.0
    pnl_usd: float = 0.0
    hold_seconds: float = 0.0
    is_profitable: bool = False
    exit_reason: str = ""         # take_profit | stop_loss | manual | rug
    risk_engine_score: int = 0
    pump_grade: str = ""
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DetectionEvent:
    """Records how fast the pipeline processed a new pair."""
    token_address: str
    symbol: str
    detection_ms: float = 0.0      # pair_created → token_info complete
    analysis_ms: float = 0.0       # full pipeline (all modules)
    modules_ms: dict = field(default_factory=dict)  # per-module times
    result: str = ""               # passed | rejected | error
    rejection_reason: str = ""
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return asdict(self)


# ─── Metrics Engine ───────────────────────────────────────────────

class MetricsService:
    """
    Centralized metrics aggregator.
    Thread-safe for single-worker ASGI — uses simple dicts/lists.
    """

    MAX_TRADES = 500        # keep last N trades
    MAX_DETECTIONS = 1000   # keep last N detection events
    MAX_ALERTS = 2000       # keep last N alert counts

    def __init__(self):
        self._start_time = time.time()

        # ── Trade P&L tracking ──
        self._trades: list[TradeRecord] = []
        self._total_pnl_usd: float = 0.0
        self._total_trades: int = 0
        self._winning_trades: int = 0
        self._losing_trades: int = 0

        # ── Detection speed ──
        self._detections: list[DetectionEvent] = []
        self._total_detections: int = 0
        self._passed_detections: int = 0
        self._rejected_detections: int = 0
        self._error_detections: int = 0

        # ── Alert counters ──
        self._alerts_by_category: dict[str, int] = defaultdict(int)
        self._alerts_by_level: dict[str, int] = defaultdict(int)
        self._alerts_total: int = 0

        # ── Module performance ──
        self._module_times: dict[str, list[float]] = defaultdict(list)

        # ── Hourly buckets for time-series ──
        self._hourly_pnl: dict[str, float] = defaultdict(float)        # "YYYY-MM-DD HH" → pnl
        self._hourly_trades: dict[str, int] = defaultdict(int)         # "YYYY-MM-DD HH" → count
        self._hourly_detections: dict[str, int] = defaultdict(int)     # "YYYY-MM-DD HH" → count

        logger.info("MetricsService initialized")

    # ─── Trade Recording ──────────────────────────────────

    def record_trade(self, trade: TradeRecord) -> None:
        """Record a completed trade for P&L tracking."""
        self._trades.append(trade)
        self._total_trades += 1
        self._total_pnl_usd += trade.pnl_usd

        if trade.is_profitable:
            self._winning_trades += 1
        else:
            self._losing_trades += 1

        # Hourly bucket
        hour_key = time.strftime("%Y-%m-%d %H", time.localtime(trade.timestamp))
        self._hourly_pnl[hour_key] += trade.pnl_usd
        self._hourly_trades[hour_key] += 1

        # Trim history
        if len(self._trades) > self.MAX_TRADES:
            self._trades = self._trades[-self.MAX_TRADES:]

        logger.info(
            f"Trade recorded: {trade.symbol} PnL={trade.pnl_percent:+.1f}% "
            f"(${trade.pnl_usd:+.2f}) exit={trade.exit_reason}"
        )

    def record_trade_from_snipe(self, snipe_data: dict, sell_price: float,
                                 exit_reason: str = "manual") -> TradeRecord:
        """Convenience: create a TradeRecord from an ActiveSnipe dict + sell price."""
        buy_price = snipe_data.get("buy_price_usd", 0)
        buy_amount = snipe_data.get("buy_amount_native", 0)
        pnl_pct = ((sell_price - buy_price) / buy_price * 100) if buy_price > 0 else 0
        pnl_usd = (sell_price - buy_price) * buy_amount if buy_price > 0 else 0
        hold_s = time.time() - snipe_data.get("timestamp", time.time())

        trade = TradeRecord(
            token_address=snipe_data.get("token_address", ""),
            symbol=snipe_data.get("symbol", "?"),
            chain_id=snipe_data.get("chain_id", 56),
            buy_price_usd=buy_price,
            sell_price_usd=sell_price,
            buy_amount_native=buy_amount,
            pnl_percent=pnl_pct,
            pnl_usd=pnl_usd,
            hold_seconds=hold_s,
            is_profitable=pnl_pct > 0,
            exit_reason=exit_reason,
            risk_engine_score=snipe_data.get("risk_engine_score", 0),
            pump_grade=snipe_data.get("pump_grade", ""),
        )
        self.record_trade(trade)
        return trade

    # ─── Detection Speed ──────────────────────────────────

    def record_detection(self, event: DetectionEvent) -> None:
        """Record a pipeline detection event."""
        self._detections.append(event)
        self._total_detections += 1

        if event.result == "passed":
            self._passed_detections += 1
        elif event.result == "rejected":
            self._rejected_detections += 1
        else:
            self._error_detections += 1

        # Per-module times
        for module, ms in event.modules_ms.items():
            self._module_times[module].append(ms)
            # Keep last 500 per module
            if len(self._module_times[module]) > 500:
                self._module_times[module] = self._module_times[module][-500:]

        # Hourly bucket
        hour_key = time.strftime("%Y-%m-%d %H", time.localtime(event.timestamp))
        self._hourly_detections[hour_key] += 1

        # Trim
        if len(self._detections) > self.MAX_DETECTIONS:
            self._detections = self._detections[-self.MAX_DETECTIONS:]

    # ─── Alert Counting ───────────────────────────────────

    def record_alert(self, category: str, level: str) -> None:
        """Count an alert event by category and level."""
        self._alerts_by_category[category] += 1
        self._alerts_by_level[level] += 1
        self._alerts_total += 1

    # ─── Aggregated Stats ────────────────────────────────

    def get_trade_stats(self) -> dict:
        """Return P&L summary statistics."""
        win_rate = (self._winning_trades / self._total_trades * 100) if self._total_trades > 0 else 0
        avg_pnl = (self._total_pnl_usd / self._total_trades) if self._total_trades > 0 else 0

        # Per-trade PnL list for additional stats
        pnl_list = [t.pnl_percent for t in self._trades]
        best_trade = max(pnl_list) if pnl_list else 0.0
        worst_trade = min(pnl_list) if pnl_list else 0.0
        avg_pnl_pct = statistics.mean(pnl_list) if pnl_list else 0.0
        median_pnl = statistics.median(pnl_list) if pnl_list else 0.0

        # Average hold time
        hold_times = [t.hold_seconds for t in self._trades if t.hold_seconds > 0]
        avg_hold_s = statistics.mean(hold_times) if hold_times else 0.0

        return {
            "total_trades": self._total_trades,
            "winning_trades": self._winning_trades,
            "losing_trades": self._losing_trades,
            "win_rate_percent": round(win_rate, 1),
            "total_pnl_usd": round(self._total_pnl_usd, 2),
            "avg_pnl_usd": round(avg_pnl, 2),
            "avg_pnl_percent": round(avg_pnl_pct, 1),
            "median_pnl_percent": round(median_pnl, 1),
            "best_trade_percent": round(best_trade, 1),
            "worst_trade_percent": round(worst_trade, 1),
            "avg_hold_seconds": round(avg_hold_s, 0),
        }

    def get_detection_stats(self) -> dict:
        """Return detection pipeline statistics."""
        det_times = [d.analysis_ms for d in self._detections if d.analysis_ms > 0]
        avg_analysis = statistics.mean(det_times) if det_times else 0.0
        p95_analysis = sorted(det_times)[int(len(det_times) * 0.95)] if len(det_times) > 1 else avg_analysis
        fastest = min(det_times) if det_times else 0.0

        pass_rate = (self._passed_detections / self._total_detections * 100) if self._total_detections > 0 else 0

        return {
            "total_detections": self._total_detections,
            "passed": self._passed_detections,
            "rejected": self._rejected_detections,
            "errors": self._error_detections,
            "pass_rate_percent": round(pass_rate, 1),
            "avg_analysis_ms": round(avg_analysis, 1),
            "p95_analysis_ms": round(p95_analysis, 1),
            "fastest_ms": round(fastest, 1),
        }

    def get_module_stats(self) -> dict:
        """Return per-module average analysis times."""
        result = {}
        for module, times in self._module_times.items():
            if times:
                result[module] = {
                    "avg_ms": round(statistics.mean(times), 1),
                    "max_ms": round(max(times), 1),
                    "min_ms": round(min(times), 1),
                    "calls": len(times),
                }
        return result

    def get_alert_stats(self) -> dict:
        """Return alert counters."""
        return {
            "total_alerts": self._alerts_total,
            "by_category": dict(self._alerts_by_category),
            "by_level": dict(self._alerts_by_level),
        }

    def get_effectiveness(self) -> dict:
        """
        Bot effectiveness metrics.
        Shows how good the bot is at filtering safe/profitable tokens.
        """
        # Token filtering effectiveness
        safe_tokens = self._passed_detections
        total_analyzed = self._total_detections
        filter_rate = (
            (self._rejected_detections / total_analyzed * 100)
            if total_analyzed > 0 else 0
        )

        # Trade effectiveness
        profitable_exits = [t for t in self._trades if t.is_profitable]
        rug_exits = [t for t in self._trades if t.exit_reason == "rug"]
        stop_loss_exits = [t for t in self._trades if t.exit_reason == "stop_loss"]
        take_profit_exits = [t for t in self._trades if t.exit_reason == "take_profit"]

        avg_gain = (
            statistics.mean([t.pnl_percent for t in profitable_exits])
            if profitable_exits else 0
        )
        avg_loss = (
            statistics.mean([abs(t.pnl_percent) for t in self._trades if not t.is_profitable])
            if self._losing_trades > 0 else 0
        )

        # Risk/reward ratio
        risk_reward = (avg_gain / avg_loss) if avg_loss > 0 else 0

        return {
            "tokens_analyzed": total_analyzed,
            "tokens_passed_filter": safe_tokens,
            "filter_rejection_rate": round(filter_rate, 1),
            "trades_total": self._total_trades,
            "trades_profitable": self._winning_trades,
            "trades_losing": self._losing_trades,
            "trades_rug_detected": len(rug_exits),
            "trades_stop_loss": len(stop_loss_exits),
            "trades_take_profit": len(take_profit_exits),
            "avg_gain_percent": round(avg_gain, 1),
            "avg_loss_percent": round(avg_loss, 1),
            "risk_reward_ratio": round(risk_reward, 2),
            "win_rate_percent": round(
                (self._winning_trades / self._total_trades * 100)
                if self._total_trades > 0 else 0, 1
            ),
        }

    def get_hourly_series(self, hours: int = 24) -> dict:
        """
        Return hourly time-series data for the dashboard charts.
        Returns last N hours of PnL, trades, and detections.
        """
        now = time.time()
        labels = []
        pnl_data = []
        trade_data = []
        detection_data = []

        for i in range(hours - 1, -1, -1):
            ts = now - i * 3600
            key = time.strftime("%Y-%m-%d %H", time.localtime(ts))
            label = time.strftime("%H:00", time.localtime(ts))
            labels.append(label)
            pnl_data.append(round(self._hourly_pnl.get(key, 0), 2))
            trade_data.append(self._hourly_trades.get(key, 0))
            detection_data.append(self._hourly_detections.get(key, 0))

        return {
            "labels": labels,
            "pnl_usd": pnl_data,
            "trades": trade_data,
            "detections": detection_data,
        }

    def get_recent_trades(self, limit: int = 20) -> list[dict]:
        """Return most recent trades for the dashboard table."""
        return [t.to_dict() for t in self._trades[-limit:]][::-1]

    def get_recent_detections(self, limit: int = 20) -> list[dict]:
        """Return most recent detection events."""
        return [d.to_dict() for d in self._detections[-limit:]][::-1]

    # ─── Full Dashboard Payload ───────────────────────────

    def get_dashboard(self) -> dict:
        """
        Return the complete dashboard payload for WebSocket emission.
        This is what the frontend consumes to render the metrics dashboard.
        """
        return {
            "uptime_seconds": round(time.time() - self._start_time),
            "trade_stats": self.get_trade_stats(),
            "detection_stats": self.get_detection_stats(),
            "module_performance": self.get_module_stats(),
            "alert_stats": self.get_alert_stats(),
            "effectiveness": self.get_effectiveness(),
            "hourly_chart": self.get_hourly_series(24),
            "recent_trades": self.get_recent_trades(10),
            "recent_detections": self.get_recent_detections(10),
        }

    def get_stats(self) -> dict:
        """Compact stats for inclusion in SniperBot.get_state()."""
        return {
            "total_trades": self._total_trades,
            "win_rate": round(
                (self._winning_trades / self._total_trades * 100)
                if self._total_trades > 0 else 0, 1
            ),
            "total_pnl_usd": round(self._total_pnl_usd, 2),
            "total_detections": self._total_detections,
            "total_alerts": self._alerts_total,
        }

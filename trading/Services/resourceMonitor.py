"""
resourceMonitor.py — System resource monitoring for the sniper bot.

Tracks memory, CPU, WebSocket connections, async tasks, and token processing
throughput. Emits periodic stats to the frontend via WebSocket events.

Usage:
    monitor = ResourceMonitor()
    monitor.record_token_processed(symbol, elapsed_ms)
    stats = monitor.get_snapshot()
"""

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field, asdict
from collections import deque

logger = logging.getLogger(__name__)

# Optional psutil for detailed system metrics
try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False
    logger.info("psutil not installed — resource monitoring will use basic os metrics")


@dataclass
class ResourceSnapshot:
    """Point-in-time resource usage."""
    timestamp: float = 0
    # Memory (MB)
    memory_rss_mb: float = 0          # Resident Set Size
    memory_vms_mb: float = 0          # Virtual Memory Size
    memory_percent: float = 0         # % of system RAM
    # CPU
    cpu_percent: float = 0            # Process CPU %
    cpu_system_percent: float = 0     # Total system CPU %
    # Async
    active_tasks: int = 0             # asyncio tasks alive
    # WebSocket
    ws_connections: int = 0           # Active WS connections
    ws_messages_sent: int = 0         # Total WS messages sent
    ws_messages_rate: float = 0       # Messages per second (last window)
    # Token processing
    tokens_processed: int = 0         # Total tokens analyzed
    tokens_per_minute: float = 0      # Throughput
    avg_analysis_ms: float = 0        # Average token analysis time
    # RPC
    rpc_calls_total: int = 0
    rpc_errors_total: int = 0
    rpc_avg_latency_ms: float = 0
    # Warnings
    warnings: list = field(default_factory=list)


class ResourceMonitor:
    """
    Lightweight resource monitor designed for async Django Channels environment.
    Collects metrics every N seconds and keeps a rolling history.
    """

    # Thresholds for warnings
    MEMORY_WARN_MB = 512
    MEMORY_CRITICAL_MB = 1024
    CPU_WARN_PERCENT = 70
    CPU_CRITICAL_PERCENT = 90
    TASK_WARN_COUNT = 200
    MAX_HISTORY = 120          # ~2 min at 1s intervals

    def __init__(self):
        self._process = psutil.Process(os.getpid()) if HAS_PSUTIL else None
        self._history: deque[ResourceSnapshot] = deque(maxlen=self.MAX_HISTORY)

        # Counters
        self._tokens_processed = 0
        self._token_times: deque[tuple[float, float]] = deque(maxlen=500)  # (timestamp, elapsed_ms)
        self._ws_messages_sent = 0
        self._ws_msg_times: deque[float] = deque(maxlen=1000)  # timestamps
        self._ws_connections = 0
        self._rpc_calls = 0
        self._rpc_errors = 0
        self._rpc_latencies: deque[float] = deque(maxlen=200)  # ms

        # Start time
        self._start_time = time.time()

    # ─── Recording Methods ──────────────────────────────

    def record_token_processed(self, symbol: str, elapsed_ms: float):
        """Record that a token was fully analyzed."""
        self._tokens_processed += 1
        self._token_times.append((time.time(), elapsed_ms))

    def record_ws_message(self):
        """Record a WebSocket message sent."""
        self._ws_messages_sent += 1
        self._ws_msg_times.append(time.time())

    def record_rpc_call(self, latency_ms: float, error: bool = False):
        """Record an RPC call."""
        self._rpc_calls += 1
        self._rpc_latencies.append(latency_ms)
        if error:
            self._rpc_errors += 1

    def set_ws_connections(self, count: int):
        """Update active WS connection count."""
        self._ws_connections = count

    def inc_ws_connections(self):
        self._ws_connections += 1

    def dec_ws_connections(self):
        self._ws_connections = max(0, self._ws_connections - 1)

    # ─── Snapshot ───────────────────────────────────────

    def get_snapshot(self) -> ResourceSnapshot:
        """Capture current resource state."""
        snap = ResourceSnapshot(timestamp=time.time())
        warnings = []

        # ── Memory ──
        if self._process and HAS_PSUTIL:
            try:
                mem = self._process.memory_info()
                snap.memory_rss_mb = round(mem.rss / (1024 * 1024), 1)
                snap.memory_vms_mb = round(mem.vms / (1024 * 1024), 1)
                snap.memory_percent = round(self._process.memory_percent(), 1)
            except Exception:
                pass
        else:
            # Fallback: basic os-level estimate (Windows/Linux)
            try:
                import resource
                usage = resource.getrusage(resource.RUSAGE_SELF)
                snap.memory_rss_mb = round(usage.ru_maxrss / 1024, 1)  # Linux: KB
            except ImportError:
                pass

        # ── CPU ──
        if self._process and HAS_PSUTIL:
            try:
                snap.cpu_percent = round(self._process.cpu_percent(interval=0), 1)
                snap.cpu_system_percent = round(psutil.cpu_percent(interval=0), 1)
            except Exception:
                pass

        # ── Async tasks ──
        try:
            all_tasks = asyncio.all_tasks()
            snap.active_tasks = len(all_tasks)
        except RuntimeError:
            snap.active_tasks = 0

        # ── WebSocket ──
        snap.ws_connections = self._ws_connections
        snap.ws_messages_sent = self._ws_messages_sent
        now = time.time()
        recent_msgs = sum(1 for t in self._ws_msg_times if now - t < 10)
        snap.ws_messages_rate = round(recent_msgs / 10, 1)

        # ── Token throughput ──
        snap.tokens_processed = self._tokens_processed
        recent_tokens = sum(1 for t, _ in self._token_times if now - t < 60)
        snap.tokens_per_minute = round(recent_tokens, 1)
        if self._token_times:
            recent_times = [ms for t, ms in self._token_times if now - t < 120]
            snap.avg_analysis_ms = round(
                sum(recent_times) / len(recent_times), 1
            ) if recent_times else 0

        # ── RPC stats ──
        snap.rpc_calls_total = self._rpc_calls
        snap.rpc_errors_total = self._rpc_errors
        if self._rpc_latencies:
            snap.rpc_avg_latency_ms = round(
                sum(self._rpc_latencies) / len(self._rpc_latencies), 1
            )

        # ── Warnings ──
        if snap.memory_rss_mb > self.MEMORY_CRITICAL_MB:
            warnings.append(f"🔴 CRITICAL: Memory {snap.memory_rss_mb}MB (>{self.MEMORY_CRITICAL_MB}MB)")
        elif snap.memory_rss_mb > self.MEMORY_WARN_MB:
            warnings.append(f"🟡 WARNING: Memory {snap.memory_rss_mb}MB (>{self.MEMORY_WARN_MB}MB)")

        if snap.cpu_percent > self.CPU_CRITICAL_PERCENT:
            warnings.append(f"🔴 CRITICAL: CPU {snap.cpu_percent}% (>{self.CPU_CRITICAL_PERCENT}%)")
        elif snap.cpu_percent > self.CPU_WARN_PERCENT:
            warnings.append(f"🟡 WARNING: CPU {snap.cpu_percent}% (>{self.CPU_WARN_PERCENT}%)")

        if snap.active_tasks > self.TASK_WARN_COUNT:
            warnings.append(f"🟡 WARNING: {snap.active_tasks} async tasks alive (>{self.TASK_WARN_COUNT})")

        if self._rpc_calls > 10:
            error_rate = self._rpc_errors / self._rpc_calls * 100
            if error_rate > 30:
                warnings.append(f"🔴 RPC error rate: {error_rate:.0f}%")
            elif error_rate > 10:
                warnings.append(f"🟡 RPC error rate: {error_rate:.0f}%")

        snap.warnings = warnings
        self._history.append(snap)
        return snap

    def get_history(self, minutes: int = 2) -> list[dict]:
        """Return recent snapshots as dicts."""
        cutoff = time.time() - minutes * 60
        return [asdict(s) for s in self._history if s.timestamp > cutoff]

    def get_stats(self) -> dict:
        """Summary for get_state."""
        snap = self.get_snapshot()
        uptime = time.time() - self._start_time
        return {
            "uptime_seconds": round(uptime, 0),
            "uptime_human": self._format_uptime(uptime),
            "memory_rss_mb": snap.memory_rss_mb,
            "memory_percent": snap.memory_percent,
            "cpu_percent": snap.cpu_percent,
            "active_tasks": snap.active_tasks,
            "ws_connections": snap.ws_connections,
            "ws_messages_sent": snap.ws_messages_sent,
            "ws_messages_rate": snap.ws_messages_rate,
            "tokens_processed": snap.tokens_processed,
            "tokens_per_minute": snap.tokens_per_minute,
            "avg_analysis_ms": snap.avg_analysis_ms,
            "rpc_calls": snap.rpc_calls_total,
            "rpc_errors": snap.rpc_errors_total,
            "rpc_avg_latency_ms": snap.rpc_avg_latency_ms,
            "warnings": snap.warnings,
        }

    @staticmethod
    def _format_uptime(seconds: float) -> str:
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        if h > 0:
            return f"{h}h {m}m {s}s"
        elif m > 0:
            return f"{m}m {s}s"
        return f"{s}s"

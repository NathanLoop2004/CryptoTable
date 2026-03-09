"""
apiResilience.py — API fault tolerance, circuit breakers, caching & retry system.

Provides resilience patterns for all external API calls:
  • CircuitBreaker — disables failing APIs temporarily
  • APIResponseCache — TTL-based in-memory cache for API responses
  • RetryExecutor — exponential backoff with configurable retries
  • APIHealthMonitor — tracks per-API stats (success/fail/latency)
  • ResilientAPIManager — orchestrates all of the above

These patterns eliminate cascading failures when GoPlus, Honeypot.is,
DexScreener, CoinGecko, or TokenSniffer go down.

Usage:
    manager = ResilientAPIManager()
    result = await manager.call("goplus", goplus_coroutine, cache_key="0xABC")
    health = manager.get_health_report()
"""

import asyncio
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Callable, Coroutine, Optional

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
#  Circuit Breaker
# ═══════════════════════════════════════════════════════════════

class CircuitState(Enum):
    CLOSED = "closed"          # Normal — requests pass through
    OPEN = "open"              # Tripped — requests rejected
    HALF_OPEN = "half_open"    # Testing — one request allowed


@dataclass
class CircuitBreaker:
    """
    Circuit breaker for an individual API.

    CLOSED  → normal operation; failures counted
    OPEN    → after `failure_threshold` consecutive failures, reject all calls
              for `recovery_timeout` seconds
    HALF_OPEN → after timeout, allow ONE test call.  If it succeeds → CLOSED,
                if it fails → OPEN again with extended timeout.

    Attributes:
        name: Human-readable API name
        failure_threshold: Consecutive failures before opening circuit
        recovery_timeout: Seconds to wait before trying again (half-open)
        max_recovery_timeout: Maximum backoff for successive trips
    """
    name: str = ""
    failure_threshold: int = 3
    recovery_timeout: float = 30.0
    max_recovery_timeout: float = 300.0

    # ── Internal state ──
    state: CircuitState = field(default=CircuitState.CLOSED)
    consecutive_failures: int = 0
    last_failure_time: float = 0.0
    last_success_time: float = 0.0
    total_successes: int = 0
    total_failures: int = 0
    total_rejected: int = 0
    _current_timeout: float = 0.0   # escalating timeout

    def __post_init__(self):
        self._current_timeout = self.recovery_timeout

    def can_execute(self) -> bool:
        """Check if a request should be allowed through."""
        if self.state == CircuitState.CLOSED:
            return True

        if self.state == CircuitState.OPEN:
            # Check if recovery timeout has elapsed → transition to HALF_OPEN
            if time.time() - self.last_failure_time >= self._current_timeout:
                self.state = CircuitState.HALF_OPEN
                logger.info(f"[CB:{self.name}] OPEN → HALF_OPEN (testing recovery)")
                return True
            self.total_rejected += 1
            return False

        # HALF_OPEN — allow exactly one call
        return True

    def record_success(self):
        """Record a successful API call."""
        self.total_successes += 1
        self.last_success_time = time.time()
        if self.state == CircuitState.HALF_OPEN:
            logger.info(f"[CB:{self.name}] HALF_OPEN → CLOSED (recovered!)")
        self.state = CircuitState.CLOSED
        self.consecutive_failures = 0
        self._current_timeout = self.recovery_timeout   # Reset escalation

    def record_failure(self):
        """Record a failed API call."""
        self.total_failures += 1
        self.consecutive_failures += 1
        self.last_failure_time = time.time()

        if self.state == CircuitState.HALF_OPEN:
            # Recovery test failed — back to OPEN with longer timeout
            self._current_timeout = min(
                self._current_timeout * 2, self.max_recovery_timeout
            )
            self.state = CircuitState.OPEN
            logger.warning(f"[CB:{self.name}] HALF_OPEN → OPEN (recovery failed, "
                           f"next try in {self._current_timeout:.0f}s)")
            return

        if self.consecutive_failures >= self.failure_threshold:
            self.state = CircuitState.OPEN
            logger.warning(f"[CB:{self.name}] CLOSED → OPEN after {self.consecutive_failures} "
                           f"failures (timeout={self._current_timeout:.0f}s)")

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "state": self.state.value,
            "consecutive_failures": self.consecutive_failures,
            "total_successes": self.total_successes,
            "total_failures": self.total_failures,
            "total_rejected": self.total_rejected,
            "last_success": self.last_success_time,
            "last_failure": self.last_failure_time,
            "recovery_timeout": self._current_timeout,
        }


# ═══════════════════════════════════════════════════════════════
#  API Response Cache
# ═══════════════════════════════════════════════════════════════

@dataclass
class CacheEntry:
    data: Any
    timestamp: float
    ttl: float
    hits: int = 0

    @property
    def expired(self) -> bool:
        return time.time() - self.timestamp > self.ttl


class APIResponseCache:
    """
    TTL-based in-memory cache for API responses.

    Different APIs have different TTLs:
      - Contract analysis (GoPlus, TokenSniffer): 300s (contract doesn't change)
      - Market data (DexScreener): 30s (prices change fast)
      - Listing status (CoinGecko): 600s (rare changes)
      - Honeypot check: 120s (tax can change but unlikely)

    Max entries auto-evicts oldest when exceeded.
    """

    def __init__(self, max_entries: int = 5000):
        self._cache: dict[str, CacheEntry] = {}
        self._max_entries = max_entries
        self._total_hits = 0
        self._total_misses = 0

    def get(self, key: str) -> Any | None:
        """Get cached value if exists and not expired."""
        entry = self._cache.get(key)
        if entry is None:
            self._total_misses += 1
            return None
        if entry.expired:
            del self._cache[key]
            self._total_misses += 1
            return None
        entry.hits += 1
        self._total_hits += 1
        return entry.data

    def set(self, key: str, data: Any, ttl: float = 120.0):
        """Store value with a TTL in seconds."""
        # Evict if over limit
        if len(self._cache) >= self._max_entries:
            self._evict_oldest()
        self._cache[key] = CacheEntry(data=data, timestamp=time.time(), ttl=ttl)

    def invalidate(self, key: str):
        """Remove a specific cache entry."""
        self._cache.pop(key, None)

    def invalidate_prefix(self, prefix: str):
        """Remove all entries with keys starting with the prefix."""
        to_remove = [k for k in self._cache if k.startswith(prefix)]
        for k in to_remove:
            del self._cache[k]

    def clear(self):
        """Remove all cache entries."""
        self._cache.clear()

    def _evict_oldest(self, count: int = 100):
        """Evict oldest entries to make room."""
        if not self._cache:
            return
        sorted_keys = sorted(self._cache, key=lambda k: self._cache[k].timestamp)
        for k in sorted_keys[:count]:
            del self._cache[k]

    def cleanup_expired(self):
        """Remove all expired entries (call periodically)."""
        now = time.time()
        expired = [k for k, v in self._cache.items() if now - v.timestamp > v.ttl]
        for k in expired:
            del self._cache[k]
        return len(expired)

    @property
    def stats(self) -> dict:
        return {
            "entries": len(self._cache),
            "max_entries": self._max_entries,
            "total_hits": self._total_hits,
            "total_misses": self._total_misses,
            "hit_rate": round(
                self._total_hits / max(1, self._total_hits + self._total_misses) * 100, 1
            ),
        }


# ═══════════════════════════════════════════════════════════════
#  API Health Monitor
# ═══════════════════════════════════════════════════════════════

@dataclass
class APICallMetric:
    """Single API call metric."""
    api_name: str
    success: bool
    latency_ms: float
    timestamp: float
    error: str = ""
    cached: bool = False


class APIHealthMonitor:
    """
    Tracks per-API health metrics: success rate, avg latency,
    recent errors, uptime estimates.
    """

    def __init__(self, window_size: int = 100):
        self._window_size = window_size
        self._metrics: dict[str, list[APICallMetric]] = defaultdict(list)
        self._api_first_seen: dict[str, float] = {}

    def record(self, metric: APICallMetric):
        """Record an API call result."""
        name = metric.api_name
        if name not in self._api_first_seen:
            self._api_first_seen[name] = time.time()
        self._metrics[name].append(metric)
        # Keep only last N
        if len(self._metrics[name]) > self._window_size:
            self._metrics[name] = self._metrics[name][-self._window_size:]

    def get_api_stats(self, api_name: str) -> dict:
        """Get stats for a single API."""
        calls = self._metrics.get(api_name, [])
        if not calls:
            return {"status": "unknown", "calls": 0}

        total = len(calls)
        successes = sum(1 for c in calls if c.success)
        failures = total - successes
        cached = sum(1 for c in calls if c.cached)
        avg_latency = sum(c.latency_ms for c in calls if not c.cached) / max(1, total - cached)

        # Last 5 calls for trend
        recent = calls[-5:]
        recent_success = sum(1 for c in recent if c.success)

        # Determine status
        if recent_success == 0 and len(recent) >= 3:
            status = "down"
        elif recent_success < len(recent) * 0.5:
            status = "degraded"
        else:
            status = "healthy"

        # Last error
        errors = [c for c in calls if not c.success]
        last_error = errors[-1].error if errors else ""

        return {
            "status": status,
            "calls": total,
            "successes": successes,
            "failures": failures,
            "cached": cached,
            "success_rate": round(successes / max(1, total) * 100, 1),
            "avg_latency_ms": round(avg_latency, 1),
            "last_error": str(last_error)[:100] if last_error else "",
            "last_call": calls[-1].timestamp if calls else 0,
        }

    def get_health_report(self) -> dict:
        """Get health stats for all tracked APIs."""
        report = {}
        for api_name in sorted(self._metrics.keys()):
            report[api_name] = self.get_api_stats(api_name)
        return report


# ═══════════════════════════════════════════════════════════════
#  Retry Executor
# ═══════════════════════════════════════════════════════════════

class RetryConfig:
    """Configuration for retry behavior."""
    __slots__ = ("max_retries", "base_delay", "max_delay", "jitter")

    def __init__(self, max_retries: int = 2, base_delay: float = 1.0,
                 max_delay: float = 10.0, jitter: bool = True):
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.jitter = jitter


async def retry_with_backoff(
    coro_factory: Callable[[], Coroutine],
    config: RetryConfig | None = None,
    api_name: str = "",
) -> Any:
    """
    Execute a coroutine with exponential backoff retry.

    Args:
        coro_factory: Callable that returns a NEW coroutine each time
                      (not an already-awaited coro)
        config: Retry configuration
        api_name: Name for logging

    Returns:
        The result of the coroutine, or raises the last exception
    """
    cfg = config or RetryConfig()
    last_exc = None
    import random

    for attempt in range(1 + cfg.max_retries):
        try:
            return await coro_factory()
        except Exception as e:
            last_exc = e
            if attempt < cfg.max_retries:
                delay = min(cfg.base_delay * (2 ** attempt), cfg.max_delay)
                if cfg.jitter:
                    delay *= (0.5 + random.random() * 0.5)
                logger.debug(f"[RETRY:{api_name}] Attempt {attempt + 1} failed: {e}. "
                             f"Retry in {delay:.1f}s")
                await asyncio.sleep(delay)
    raise last_exc


# ═══════════════════════════════════════════════════════════════
#  Resilient API Manager
# ═══════════════════════════════════════════════════════════════

# Default TTLs per API (seconds)
DEFAULT_TTL = {
    "honeypot":     120,    # Honeypot status rarely changes
    "goplus":       300,    # Contract security flags are static
    "dexscreener":  30,     # Market data changes fast
    "coingecko":    600,    # Listing status rarely changes
    "tokensniffer": 300,    # Scam score is static for a contract
    "binance":      15,     # Price data
}

# Default retry configs per API
DEFAULT_RETRY = {
    "honeypot":     RetryConfig(max_retries=1, base_delay=0.5),
    "goplus":       RetryConfig(max_retries=1, base_delay=0.5),
    "dexscreener":  RetryConfig(max_retries=2, base_delay=0.5),     # Most critical for liq data
    "coingecko":    RetryConfig(max_retries=0),                      # Low priority, often rate-limited
    "tokensniffer": RetryConfig(max_retries=0),                      # Low priority
    "binance":      RetryConfig(max_retries=1, base_delay=0.3),
}

# Circuit breaker thresholds
DEFAULT_CB = {
    "honeypot":     {"failure_threshold": 3, "recovery_timeout": 30},
    "goplus":       {"failure_threshold": 3, "recovery_timeout": 60},
    "dexscreener":  {"failure_threshold": 5, "recovery_timeout": 20},    # Important, recover fast
    "coingecko":    {"failure_threshold": 2, "recovery_timeout": 120},   # Aggressive rate limiting
    "tokensniffer": {"failure_threshold": 3, "recovery_timeout": 60},
    "binance":      {"failure_threshold": 5, "recovery_timeout": 15},
}


class ResilientAPIManager:
    """
    Central API resilience orchestrator.

    Wraps every external API call with:
      1. Circuit breaker check (skip if API is known-down)
      2. Cache lookup (return cached result if available)
      3. Retry with exponential backoff
      4. Metrics recording (latency, success/failure)
      5. Cache store (on success)

    Usage:
        manager = ResilientAPIManager()

        # Wrap an API call
        result = await manager.call(
            "goplus",
            lambda: check_goplus_api(session, address),
            cache_key=f"goplus:{address}",
        )

        # Get health dashboard
        report = manager.get_health_report()
    """

    def __init__(self, config: dict | None = None):
        self._config = config or {}
        self.cache = APIResponseCache(max_entries=self._config.get("max_cache_entries", 5000))
        self.health = APIHealthMonitor(window_size=self._config.get("metrics_window", 100))

        # Create circuit breakers per API
        self._breakers: dict[str, CircuitBreaker] = {}
        for api_name, cb_config in DEFAULT_CB.items():
            self._breakers[api_name] = CircuitBreaker(
                name=api_name,
                **cb_config,
            )

        # Lock for cleanup task
        self._cleanup_task: asyncio.Task | None = None
        self._running = False

    def get_breaker(self, api_name: str) -> CircuitBreaker:
        """Get or create a circuit breaker for an API."""
        if api_name not in self._breakers:
            self._breakers[api_name] = CircuitBreaker(
                name=api_name,
                failure_threshold=3,
                recovery_timeout=30,
            )
        return self._breakers[api_name]

    async def call(
        self,
        api_name: str,
        coro_factory: Callable[[], Coroutine],
        cache_key: str = "",
        cache_ttl: float | None = None,
        retry_config: RetryConfig | None = None,
        fallback: Any = None,
    ) -> Any:
        """
        Execute an API call with full resilience stack.

        Args:
            api_name: API identifier (e.g. "goplus", "honeypot")
            coro_factory: Callable that returns a fresh coroutine
            cache_key: If provided, results are cached/looked up
            cache_ttl: Override default TTL for this API
            retry_config: Override default retry behavior
            fallback: Value to return if all retries + circuit breaker fail

        Returns:
            API response data, cached data, or fallback value
        """
        breaker = self.get_breaker(api_name)
        ttl = cache_ttl or DEFAULT_TTL.get(api_name, 120)
        retry = retry_config or DEFAULT_RETRY.get(api_name, RetryConfig())

        # ── 1. Cache check ──
        if cache_key:
            cached = self.cache.get(cache_key)
            if cached is not None:
                self.health.record(APICallMetric(
                    api_name=api_name, success=True, latency_ms=0,
                    timestamp=time.time(), cached=True,
                ))
                return cached

        # ── 2. Circuit breaker check ──
        if not breaker.can_execute():
            logger.debug(f"[API:{api_name}] Circuit OPEN — skipping call "
                         f"({breaker.consecutive_failures} failures)")
            self.health.record(APICallMetric(
                api_name=api_name, success=False, latency_ms=0,
                timestamp=time.time(), error="circuit_open",
            ))
            # Return stale cache if available
            if cache_key:
                stale = self.cache.get(cache_key)
                if stale is not None:
                    return stale
            return fallback

        # ── 3. Execute with retry ──
        t0 = time.time()
        try:
            result = await retry_with_backoff(
                coro_factory, config=retry, api_name=api_name,
            )
            latency_ms = (time.time() - t0) * 1000

            # ── 4. Record success ──
            breaker.record_success()
            self.health.record(APICallMetric(
                api_name=api_name, success=True, latency_ms=latency_ms,
                timestamp=time.time(),
            ))

            # ── 5. Cache store ──
            if cache_key and result is not None:
                self.cache.set(cache_key, result, ttl=ttl)

            return result

        except Exception as e:
            latency_ms = (time.time() - t0) * 1000
            breaker.record_failure()
            self.health.record(APICallMetric(
                api_name=api_name, success=False, latency_ms=latency_ms,
                timestamp=time.time(), error=str(e)[:200],
            ))
            logger.warning(f"[API:{api_name}] Failed after {retry.max_retries + 1} attempts: {e}")

            # Return stale cache on failure
            if cache_key:
                stale = self.cache.get(cache_key)
                if stale is not None:
                    logger.info(f"[API:{api_name}] Returning stale cache for {cache_key}")
                    return stale

            return fallback

    def get_health_report(self) -> dict:
        """Full health report: circuit breaker states + API metrics + cache stats."""
        return {
            "apis": {
                name: {
                    "circuit_breaker": self._breakers[name].to_dict(),
                    **self.health.get_api_stats(name),
                }
                for name in sorted(self._breakers.keys())
            },
            "cache": self.cache.stats,
            "summary": self._build_summary(),
        }

    def get_api_status(self, api_name: str) -> dict:
        """Quick status check for a single API."""
        breaker = self._breakers.get(api_name)
        if not breaker:
            return {"state": "unknown"}
        return {
            "state": breaker.state.value,
            "ok": breaker.state == CircuitState.CLOSED,
            **self.health.get_api_stats(api_name),
        }

    def _build_summary(self) -> dict:
        """Build overall system health summary."""
        total_apis = len(self._breakers)
        healthy = sum(1 for b in self._breakers.values() if b.state == CircuitState.CLOSED)
        degraded = sum(1 for b in self._breakers.values() if b.state == CircuitState.HALF_OPEN)
        down = sum(1 for b in self._breakers.values() if b.state == CircuitState.OPEN)

        if down > total_apis * 0.5:
            overall = "critical"
        elif down > 0 or degraded > 0:
            overall = "degraded"
        else:
            overall = "healthy"

        return {
            "overall_status": overall,
            "total_apis": total_apis,
            "healthy": healthy,
            "degraded": degraded,
            "down": down,
        }

    async def start_background_tasks(self):
        """Start periodic cache cleanup."""
        self._running = True
        self._cleanup_task = asyncio.create_task(self._periodic_cleanup())

    async def stop(self):
        """Stop background tasks."""
        self._running = False
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass

    async def _periodic_cleanup(self):
        """Periodically clean expired cache entries."""
        while self._running:
            try:
                await asyncio.sleep(60)
                removed = self.cache.cleanup_expired()
                if removed > 0:
                    logger.debug(f"[CACHE] Cleaned {removed} expired entries, "
                                 f"{self.cache.stats['entries']} remaining")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"[CACHE] Cleanup error: {e}")

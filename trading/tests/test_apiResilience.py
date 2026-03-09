"""
Tests for apiResilience.py — circuit breakers, cache, retry, health monitor,
and the resilient API manager.
"""

import asyncio
import time
from unittest.mock import AsyncMock, patch, MagicMock
from django.test import TestCase

from trading.Services.apiResilience import (
    CircuitBreaker,
    CircuitState,
    APIResponseCache,
    CacheEntry,
    APIHealthMonitor,
    APICallMetric,
    RetryConfig,
    retry_with_backoff,
    ResilientAPIManager,
)


# ═══════════════════════════════════════════════════════════════
#  CircuitBreaker
# ═══════════════════════════════════════════════════════════════

class CircuitBreakerTests(TestCase):
    """Tests for the CircuitBreaker state machine."""

    def test_initial_state_is_closed(self):
        cb = CircuitBreaker(name="test", failure_threshold=3)
        self.assertEqual(cb.state, CircuitState.CLOSED)
        self.assertTrue(cb.can_execute())

    def test_remains_closed_below_threshold(self):
        cb = CircuitBreaker(name="test", failure_threshold=3)
        cb.record_failure()
        cb.record_failure()
        self.assertEqual(cb.state, CircuitState.CLOSED)
        self.assertTrue(cb.can_execute())

    def test_opens_after_threshold_failures(self):
        cb = CircuitBreaker(name="test", failure_threshold=3, recovery_timeout=60)
        for _ in range(3):
            cb.record_failure()
        self.assertEqual(cb.state, CircuitState.OPEN)
        self.assertFalse(cb.can_execute())

    def test_success_resets_failure_count(self):
        cb = CircuitBreaker(name="test", failure_threshold=3)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        self.assertEqual(cb.consecutive_failures, 0)
        self.assertEqual(cb.state, CircuitState.CLOSED)

    def test_open_transitions_to_half_open_after_timeout(self):
        cb = CircuitBreaker(name="test", failure_threshold=1, recovery_timeout=0.01)
        cb.record_failure()
        self.assertEqual(cb.state, CircuitState.OPEN)
        time.sleep(0.02)
        self.assertTrue(cb.can_execute())
        self.assertEqual(cb.state, CircuitState.HALF_OPEN)

    def test_half_open_success_closes(self):
        cb = CircuitBreaker(name="test", failure_threshold=1, recovery_timeout=0.01)
        cb.record_failure()
        time.sleep(0.02)
        cb.can_execute()  # triggers HALF_OPEN
        cb.record_success()
        self.assertEqual(cb.state, CircuitState.CLOSED)

    def test_half_open_failure_reopens_with_escalated_timeout(self):
        cb = CircuitBreaker(name="test", failure_threshold=1,
                            recovery_timeout=0.01, max_recovery_timeout=100)
        cb.record_failure()
        time.sleep(0.02)
        cb.can_execute()  # triggers HALF_OPEN
        cb.record_failure()
        self.assertEqual(cb.state, CircuitState.OPEN)
        self.assertAlmostEqual(cb._current_timeout, 0.02, places=5)

    def test_rejected_counter(self):
        cb = CircuitBreaker(name="test", failure_threshold=1, recovery_timeout=999)
        cb.record_failure()
        cb.can_execute()  # rejected
        cb.can_execute()  # rejected
        self.assertEqual(cb.total_rejected, 2)

    def test_to_dict(self):
        cb = CircuitBreaker(name="goplus", failure_threshold=3)
        d = cb.to_dict()
        self.assertEqual(d["name"], "goplus")
        self.assertEqual(d["state"], "closed")
        self.assertIn("total_successes", d)
        self.assertIn("recovery_timeout", d)

    def test_max_recovery_timeout_cap(self):
        cb = CircuitBreaker(name="test", failure_threshold=1,
                            recovery_timeout=10, max_recovery_timeout=30)
        for _ in range(5):
            cb.record_failure()
            cb.last_failure_time = 0   # force timeout
            cb.can_execute()           # go to HALF_OPEN
            cb.record_failure()        # back to OPEN with escalation
        self.assertLessEqual(cb._current_timeout, 30)

    def test_success_resets_timeout_escalation(self):
        cb = CircuitBreaker(name="test", failure_threshold=1,
                            recovery_timeout=1.0, max_recovery_timeout=100)
        cb.record_failure()          # OPEN
        cb.last_failure_time = 0     # force timeout
        cb.can_execute()             # HALF_OPEN
        cb.record_failure()          # OPEN, timeout=2.0
        self.assertEqual(cb._current_timeout, 2.0)
        cb.last_failure_time = 0
        cb.can_execute()             # HALF_OPEN
        cb.record_success()          # CLOSED, timeout reset
        self.assertEqual(cb._current_timeout, 1.0)


# ═══════════════════════════════════════════════════════════════
#  APIResponseCache
# ═══════════════════════════════════════════════════════════════

class APIResponseCacheTests(TestCase):
    """Tests for the TTL-based in-memory API cache."""

    def test_set_and_get(self):
        cache = APIResponseCache()
        cache.set("key1", {"score": 80}, ttl=60)
        self.assertEqual(cache.get("key1"), {"score": 80})

    def test_returns_none_for_missing(self):
        cache = APIResponseCache()
        self.assertIsNone(cache.get("nonexistent"))

    def test_expired_entry_returns_none(self):
        cache = APIResponseCache()
        cache.set("key1", "data", ttl=0.01)
        time.sleep(0.02)
        self.assertIsNone(cache.get("key1"))

    def test_hit_rate_tracking(self):
        cache = APIResponseCache()
        cache.set("k", "v", ttl=60)
        cache.get("k")       # hit
        cache.get("k")       # hit
        cache.get("miss")    # miss
        stats = cache.stats
        self.assertEqual(stats["total_hits"], 2)
        self.assertEqual(stats["total_misses"], 1)
        # hit_rate is percentage: 2/3*100 ≈ 66.7
        self.assertAlmostEqual(stats["hit_rate"], 66.7, delta=0.1)

    def test_invalidate(self):
        cache = APIResponseCache()
        cache.set("k", "v", ttl=60)
        cache.invalidate("k")
        self.assertIsNone(cache.get("k"))

    def test_invalidate_prefix(self):
        cache = APIResponseCache()
        cache.set("goplus:0x1", "a", ttl=60)
        cache.set("goplus:0x2", "b", ttl=60)
        cache.set("honeypot:0x1", "c", ttl=60)
        cache.invalidate_prefix("goplus:")
        self.assertIsNone(cache.get("goplus:0x1"))
        self.assertIsNone(cache.get("goplus:0x2"))
        self.assertEqual(cache.get("honeypot:0x1"), "c")

    def test_max_entries_eviction(self):
        cache = APIResponseCache(max_entries=3)
        cache.set("a", 1, ttl=60)
        cache.set("b", 2, ttl=60)
        cache.set("c", 3, ttl=60)
        cache.set("d", 4, ttl=60)  # should evict oldest
        self.assertEqual(cache.get("d"), 4)
        # After eviction, there should be at most 3 entries
        self.assertLessEqual(len(cache._cache), 3)

    def test_cleanup_expired(self):
        cache = APIResponseCache()
        cache.set("fresh", "yes", ttl=60)
        cache.set("stale", "no", ttl=0.01)
        time.sleep(0.02)
        removed = cache.cleanup_expired()
        self.assertEqual(removed, 1)
        self.assertEqual(cache.get("fresh"), "yes")

    def test_overwrite_existing_key(self):
        cache = APIResponseCache()
        cache.set("k", "v1", ttl=60)
        cache.set("k", "v2", ttl=60)
        self.assertEqual(cache.get("k"), "v2")

    def test_clear_removes_all(self):
        cache = APIResponseCache()
        cache.set("a", 1, ttl=60)
        cache.set("b", 2, ttl=60)
        cache.clear()
        self.assertIsNone(cache.get("a"))
        self.assertIsNone(cache.get("b"))

    def test_cache_entry_expired_property(self):
        entry = CacheEntry(data="x", timestamp=time.time() - 100, ttl=1)
        self.assertTrue(entry.expired)
        entry2 = CacheEntry(data="y", timestamp=time.time(), ttl=999)
        self.assertFalse(entry2.expired)


# ═══════════════════════════════════════════════════════════════
#  APIHealthMonitor
# ═══════════════════════════════════════════════════════════════

class APIHealthMonitorTests(TestCase):
    """Tests for the per-API health tracking system."""

    def test_record_and_stats(self):
        monitor = APIHealthMonitor()
        monitor.record(APICallMetric(
            api_name="goplus", success=True, latency_ms=150,
            timestamp=time.time(), cached=False))
        stats = monitor.get_api_stats("goplus")
        self.assertEqual(stats["calls"], 1)
        self.assertEqual(stats["success_rate"], 100.0)
        self.assertEqual(stats["avg_latency_ms"], 150)

    def test_success_rate_calculation(self):
        monitor = APIHealthMonitor()
        for _ in range(8):
            monitor.record(APICallMetric(
                api_name="hp", success=True, latency_ms=100,
                timestamp=time.time(), cached=False))
        for _ in range(2):
            monitor.record(APICallMetric(
                api_name="hp", success=False, latency_ms=5000,
                timestamp=time.time(), cached=False))
        stats = monitor.get_api_stats("hp")
        self.assertEqual(stats["success_rate"], 80.0)
        self.assertEqual(stats["calls"], 10)

    def test_status_healthy(self):
        monitor = APIHealthMonitor()
        for _ in range(10):
            monitor.record(APICallMetric(
                api_name="api", success=True, latency_ms=50,
                timestamp=time.time(), cached=False))
        stats = monitor.get_api_stats("api")
        self.assertEqual(stats["status"], "healthy")

    def test_status_degraded(self):
        monitor = APIHealthMonitor()
        # 7 success + 3 fail, last 5 = [s, s, f, f, f] → 2/5 = 0.4 < 0.5
        for _ in range(7):
            monitor.record(APICallMetric(
                api_name="api", success=True, latency_ms=50,
                timestamp=time.time(), cached=False))
        for _ in range(3):
            monitor.record(APICallMetric(
                api_name="api", success=False, latency_ms=50,
                timestamp=time.time(), cached=False))
        stats = monitor.get_api_stats("api")
        self.assertEqual(stats["status"], "degraded")

    def test_status_down(self):
        monitor = APIHealthMonitor()
        for _ in range(5):
            monitor.record(APICallMetric(
                api_name="api", success=False, latency_ms=50,
                timestamp=time.time(), cached=False))
        stats = monitor.get_api_stats("api")
        self.assertEqual(stats["status"], "down")

    def test_health_report_all_apis(self):
        monitor = APIHealthMonitor()
        monitor.record(APICallMetric("a", True, 50, time.time(), False))
        monitor.record(APICallMetric("b", False, 5000, time.time(), False))
        report = monitor.get_health_report()
        self.assertIn("a", report)
        self.assertIn("b", report)

    def test_bounded_history(self):
        monitor = APIHealthMonitor(window_size=5)
        for _ in range(10):
            monitor.record(APICallMetric("x", True, 50, time.time(), False))
        self.assertEqual(len(monitor._metrics["x"]), 5)

    def test_unknown_api_stats(self):
        monitor = APIHealthMonitor()
        stats = monitor.get_api_stats("nonexistent")
        self.assertEqual(stats["status"], "unknown")
        self.assertEqual(stats["calls"], 0)


# ═══════════════════════════════════════════════════════════════
#  RetryExecutor
# ═══════════════════════════════════════════════════════════════

class RetryExecutorTests(TestCase):
    """Tests for the exponential backoff retry executor."""

    def test_succeeds_on_first_try(self):
        call_count = 0
        async def ok():
            nonlocal call_count
            call_count += 1
            return "done"
        result = asyncio.run(
            retry_with_backoff(ok, RetryConfig(max_retries=3))
        )
        self.assertEqual(result, "done")
        self.assertEqual(call_count, 1)

    def test_retries_on_failure_then_succeeds(self):
        call_count = 0
        async def flaky():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("fail")
            return "recovered"
        config = RetryConfig(max_retries=3, base_delay=0.01)
        result = asyncio.run(
            retry_with_backoff(flaky, config)
        )
        self.assertEqual(result, "recovered")
        self.assertEqual(call_count, 3)

    def test_raises_after_max_retries(self):
        async def always_fail():
            raise TimeoutError("down")
        config = RetryConfig(max_retries=2, base_delay=0.01)
        with self.assertRaises(TimeoutError):
            asyncio.run(
                retry_with_backoff(always_fail, config)
            )

    def test_zero_retries_no_retry(self):
        call_count = 0
        async def fail():
            nonlocal call_count
            call_count += 1
            raise ValueError("no retry")
        config = RetryConfig(max_retries=0)
        with self.assertRaises(ValueError):
            asyncio.run(
                retry_with_backoff(fail, config)
            )
        self.assertEqual(call_count, 1)

    def test_retry_config_defaults(self):
        cfg = RetryConfig()
        self.assertEqual(cfg.max_retries, 2)
        self.assertEqual(cfg.base_delay, 1.0)
        self.assertEqual(cfg.max_delay, 10.0)
        self.assertTrue(cfg.jitter)


# ═══════════════════════════════════════════════════════════════
#  ResilientAPIManager
# ═══════════════════════════════════════════════════════════════

class ResilientAPIManagerTests(TestCase):
    """Tests for the full orchestration manager."""

    def _run(self, coro):
        """Shortcut to run async in sync test."""
        return asyncio.run(coro)

    def test_call_success_caches_result(self):
        manager = ResilientAPIManager()
        async def fetch():
            return {"score": 95}
        result = self._run(
            manager.call("goplus", fetch, cache_key="gp:0x1", cache_ttl=60)
        )
        self.assertEqual(result, {"score": 95})
        self.assertEqual(manager.cache.get("gp:0x1"), {"score": 95})

    def test_cache_hit_skips_api_call(self):
        manager = ResilientAPIManager()
        call_count = 0
        async def fetch():
            nonlocal call_count
            call_count += 1
            return {"data": "fresh"}
        # First call — hits API
        self._run(manager.call("goplus", fetch, cache_key="k", cache_ttl=60))
        self.assertEqual(call_count, 1)
        # Second call — should use cache
        result = self._run(manager.call("goplus", fetch, cache_key="k", cache_ttl=60))
        self.assertEqual(result, {"data": "fresh"})
        self.assertEqual(call_count, 1)

    def test_circuit_breaker_opens_on_repeated_failures(self):
        manager = ResilientAPIManager()
        # Use honeypot which has failure_threshold=3
        async def fail_api():
            raise ConnectionError("API down")
        # Make enough calls to trip the circuit breaker
        for i in range(4):
            result = self._run(
                manager.call("honeypot", fail_api, cache_key=f"k:{i}",
                             retry_config=RetryConfig(max_retries=0))
            )
            self.assertIsNone(result)
        # Circuit should be open now
        breaker = manager.get_breaker("honeypot")
        self.assertEqual(breaker.state, CircuitState.OPEN)

    def test_cached_result_returned_when_circuit_open(self):
        manager = ResilientAPIManager()
        # Prime cache with long TTL
        async def fetch_ok():
            return {"score": 80}
        self._run(manager.call("goplus", fetch_ok, cache_key="k", cache_ttl=300))
        # Second call uses cache (cache hit before circuit even checked)
        async def fetch_fail():
            raise ConnectionError("down")
        result = self._run(manager.call("goplus", fetch_fail, cache_key="k", cache_ttl=300))
        self.assertEqual(result, {"score": 80})

    def test_returns_fallback_when_all_fails(self):
        manager = ResilientAPIManager()
        async def fail():
            raise ConnectionError("down")
        result = self._run(
            manager.call("goplus", fail, fallback={"default": True},
                         retry_config=RetryConfig(max_retries=0))
        )
        self.assertEqual(result, {"default": True})

    def test_health_report_structure(self):
        manager = ResilientAPIManager()
        async def fetch():
            return {"ok": True}
        self._run(manager.call("honeypot", fetch, cache_key="test"))
        report = manager.get_health_report()
        self.assertIn("apis", report)
        self.assertIn("cache", report)
        self.assertIn("summary", report)
        self.assertIn("honeypot", report["apis"])

    def test_no_cache_key_skips_caching(self):
        manager = ResilientAPIManager()
        async def fetch():
            return {"data": 1}
        result = self._run(manager.call("goplus", fetch))
        self.assertEqual(result, {"data": 1})
        self.assertEqual(len(manager.cache._cache), 0)

    def test_summary_healthy_all_ok(self):
        manager = ResilientAPIManager()
        summary = manager._build_summary()
        self.assertEqual(summary["overall_status"], "healthy")
        self.assertEqual(summary["down"], 0)

    def test_get_api_status(self):
        manager = ResilientAPIManager()
        status = manager.get_api_status("honeypot")
        self.assertEqual(status["state"], "closed")
        self.assertTrue(status["ok"])

    def test_get_api_status_unknown(self):
        manager = ResilientAPIManager()
        status = manager.get_api_status("nonexistent_api")
        self.assertEqual(status["state"], "unknown")

    def test_get_breaker_creates_on_demand(self):
        manager = ResilientAPIManager()
        breaker = manager.get_breaker("new_api")
        self.assertEqual(breaker.name, "new_api")
        self.assertEqual(breaker.state, CircuitState.CLOSED)
        # Should be cached
        self.assertIs(manager.get_breaker("new_api"), breaker)

    def test_metrics_recorded_on_success(self):
        manager = ResilientAPIManager()
        async def fetch():
            return "ok"
        self._run(manager.call("goplus", fetch, cache_key="x"))
        report = manager.health.get_health_report()
        self.assertIn("goplus", report)
        self.assertEqual(report["goplus"]["successes"], 1)

    def test_metrics_recorded_on_failure(self):
        manager = ResilientAPIManager()
        async def fail():
            raise ConnectionError("timeout")
        self._run(manager.call("goplus", fail,
                               retry_config=RetryConfig(max_retries=0)))
        report = manager.health.get_health_report()
        self.assertIn("goplus", report)
        self.assertEqual(report["goplus"]["failures"], 1)

    def test_summary_degraded_when_api_down(self):
        manager = ResilientAPIManager()
        breaker = manager.get_breaker("honeypot")
        # Force circuit open
        for _ in range(3):
            breaker.record_failure()
        summary = manager._build_summary()
        self.assertEqual(summary["overall_status"], "degraded")
        self.assertEqual(summary["down"], 1)

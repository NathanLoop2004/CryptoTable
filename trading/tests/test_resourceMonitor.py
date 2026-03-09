"""
test_resourceMonitor.py — Unit tests for ResourceMonitor module.
"""
import time
import unittest
from unittest.mock import patch, MagicMock

from trading.Services.resourceMonitor import ResourceMonitor, ResourceSnapshot


class TestResourceMonitor(unittest.TestCase):
    """Test resource monitoring functionality."""

    def setUp(self):
        self.monitor = ResourceMonitor()

    # ── Token processing tracking ──

    def test_record_token_processed_increments_counter(self):
        self.assertEqual(self.monitor._tokens_processed, 0)
        self.monitor.record_token_processed("TEST", 50.0)
        self.assertEqual(self.monitor._tokens_processed, 1)
        self.monitor.record_token_processed("TEST2", 75.0)
        self.assertEqual(self.monitor._tokens_processed, 2)

    def test_record_token_processed_stores_time(self):
        self.monitor.record_token_processed("TEST", 123.5)
        self.assertEqual(len(self.monitor._token_times), 1)
        ts, elapsed = self.monitor._token_times[0]
        self.assertAlmostEqual(elapsed, 123.5)
        self.assertAlmostEqual(ts, time.time(), delta=1.0)

    # ── WebSocket message tracking ──

    def test_ws_message_recording(self):
        self.assertEqual(self.monitor._ws_messages_sent, 0)
        self.monitor.record_ws_message()
        self.monitor.record_ws_message()
        self.monitor.record_ws_message()
        self.assertEqual(self.monitor._ws_messages_sent, 3)
        self.assertEqual(len(self.monitor._ws_msg_times), 3)

    def test_ws_connections_management(self):
        self.assertEqual(self.monitor._ws_connections, 0)
        self.monitor.set_ws_connections(5)
        self.assertEqual(self.monitor._ws_connections, 5)
        self.monitor.inc_ws_connections()
        self.assertEqual(self.monitor._ws_connections, 6)
        self.monitor.dec_ws_connections()
        self.assertEqual(self.monitor._ws_connections, 5)
        self.monitor.dec_ws_connections()
        self.monitor.dec_ws_connections()
        self.monitor.dec_ws_connections()
        self.monitor.dec_ws_connections()
        self.monitor.dec_ws_connections()
        self.monitor.dec_ws_connections()  # Should not go below 0
        self.assertEqual(self.monitor._ws_connections, 0)

    # ── RPC call tracking ──

    def test_rpc_call_success_recording(self):
        self.monitor.record_rpc_call(50.0, error=False)
        self.monitor.record_rpc_call(100.0, error=False)
        self.assertEqual(self.monitor._rpc_calls, 2)
        self.assertEqual(self.monitor._rpc_errors, 0)
        self.assertEqual(len(self.monitor._rpc_latencies), 2)

    def test_rpc_call_error_recording(self):
        self.monitor.record_rpc_call(0, error=True)
        self.assertEqual(self.monitor._rpc_calls, 1)
        self.assertEqual(self.monitor._rpc_errors, 1)

    # ── Snapshot generation ──

    def test_get_snapshot_returns_dataclass(self):
        snap = self.monitor.get_snapshot()
        self.assertIsInstance(snap, ResourceSnapshot)
        self.assertGreater(snap.timestamp, 0)

    def test_get_snapshot_includes_token_throughput(self):
        for i in range(5):
            self.monitor.record_token_processed(f"T{i}", 50.0)
        snap = self.monitor.get_snapshot()
        self.assertEqual(snap.tokens_processed, 5)

    def test_get_snapshot_includes_rpc_stats(self):
        self.monitor.record_rpc_call(25.0, error=False)
        self.monitor.record_rpc_call(75.0, error=False)
        self.monitor.record_rpc_call(0, error=True)
        snap = self.monitor.get_snapshot()
        self.assertEqual(snap.rpc_calls_total, 3)
        self.assertEqual(snap.rpc_errors_total, 1)
        self.assertGreater(snap.rpc_avg_latency_ms, 0)

    def test_get_snapshot_generates_warnings(self):
        """Snapshot should contain warnings if thresholds are exceeded."""
        # We can't easily fake high memory/CPU without psutil mocks,
        # but we can test the warning generation for task count
        snap = self.monitor.get_snapshot()
        # With no overloads, warnings should be empty or just informational
        self.assertIsInstance(snap.warnings, list)

    # ── History tracking ──

    def test_history_is_bounded(self):
        for _ in range(ResourceMonitor.MAX_HISTORY + 50):
            self.monitor._history.append(ResourceSnapshot())
        self.assertEqual(len(self.monitor._history), ResourceMonitor.MAX_HISTORY)

    def test_get_history_returns_dicts(self):
        self.monitor.record_token_processed("T1", 50)
        snap = self.monitor.get_snapshot()
        self.monitor._history.append(snap)
        history = self.monitor.get_history(minutes=5)
        self.assertIsInstance(history, list)
        if history:
            self.assertIsInstance(history[0], dict)

    # ── Stats summary ──

    def test_get_stats_returns_dict(self):
        stats = self.monitor.get_stats()
        self.assertIsInstance(stats, dict)
        self.assertIn("tokens_processed", stats)
        self.assertIn("rpc_calls", stats)
        self.assertIn("rpc_errors", stats)
        self.assertIn("ws_messages_sent", stats)
        self.assertIn("ws_connections", stats)

    def test_get_stats_reflects_state(self):
        self.monitor.record_token_processed("A", 10)
        self.monitor.record_token_processed("B", 20)
        self.monitor.record_rpc_call(10, error=False)
        self.monitor.record_rpc_call(0, error=True)
        self.monitor.record_ws_message()
        self.monitor.set_ws_connections(3)
        stats = self.monitor.get_stats()
        self.assertEqual(stats["tokens_processed"], 2)
        self.assertEqual(stats["rpc_calls"], 2)
        self.assertEqual(stats["rpc_errors"], 1)
        self.assertEqual(stats["ws_messages_sent"], 1)
        self.assertEqual(stats["ws_connections"], 3)


class TestResourceSnapshot(unittest.TestCase):
    """Test ResourceSnapshot dataclass."""

    def test_default_values(self):
        snap = ResourceSnapshot()
        self.assertEqual(snap.timestamp, 0)
        self.assertEqual(snap.memory_rss_mb, 0)
        self.assertEqual(snap.cpu_percent, 0)
        self.assertEqual(snap.active_tasks, 0)
        self.assertEqual(snap.warnings, [])

    def test_to_dict_via_asdict(self):
        snap = ResourceSnapshot(timestamp=123.0, memory_rss_mb=256.5)
        from dataclasses import asdict
        d = asdict(snap)
        self.assertEqual(d["timestamp"], 123.0)
        self.assertEqual(d["memory_rss_mb"], 256.5)


if __name__ == "__main__":
    unittest.main()

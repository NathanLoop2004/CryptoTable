"""
test_alertService.py — Unit tests for AlertService module.
"""
import asyncio
import time
import unittest
from unittest.mock import patch, MagicMock, AsyncMock

from trading.Services.alertService import AlertService, AlertEvent, LEVEL_ORDER, DEFAULT_CONFIG


class TestAlertEvent(unittest.TestCase):
    """Test AlertEvent dataclass."""

    def test_default_values(self):
        event = AlertEvent()
        self.assertEqual(event.level, "info")
        self.assertEqual(event.category, "general")
        self.assertEqual(event.title, "")
        self.assertEqual(event.message, "")
        self.assertEqual(event.token, "")
        self.assertEqual(event.channels_sent, [])
        self.assertTrue(event.success)

    def test_to_dict(self):
        event = AlertEvent(
            timestamp=1000.0,
            level="warning",
            category="token",
            title="Test Alert",
            message="Something happened",
            token="0x1234",
            symbol="TEST",
        )
        d = event.to_dict()
        self.assertEqual(d["level"], "warning")
        self.assertEqual(d["category"], "token")
        self.assertEqual(d["token"], "0x1234")
        self.assertIsInstance(d, dict)


class TestLevelOrder(unittest.TestCase):
    """Test alert level ordering."""

    def test_level_hierarchy(self):
        self.assertLess(LEVEL_ORDER["info"], LEVEL_ORDER["warning"])
        self.assertLess(LEVEL_ORDER["warning"], LEVEL_ORDER["error"])
        self.assertLess(LEVEL_ORDER["error"], LEVEL_ORDER["critical"])

    def test_all_levels_present(self):
        for level in ("info", "warning", "error", "critical"):
            self.assertIn(level, LEVEL_ORDER)


class TestAlertService(unittest.TestCase):
    """Test AlertService core functionality."""

    def setUp(self):
        self.service = AlertService(config={
            "telegram_enabled": False,
            "discord_enabled": False,
            "email_enabled": False,
            "log_file": "logs/test_alerts.log",
        })

    def test_default_config(self):
        svc = AlertService()
        self.assertFalse(svc.config["telegram_enabled"])
        self.assertFalse(svc.config["discord_enabled"])
        self.assertFalse(svc.config["email_enabled"])
        self.assertEqual(svc.config["max_alerts_per_hour"], 60)

    def test_config_override(self):
        svc = AlertService(config={"max_alerts_per_hour": 100})
        self.assertEqual(svc.config["max_alerts_per_hour"], 100)

    def test_auto_title_generation(self):
        title = AlertService._auto_title("warning", "token")
        self.assertIn("Warning", title)
        self.assertIn("Token Alert", title)

    def test_auto_title_critical(self):
        title = AlertService._auto_title("critical", "rug")
        self.assertIn("CRITICAL", title)
        self.assertIn("Rug", title)

    def test_auto_title_rpc(self):
        title = AlertService._auto_title("error", "rpc")
        self.assertIn("Error", title)
        self.assertIn("RPC", title)

    # ── Rate limiting ──

    def test_rate_limit_passes_first_call(self):
        result = self.service._check_rate_limit("token")
        self.assertTrue(result)

    def test_rate_limit_blocks_rapid_calls(self):
        self.service._check_rate_limit("token")
        # Second call within min_interval should be blocked
        result = self.service._check_rate_limit("token")
        self.assertFalse(result)

    def test_rate_limit_different_categories_independent(self):
        self.service._check_rate_limit("token")
        # Different category should pass
        result = self.service._check_rate_limit("rpc")
        self.assertTrue(result)

    def test_rate_limit_hourly_cap(self):
        self.service.config["min_interval_seconds"] = 0   # Disable per-category cooldown
        self.service.config["max_alerts_per_hour"] = 3
        self.assertTrue(self.service._check_rate_limit("a"))
        self.assertTrue(self.service._check_rate_limit("b"))
        self.assertTrue(self.service._check_rate_limit("c"))
        self.assertFalse(self.service._check_rate_limit("d"))  # Over hourly cap

    def test_rate_limit_hourly_reset(self):
        self.service.config["min_interval_seconds"] = 0
        self.service.config["max_alerts_per_hour"] = 1
        self.service._check_rate_limit("test")
        # Simulate hour passing
        self.service._hourly_reset = time.time() - 3601
        result = self.service._check_rate_limit("test2")
        self.assertTrue(result)

    # ── Send (async) ──

    def test_send_with_no_channels(self):
        """Send should succeed even without any channels enabled."""
        loop = asyncio.new_event_loop()
        try:
            event = loop.run_until_complete(
                self.service.send("Test message", level="info")
            )
            self.assertIsInstance(event, AlertEvent)
            self.assertTrue(event.success)
        finally:
            loop.run_until_complete(self.service.close())
            loop.close()

    def test_send_logs_to_history(self):
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(
                self.service.send("Alert 1", level="info", category="test1")
            )
            loop.run_until_complete(
                self.service.send("Alert 2", level="warning", category="test2")
            )
            self.assertEqual(len(self.service._history), 2)
        finally:
            loop.run_until_complete(self.service.close())
            loop.close()

    def test_send_rate_limited_event(self):
        """Rapid sends of same category should produce rate-limited entry."""
        self.service.config["min_interval_seconds"] = 10
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(
                self.service.send("First", category="repeat")
            )
            event = loop.run_until_complete(
                self.service.send("Second", category="repeat")
            )
            self.assertIn("rate_limited", event.channels_sent)
            self.assertFalse(event.success)
        finally:
            loop.run_until_complete(self.service.close())
            loop.close()

    # ── Stats ──

    def test_get_stats(self):
        stats = self.service.get_stats()
        self.assertIn("total_alerts", stats)
        self.assertIn("alerts_last_hour", stats)
        self.assertIn("alerts_by_level", stats)
        self.assertIn("channels", stats)

    def test_get_history_empty(self):
        history = self.service.get_history()
        self.assertEqual(history, [])

    def test_get_history_limit(self):
        for i in range(10):
            self.service._history.append(AlertEvent(
                timestamp=time.time(),
                message=f"Alert {i}",
            ))
        history = self.service.get_history(limit=5)
        self.assertEqual(len(history), 5)

    # ── Config update ──

    def test_update_config(self):
        self.service.update_config({"telegram_enabled": True, "max_alerts_per_hour": 100})
        self.assertTrue(self.service.config["telegram_enabled"])
        self.assertEqual(self.service.config["max_alerts_per_hour"], 100)

    def test_update_config_ignores_unknown_keys(self):
        self.service.update_config({"nonexistent_key": "value"})
        self.assertNotIn("nonexistent_key", self.service.config)


class TestAlertServiceConvenienceMethods(unittest.TestCase):
    """Test convenience alert methods."""

    def setUp(self):
        self.service = AlertService(config={
            "telegram_enabled": False,
            "discord_enabled": False,
            "email_enabled": False,
            "log_file": "logs/test_alerts.log",
        })

    def _run(self, coro):
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.run_until_complete(self.service.close())
            loop.close()

    def test_alert_token_suspicious(self):
        event = self._run(self.service.alert_token_suspicious(
            "0xABC123", "SCAM", ["High sell tax", "Hidden owner"]
        ))
        self.assertEqual(event.category, "token")
        self.assertEqual(event.level, "warning")
        self.assertIn("SCAM", event.message)

    def test_alert_rpc_error(self):
        event = self._run(self.service.alert_rpc_error(
            "https://rpc.example.com", "Connection timeout"
        ))
        self.assertEqual(event.category, "rpc")
        self.assertEqual(event.level, "error")

    def test_alert_trade_executed(self):
        event = self._run(self.service.alert_trade_executed(
            "TOKEN", "buy", 0.5, "0xTXHASH"
        ))
        self.assertEqual(event.category, "trade")
        self.assertEqual(event.level, "info")
        self.assertIn("BUY", event.message)

    def test_alert_trade_failed(self):
        event = self._run(self.service.alert_trade_failed(
            "TOKEN", "sell", "Insufficient gas"
        ))
        self.assertEqual(event.category, "trade")
        self.assertEqual(event.level, "error")

    def test_alert_rug_detected_critical(self):
        event = self._run(self.service.alert_rug_detected(
            "0xRUG", "RUGTOKEN", "LP removed", 9
        ))
        self.assertEqual(event.category, "rug")
        self.assertEqual(event.level, "critical")

    def test_alert_rug_detected_warning(self):
        event = self._run(self.service.alert_rug_detected(
            "0xRUG", "MAYBE", "Fee increased", 3
        ))
        self.assertEqual(event.level, "warning")

    def test_alert_resource_warning(self):
        event = self._run(self.service.alert_resource_warning(
            ["Memory above 512MB", "CPU at 85%"]
        ))
        self.assertEqual(event.category, "system")
        self.assertEqual(event.level, "warning")


if __name__ == "__main__":
    unittest.main()

"""
test_devTracker.py — Unit tests for DevTracker module.
"""
import asyncio
import time
import unittest
from unittest.mock import MagicMock, AsyncMock, patch
from types import SimpleNamespace

from web3 import Web3

from trading.Services.devTracker import (
    DevTracker, DevProfile, DevCheckResult, DevLaunch,
)


def _make_token_info(**overrides):
    """Create mock token info."""
    defaults = {
        "address": "0x" + "A" * 40,
        "symbol": "DEV",
        "name": "Dev Token",
        "owner_address": "0x" + "B" * 40,
        "is_honeypot": False,
        "is_open_source": True,
        "lp_locked": True,
        "lp_lock_percent": 90.0,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class TestDevLaunch(unittest.TestCase):
    """Test DevLaunch dataclass."""

    def test_default_values(self):
        launch = DevLaunch(token_address="0x123")
        self.assertEqual(launch.symbol, "")
        self.assertFalse(launch.was_rug_pull)
        self.assertEqual(launch.max_roi_x, 0.0)
        self.assertEqual(launch.outcome, "unknown")

    def test_to_dict(self):
        launch = DevLaunch(token_address="0x123", symbol="TEST", max_roi_x=5.0)
        d = launch.to_dict()
        self.assertEqual(d["symbol"], "TEST")
        self.assertEqual(d["max_roi_x"], 5.0)


class TestDevProfile(unittest.TestCase):
    """Test DevProfile dataclass."""

    def test_default_values(self):
        profile = DevProfile(wallet="0x" + "A" * 40)
        self.assertEqual(profile.tokens_launched, 0)
        self.assertEqual(profile.reputation_score, 0)
        self.assertEqual(profile.launches, [])

    def test_to_dict_limits_launches(self):
        """Serialization should limit launches to 10."""
        profile = DevProfile(wallet="0x" + "A" * 40)
        for i in range(20):
            profile.launches.append(DevLaunch(token_address=f"0x{i:040x}"))
        d = profile.to_dict()
        self.assertEqual(len(d["launches"]), 10)


class TestDevCheckResult(unittest.TestCase):
    """Test DevCheckResult dataclass."""

    def test_default_values(self):
        result = DevCheckResult(creator_address="")
        self.assertFalse(result.is_tracked)
        self.assertFalse(result.is_serial_scammer)
        self.assertEqual(result.dev_score, 0)

    def test_to_dict(self):
        result = DevCheckResult(
            creator_address="0x123",
            is_tracked=True,
            dev_score=75,
        )
        d = result.to_dict()
        self.assertTrue(d["is_tracked"])
        self.assertEqual(d["dev_score"], 75)


class TestDevTracker(unittest.TestCase):
    """Test DevTracker core functionality."""

    def setUp(self):
        self.w3_mock = MagicMock(spec=Web3)
        self.w3_mock.eth = MagicMock()
        self.tracker = DevTracker(self.w3_mock, 56, enable_ml=False)

    def _run(self, coro):
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    # ── check_creator tests ──

    def test_check_creator_unknown_dev(self):
        """Unknown dev should get neutral score."""
        info = _make_token_info(
            owner_address="0x" + "C" * 40,
        )
        result = self._run(self.tracker.check_creator("0x" + "A" * 40, info))
        self.assertIsInstance(result, DevCheckResult)
        self.assertEqual(result.dev_score, 50)  # neutral for new dev
        self.assertFalse(result.is_serial_scammer)

    def test_check_creator_no_owner(self):
        """Token with no owner should return empty creator."""
        info = _make_token_info(owner_address="")
        result = self._run(self.tracker.check_creator("0x" + "A" * 40, info))
        self.assertEqual(result.creator_address, "")

    def test_check_creator_tracked_dev(self):
        """Previously tracked dev should be recognized."""
        creator = "0x" + "D" * 40
        self.tracker._devs[creator.lower()] = DevProfile(
            wallet=creator,
            label="Good Dev",
            reputation_score=80,
            successful_launches=5,
            tokens_launched=6,
        )
        info = _make_token_info(owner_address=creator)
        result = self._run(self.tracker.check_creator("0x" + "A" * 40, info))
        self.assertTrue(result.is_tracked)
        self.assertEqual(result.dev_score, 80)
        self.assertEqual(result.dev_label, "Good Dev")

    def test_serial_scammer_detection(self):
        """Dev with 3+ rug pulls should be flagged as serial scammer."""
        creator = "0x" + "E" * 40
        self.tracker._devs[creator.lower()] = DevProfile(
            wallet=creator,
            reputation_score=10,
            rug_pulls=4,
            tokens_launched=5,
        )
        info = _make_token_info(owner_address=creator)
        result = self._run(self.tracker.check_creator("0x" + "A" * 40, info))
        self.assertTrue(result.is_serial_scammer)
        self.assertLessEqual(result.dev_score, 0)

    # ── record_launch tests ──

    def test_record_launch_creates_profile(self):
        """Recording a launch for unknown dev should create profile."""
        creator = "0x" + "F" * 40
        info = _make_token_info()
        self._run(self.tracker.record_launch("0xTOKEN", creator, info, 10000))
        self.assertIn(creator.lower(), self.tracker._devs)
        profile = self.tracker._devs[creator.lower()]
        self.assertEqual(profile.tokens_launched, 1)

    def test_record_launch_increments_count(self):
        """Multiple launches should increment counter."""
        creator = "0x" + "F" * 40
        info = _make_token_info()
        self._run(self.tracker.record_launch("0xTOKEN1", creator, info, 10000))
        self._run(self.tracker.record_launch("0xTOKEN2", creator, info, 20000))
        profile = self.tracker._devs[creator.lower()]
        self.assertEqual(profile.tokens_launched, 2)

    # ── record_outcome tests ──

    def test_record_outcome_success(self):
        """Recording a successful outcome should update stats."""
        creator = "0x" + "F" * 40
        token = "0x" + "1" * 40
        info = _make_token_info()
        self._run(self.tracker.record_launch(token, creator, info, 10000))
        self._run(self.tracker.record_outcome(token, "success", peak_roi=5.0))
        profile = self.tracker._devs[creator.lower()]
        self.assertEqual(profile.successful_launches, 1)
        self.assertEqual(profile.best_multiplier, 5.0)

    def test_record_outcome_rug(self):
        """Recording a rug outcome should increase rug_pulls."""
        creator = "0x" + "F" * 40
        token = "0x" + "2" * 40
        info = _make_token_info()
        self._run(self.tracker.record_launch(token, creator, info, 10000))
        self._run(self.tracker.record_outcome(token, "rug", was_rug=True))
        profile = self.tracker._devs[creator.lower()]
        self.assertEqual(profile.rug_pulls, 1)

    # ── Reputation recalculation ──

    def test_reputation_score_recalculation(self):
        """Reputation should be recalculated after recording outcomes."""
        creator = "0x" + "F" * 40
        info = _make_token_info()
        for i in range(5):
            token = f"0x{i:040x}"
            self._run(self.tracker.record_launch(token, creator, info, 10000))
            self._run(self.tracker.record_outcome(token, "success", peak_roi=3.0 + i))
        profile = self.tracker._devs[creator.lower()]
        # With 5 successful launches, reputation should be good
        self.assertGreater(profile.reputation_score, 50)
        self.assertEqual(profile.successful_launches, 5)

    # ── add_dev / get_tracked_devs ──

    def test_add_dev(self):
        addr = "0x" + "1" * 40
        result = self.tracker.add_dev(addr, label="My Dev", source="manual")
        self.assertIn(addr.lower(), self.tracker._devs)
        self.assertEqual(result["label"], "My Dev")

    def test_get_tracked_devs(self):
        for i in range(5):
            addr = f"0x{i:040x}"
            self.tracker._devs[addr] = DevProfile(
                wallet=Web3.to_checksum_address(addr),
                reputation_score=50 + i * 10,
            )
        result = self.tracker.get_tracked_devs(top_n=3)
        self.assertEqual(len(result), 3)
        # Should be sorted by reputation descending
        self.assertGreaterEqual(result[0]["reputation_score"], result[1]["reputation_score"])

    def test_get_dev_profile_exists(self):
        addr = "0x" + "A" * 40
        self.tracker._devs[addr.lower()] = DevProfile(
            wallet=addr, label="Test Dev", reputation_score=70
        )
        result = self.tracker.get_dev_profile(addr)
        self.assertIsNotNone(result)
        self.assertEqual(result["label"], "Test Dev")

    def test_get_dev_profile_not_exists(self):
        result = self.tracker.get_dev_profile("0x" + "9" * 40)
        self.assertIsNone(result)

    # ── Stats ──

    def test_get_stats(self):
        stats = self.tracker.get_stats()
        self.assertIn("total_tracked", stats)
        self.assertIn("good_devs", stats)
        self.assertIn("known_scammers", stats)
        self.assertIn("tokens_mapped", stats)

    def test_get_stats_reflects_state(self):
        for i in range(3):
            addr = f"0x{i:040x}"
            self.tracker._devs[addr] = DevProfile(
                wallet=addr, reputation_score=80
            )
        stats = self.tracker.get_stats()
        self.assertEqual(stats["total_tracked"], 3)
        self.assertEqual(stats["good_devs"], 3)


if __name__ == "__main__":
    unittest.main()

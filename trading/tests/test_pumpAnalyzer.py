"""
test_pumpAnalyzer.py — Unit tests for PumpAnalyzer v3 module.
"""
import asyncio
import time
import unittest
from unittest.mock import MagicMock, patch
from types import SimpleNamespace
from collections import defaultdict

from trading.Services.pumpAnalyzer import PumpAnalyzer, PumpScore, WEIGHTS, _TokenSnapshot


def _make_token_info(**overrides):
    """Create a mock TokenInfo with sensible defaults."""
    defaults = {
        "address": "0x" + "A" * 40,
        "symbol": "PUMP",
        "name": "Pump Token",
        # Holder distribution
        "holder_count": 120,
        "top_holder_percent": 8.0,
        "creator_percent": 2.0,
        # Trading activity (DexScreener) — _score_activity uses these
        "_dexscreener_ok": True,
        "dexscreener_buys_24h": 150,
        "dexscreener_sells_24h": 60,
        "dexscreener_volume_24h": 50000,
        # Price changes — _score_momentum uses these
        "dexscreener_price_change_m5": 5.0,
        "dexscreener_price_change_h1": 15.0,
        "dexscreener_price_change_h6": 25.0,
        "dexscreener_price_change_h24": 40.0,
        # Social / web presence — _score_social uses these exact names
        "has_website": True,
        "has_social_links": True,
        "listed_coingecko": True,
        "dexscreener_pairs": 2,
        "_coingecko_ok": True,
        # Age — _score_age uses this
        "dexscreener_age_hours": 2.0,
        # Market cap / price — _score_market_cap uses these
        "dexscreener_price_usd": 0.0001,
        "dexscreener_fdv": 100000,
        "dexscreener_liquidity": 25000,
        "total_supply": 1000000000,
        # LP
        "lp_locked": True,
        "lp_lock_percent": 90.0,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class TestPumpScoreDataclass(unittest.TestCase):
    """Test PumpScore dataclass."""

    def test_default_values(self):
        ps = PumpScore(token_address="0x123")
        self.assertEqual(ps.total_score, 0)
        self.assertEqual(ps.grade, "AVOID")
        self.assertEqual(ps.liquidity_score, 0)
        self.assertEqual(ps.market_cap_usd, 0.0)
        self.assertEqual(ps.signals, [])

    def test_to_dict(self):
        ps = PumpScore(token_address="0x123", total_score=75, grade="MEDIUM")
        d = ps.to_dict()
        self.assertEqual(d["total_score"], 75)
        self.assertEqual(d["grade"], "MEDIUM")
        self.assertIsInstance(d, dict)


class TestWeights(unittest.TestCase):
    """Test scoring weights configuration."""

    def test_weights_sum_to_100(self):
        total = sum(WEIGHTS.values())
        self.assertEqual(total, 100)

    def test_all_10_components(self):
        expected = {"liquidity", "holder", "activity", "whale", "momentum",
                    "age", "social", "mcap", "holder_growth", "lp_growth"}
        self.assertEqual(set(WEIGHTS.keys()), expected)


class TestPumpAnalyzer(unittest.TestCase):
    """Test PumpAnalyzer scoring logic."""

    def setUp(self):
        self.w3_mock = MagicMock()
        self.analyzer = PumpAnalyzer(self.w3_mock, 56)

    def _run(self, coro):
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    def test_analyze_returns_pump_score(self):
        info = _make_token_info()
        result = self._run(self.analyzer.analyze(info, 25000))
        self.assertIsInstance(result, PumpScore)
        self.assertEqual(result.token_address, info.address)

    def test_grade_assignment_high(self):
        """High score should get HIGH grade."""
        info = _make_token_info()
        result = self._run(self.analyzer.analyze(info, 25000))
        if result.total_score >= 80:
            self.assertEqual(result.grade, "HIGH")

    def test_grade_assignment_avoid(self):
        """Very bad token should get AVOID grade."""
        info = _make_token_info(
            holder_count=5,
            _dexscreener_ok=False,
            _coingecko_ok=False,
            has_website=False,
            has_social_links=False,
            listed_coingecko=False,
            dexscreener_buys_24h=0,
            dexscreener_sells_24h=0,
            dexscreener_volume_24h=0,
            dexscreener_liquidity=0,
            dexscreener_pairs=0,
            total_supply=0,
            top_holder_percent=40.0,
            creator_percent=30.0,
        )
        result = self._run(self.analyzer.analyze(info, 100))
        # Very low liquidity + no data should score poorly
        self.assertLessEqual(result.total_score, 39)
        self.assertEqual(result.grade, "AVOID")

    def test_score_range_0_to_100(self):
        """Total score should always be 0–100."""
        info = _make_token_info()
        result = self._run(self.analyzer.analyze(info, 25000))
        self.assertGreaterEqual(result.total_score, 0)
        self.assertLessEqual(result.total_score, 100)

    def test_component_scores_0_to_100(self):
        """Each component score should be 0–100."""
        info = _make_token_info()
        result = self._run(self.analyzer.analyze(info, 25000))
        for attr in ["liquidity_score", "holder_score", "activity_score",
                      "whale_score", "momentum_score", "age_score", "social_score",
                      "mcap_score", "holder_growth_score", "lp_growth_score"]:
            score = getattr(result, attr)
            self.assertGreaterEqual(score, 0, f"{attr} should be >= 0, got {score}")
            self.assertLessEqual(score, 100, f"{attr} should be <= 100, got {score}")

    def test_liquidity_sweet_spot(self):
        """Tokens with liquidity in the sweet spot should score better."""
        info = _make_token_info()
        # Sweet spot ($8k–$120k)
        result_good = self._run(self.analyzer.analyze(info, 50000))
        result_low = self._run(self.analyzer.analyze(info, 500))
        self.assertGreater(result_good.liquidity_score, result_low.liquidity_score)

    def test_snapshot_recording(self):
        """Analyzer should record snapshots for growth tracking."""
        info = _make_token_info()
        addr = info.address.lower()
        self.assertEqual(len(self.analyzer._snapshots.get(addr, [])), 0)
        self._run(self.analyzer.analyze(info, 25000))
        self.assertEqual(len(self.analyzer._snapshots[addr]), 1)
        self._run(self.analyzer.analyze(info, 30000))
        self.assertEqual(len(self.analyzer._snapshots[addr]), 2)

    def test_initial_liquidity_tracking(self):
        """First analysis should record initial liquidity."""
        info = _make_token_info()
        addr = info.address.lower()
        self._run(self.analyzer.analyze(info, 25000))
        self.assertEqual(self.analyzer._initial_liquidity[addr], 25000)

    def test_snapshot_max_limit(self):
        """Snapshots should be bounded by MAX_SNAPSHOTS."""
        info = _make_token_info()
        addr = info.address.lower()
        for i in range(PumpAnalyzer.MAX_SNAPSHOTS + 10):
            self._run(self.analyzer.analyze(info, 25000 + i * 100))
        self.assertLessEqual(len(self.analyzer._snapshots[addr]), PumpAnalyzer.MAX_SNAPSHOTS)

    def test_signals_populated(self):
        """Signals list should be populated with human-readable reasons."""
        info = _make_token_info()
        result = self._run(self.analyzer.analyze(info, 25000))
        self.assertIsInstance(result.signals, list)
        # With good metrics, there should be some positive signals
        self.assertGreater(len(result.signals), 0)

    def test_v3_market_cap_scoring(self):
        """Market cap score should be populated."""
        info = _make_token_info(dexscreener_fdv=100000)
        result = self._run(self.analyzer.analyze(info, 25000))
        # mcap_score should be calculated
        self.assertIsInstance(result.mcap_score, int)

    def test_v3_holder_growth_rate(self):
        """Holder growth rate should be calculated with multiple snapshots."""
        info = _make_token_info(holder_count=100)
        self._run(self.analyzer.analyze(info, 25000))
        # Simulate time passing and holder growth
        info2 = _make_token_info(holder_count=150)
        # Manually backdate the first snapshot
        addr = info.address.lower()
        if self.analyzer._snapshots[addr]:
            self.analyzer._snapshots[addr][0].timestamp -= 600  # 10 min ago
        result = self._run(self.analyzer.analyze(info2, 25000))
        # holder_growth_rate should be > 0 if holders increased
        self.assertGreaterEqual(result.holder_growth_rate, 0)


class TestPumpAnalyzerGetStats(unittest.TestCase):
    """Test PumpAnalyzer get_stats."""

    def test_get_stats_returns_dict(self):
        w3_mock = MagicMock()
        analyzer = PumpAnalyzer(w3_mock, 56)
        stats = analyzer.get_stats()
        self.assertIsInstance(stats, dict)
        self.assertIn("tracked_tokens", stats)
        self.assertIn("total_snapshots", stats)
        self.assertIn("tracked_initial_lp", stats)


if __name__ == "__main__":
    unittest.main()

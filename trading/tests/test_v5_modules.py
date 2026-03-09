"""
test_v5_modules.py — Unit tests for Professional v5 modules.

Tests:
  - ProxyDetector, StressTester, VolatilitySlippageCalc (swapSimulator v5)
  - PumpDumpPredictor, DevReputationML, AnomalyDetector (mlPredictor)
  - SocialSentimentAnalyzer (socialSentiment)
  - DynamicContractScanner (dynamicContractScanner)
  - SmartMoneyTracker whale analysis (smartMoneyTracker v5)
  - DevTracker ML integration (devTracker v5)
"""
import asyncio
import time
import unittest
from unittest.mock import MagicMock, AsyncMock, patch
from types import SimpleNamespace
from dataclasses import asdict

from trading.Services.swapSimulator import (
    ProxyDetector, StressTester, VolatilitySlippageCalc,
    StressTestResult, ProxyAnalysis, VolatilitySlippage,
)
from trading.Services.mlPredictor import (
    PumpDumpPredictor, DevReputationML, AnomalyDetector,
    PumpDumpPrediction, DevReputationPrediction, AnomalyResult,
)
from trading.Services.socialSentiment import SocialSentimentAnalyzer, SocialSentimentResult
from trading.Services.dynamicContractScanner import DynamicContractScanner, ContractSnapshot, DynamicScanResult
from trading.Services.smartMoneyTracker import WhaleAlert, WhaleActivity
from trading.Services.devTracker import DevTracker, DevCheckResult, DevProfile


def _make_token_info(**overrides):
    """Create a mock TokenInfo with sensible defaults for v5 testing."""
    defaults = {
        "address": "0x" + "A" * 40,
        "symbol": "TEST",
        "name": "Test Token",
        # Security
        "is_honeypot": False,
        "buy_tax": 3.0,
        "sell_tax": 5.0,
        "is_open_source": True,
        "is_proxy": False,
        "has_blacklist": False,
        "is_mintable": False,
        "can_self_destruct": False,
        "has_hidden_owner": False,
        "can_take_back_ownership": False,
        "can_pause_trading": False,
        "is_true_token": True,
        "is_airdrop_scam": False,
        "is_anti_whale": False,
        "cannot_sell_all": False,
        "owner_can_change_balance": False,
        # LP
        "lp_locked": True,
        "lp_lock_percent": 95.0,
        "lp_lock_hours_remaining": 720,
        # Holders
        "holder_count": 200,
        "top_holder_percent": 5.0,
        "creator_percent": 2.0,
        # Market data
        "_dexscreener_ok": True,
        "dexscreener_buys_24h": 100,
        "dexscreener_sells_24h": 40,
        "dexscreener_volume_24h": 30000,
        "dexscreener_liquidity": 50000,
        "dexscreener_age_hours": 3.0,
        "dexscreener_price_change_m5": 5.0,
        "dexscreener_price_change_h1": 10.0,
        "dexscreener_price_change_h6": 20.0,
        "dexscreener_price_change_h24": 30.0,
        # Social
        "has_website": True,
        "has_social_links": True,
        "listed_coingecko": False,
        "dexscreener_pairs": 1,
        # Smart money
        "smart_money_buyers": 2,
        "smart_money_confidence": 0.7,
        # Dev
        "dev_score": 60,
        "dev_is_tracked": True,
        "dev_is_serial_scammer": False,
        "_dev_ok": True,
        "owner_address": "0x" + "B" * 40,
        # Simulation
        "sim_is_honeypot": False,
        "sim_buy_tax": 3.0,
        "sim_sell_tax": 5.0,
        "sim_can_buy": True,
        "sim_can_sell": True,
        "_simulation_ok": True,
        # Bytecode
        "bytecode_has_selfdestruct": False,
        "bytecode_has_delegatecall": False,
        "bytecode_is_proxy": False,
        "bytecode_flags": [],
        "_bytecode_ok": True,
        # Pump
        "pump_score": 65,
        "pump_grade": "MEDIUM",
        "pump_holder_growth_rate": 0,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# ═══════════════════════════════════════════════════════════════
#  ProxyDetector Tests
# ═══════════════════════════════════════════════════════════════

class TestProxyAnalysisDataclass(unittest.TestCase):
    def test_default_values(self):
        pa = ProxyAnalysis()
        self.assertFalse(pa.is_proxy)
        self.assertEqual(pa.proxy_type, "")
        self.assertEqual(pa.risk_level, "unknown")
        self.assertFalse(pa.has_multisig)
        self.assertFalse(pa.has_timelock)

    def test_proxy_detected(self):
        pa = ProxyAnalysis(
            is_proxy=True,
            proxy_type="transparent",
            risk_level="dangerous",
        )
        self.assertTrue(pa.is_proxy)
        self.assertEqual(pa.proxy_type, "transparent")


class TestProxyDetector(unittest.TestCase):
    def test_init(self):
        w3 = MagicMock()
        detector = ProxyDetector(w3)
        self.assertIsNotNone(detector)

    def test_analyze_non_proxy(self):
        """Non-proxy contract should return safe result."""
        w3 = MagicMock()
        # Mock eth.get_storage_at to return empty bytes (no proxy slots)
        w3.eth.get_storage_at.return_value = b'\x00' * 32
        # Mock eth.get_code to return some bytecode (not a minimal proxy)
        w3.eth.get_code.return_value = b'\x60\x80\x60\x40' + b'\x00' * 100
        w3.to_checksum_address = lambda x: x

        detector = ProxyDetector(w3)
        result = asyncio.run(
            detector.analyze("0x" + "A" * 40)
        )
        self.assertIsInstance(result, ProxyAnalysis)
        self.assertFalse(result.is_proxy)
        self.assertEqual(result.risk_level, "safe")


# ═══════════════════════════════════════════════════════════════
#  StressTester Tests
# ═══════════════════════════════════════════════════════════════

class TestStressTestResult(unittest.TestCase):
    def test_default_values(self):
        r = StressTestResult(token_address="0x123")
        self.assertEqual(r.liquidity_depth_score, 0)
        self.assertEqual(r.max_safe_amount_wei, 0)
        self.assertEqual(r.slippage_curve, [])


class TestStressTester(unittest.TestCase):
    def test_init(self):
        w3 = MagicMock()
        w3.to_checksum_address = lambda x: x
        w3.eth.contract = MagicMock()
        st = StressTester(w3, "0x" + "1" * 40, "0x" + "2" * 40)
        self.assertIsNotNone(st)


# ═══════════════════════════════════════════════════════════════
#  VolatilitySlippageCalc Tests
# ═══════════════════════════════════════════════════════════════

class TestVolatilitySlippage(unittest.TestCase):
    def test_default_values(self):
        vs = VolatilitySlippage()
        self.assertEqual(vs.volatility_score, 0)
        self.assertGreater(vs.recommended_slippage_pct, 0)

    def test_calculate_from_dexscreener(self):
        """Should calculate slippage from DexScreener price changes."""
        calc = VolatilitySlippageCalc()
        dex_data = {
            "price_change_m5": 15.0,
            "price_change_h1": 30.0,
            "price_change_h6": 50.0,
        }
        result = asyncio.run(
            calc.calculate("0x" + "A" * 40, dexscreener_data=dex_data)
        )
        self.assertIsInstance(result, VolatilitySlippage)
        self.assertGreater(result.volatility_score, 0)
        self.assertGreaterEqual(result.recommended_slippage_pct, 5)
        self.assertLessEqual(result.recommended_slippage_pct, 30)

    def test_low_volatility(self):
        """Low price changes → low recommended slippage."""
        calc = VolatilitySlippageCalc()
        dex_data = {
            "price_change_m5": 0.5,
            "price_change_h1": 1.0,
            "price_change_h6": 2.0,
        }
        result = asyncio.run(
            calc.calculate("0x" + "A" * 40, dexscreener_data=dex_data)
        )
        self.assertLess(result.recommended_slippage_pct, 15)


# ═══════════════════════════════════════════════════════════════
#  PumpDumpPredictor Tests
# ═══════════════════════════════════════════════════════════════

class TestPumpDumpPrediction(unittest.TestCase):
    def test_default_values(self):
        p = PumpDumpPrediction(token_address="0x123")
        self.assertEqual(p.ml_score, 50)
        self.assertEqual(p.pump_probability, 0.5)
        self.assertEqual(p.dump_probability, 0.5)

    def test_to_dict(self):
        p = PumpDumpPrediction(token_address="0x123", ml_score=75)
        d = asdict(p)
        self.assertEqual(d["ml_score"], 75)


class TestPumpDumpPredictor(unittest.TestCase):
    def test_predict_safe_token(self):
        """A good token (verified, locked LP, low taxes) should score high."""
        predictor = PumpDumpPredictor()
        token = _make_token_info()
        result = predictor.predict(token)
        self.assertIsInstance(result, PumpDumpPrediction)
        self.assertGreater(result.ml_score, 40)

    def test_predict_scam_token(self):
        """A scam token (honeypot, hidden owner, no LP lock) should score low."""
        predictor = PumpDumpPredictor()
        token = _make_token_info(
            is_honeypot=True,
            has_hidden_owner=True,
            lp_locked=False,
            lp_lock_percent=0,
            is_open_source=False,
            sell_tax=50,
            is_mintable=True,
            holder_count=5,
            top_holder_percent=80,
            sim_is_honeypot=True,
            bytecode_has_selfdestruct=True,
            pump_score=10,
        )
        result = predictor.predict(token)
        self.assertLess(result.ml_score, 40)

    def test_record_outcome(self):
        """Online learning should not crash."""
        predictor = PumpDumpPredictor()
        # Record multiple outcomes to trigger mini-batch
        for i in range(15):
            predictor.record_outcome("0x" + "A" * 40, {}, was_pump=(i % 2 == 0))
        # Should not raise


class TestDevReputationML(unittest.TestCase):
    def test_predict_new_dev(self):
        """New developer with no history should get neutral cluster."""
        ml = DevReputationML()
        profile = DevProfile(wallet="0x" + "C" * 40)
        result = ml.predict(profile)
        self.assertIsInstance(result, DevReputationPrediction)
        self.assertIn(result.cluster, ("legit_dev", "neutral", "suspicious", "scammer", "unknown"))

    def test_predict_known_scammer(self):
        """Dev with many rug pulls should be flagged."""
        ml = DevReputationML()
        profile = DevProfile(
            wallet="0x" + "D" * 40,
            tokens_launched=10,
            rug_pulls=8,
            successful_launches=0,
            avg_roi=0.5,
        )
        result = ml.predict(profile)
        self.assertIn(result.cluster, ("suspicious", "scammer"))

    def test_link_wallets(self):
        """Linking wallets should not crash."""
        ml = DevReputationML()
        ml.link_wallets("0xAAA", "0xBBB", "funded_by")
        self.assertIn("0xaaa", ml._funding_graph)


class TestAnomalyDetector(unittest.TestCase):
    def test_detect_normal_token(self):
        """Normal token should not be anomalous."""
        detector = AnomalyDetector()
        token = _make_token_info()
        result = detector.detect(token)
        self.assertIsInstance(result, AnomalyResult)
        self.assertFalse(result.is_anomalous)

    def test_detect_volume_spike(self):
        """Extreme volume/liquidity ratio should trigger anomaly."""
        detector = AnomalyDetector()
        token = _make_token_info(
            dexscreener_volume_24h=1000000,
            dexscreener_liquidity=1000,
            dexscreener_price_change_m5=60.0,  # also triggers price pattern
        )
        result = detector.detect(token, liquidity_usd=1000)
        self.assertTrue(result.is_anomalous)
        self.assertEqual(result.anomaly_type, "volume_spike")

    def test_detect_buy_sell_anomaly(self):
        """Extreme buy/sell ratio should trigger anomaly."""
        detector = AnomalyDetector()
        token = _make_token_info(
            dexscreener_buys_24h=500,
            dexscreener_sells_24h=2,
            dexscreener_price_change_m5=60.0,  # triggers price pattern too
        )
        result = detector.detect(token)
        self.assertTrue(result.is_anomalous)
        self.assertIn(result.anomaly_type, ("buy_sell_ratio_anomaly", "price_pattern"))


# ═══════════════════════════════════════════════════════════════
#  SocialSentimentAnalyzer Tests
# ═══════════════════════════════════════════════════════════════

class TestSocialSentimentResult(unittest.TestCase):
    def test_default_values(self):
        r = SocialSentimentResult(token_address="0x123")
        self.assertEqual(r.sentiment_score, 50)
        self.assertEqual(r.sentiment_label, "neutral")
        self.assertEqual(r.platforms_active, 0)


class TestSocialSentimentAnalyzer(unittest.TestCase):
    def test_init(self):
        analyzer = SocialSentimentAnalyzer()
        self.assertIsNotNone(analyzer)

    def test_analyze_returns_result(self):
        """analyze should return SocialSentimentResult without crashing."""
        analyzer = SocialSentimentAnalyzer()
        token = _make_token_info()
        result = asyncio.run(
            analyzer.analyze(token.address, token_symbol=token.symbol, token_info=token)
        )
        self.assertIsInstance(result, SocialSentimentResult)
        self.assertGreaterEqual(result.sentiment_score, 0)
        self.assertLessEqual(result.sentiment_score, 100)


# ═══════════════════════════════════════════════════════════════
#  DynamicContractScanner Tests
# ═══════════════════════════════════════════════════════════════

class TestContractSnapshot(unittest.TestCase):
    def test_default_values(self):
        snap = ContractSnapshot(token_address="0x123")
        self.assertEqual(snap.bytecode_hash, "")
        self.assertEqual(snap.total_supply, 0)


class TestDynamicContractScanner(unittest.TestCase):
    def test_init(self):
        w3 = MagicMock()
        scanner = DynamicContractScanner(w3, 56)
        self.assertIsNotNone(scanner)
        self.assertFalse(scanner.running)

    def test_register_and_unregister(self):
        """Register/unregister should work without crashing."""
        w3 = MagicMock()
        w3.eth.get_code.return_value = b'\x60\x80' + b'\x00' * 50
        w3.eth.get_storage_at.return_value = b'\x00' * 32
        w3.eth.call.return_value = b'\x00' * 32
        w3.to_checksum_address = lambda x: x
        w3.keccak.return_value = b'\x01' * 32

        scanner = DynamicContractScanner(w3, 56)
        token = _make_token_info()

        # Register
        asyncio.run(
            scanner.register("0x" + "A" * 40, symbol="TEST", pair_address="0xPAIR")
        )
        self.assertIn(("0x" + "a" * 40), scanner._contracts)

        # Unregister
        scanner.unregister("0x" + "A" * 40)
        self.assertNotIn(("0x" + "a" * 40), scanner._contracts)

    def test_is_blocked_unregistered(self):
        """Unregistered token should not be blocked."""
        w3 = MagicMock()
        scanner = DynamicContractScanner(w3, 56)
        blocked, reason = scanner.is_blocked("0xNONEXISTENT")
        self.assertFalse(blocked)


# ═══════════════════════════════════════════════════════════════
#  WhaleActivity Tests
# ═══════════════════════════════════════════════════════════════

class TestWhaleAlert(unittest.TestCase):
    def test_default_values(self):
        alert = WhaleAlert(token_address="0x123", alert_type="large_buy")
        self.assertEqual(alert.severity, "info")
        self.assertFalse(alert.is_suspicious)


class TestWhaleActivity(unittest.TestCase):
    def test_default_values(self):
        wa = WhaleActivity(token_address="0x123")
        self.assertEqual(wa.total_whale_buys, 0)
        self.assertEqual(wa.total_whale_sells, 0)
        self.assertFalse(wa.coordinated_buying)
        self.assertFalse(wa.dev_dumping)
        self.assertEqual(wa.risk_score, 50)


# ═══════════════════════════════════════════════════════════════
#  DevTracker v5 ML Integration Tests
# ═══════════════════════════════════════════════════════════════

class TestDevCheckResultV5(unittest.TestCase):
    def test_ml_fields_present(self):
        """DevCheckResult should have v5 ML fields."""
        r = DevCheckResult(creator_address="0x123")
        self.assertEqual(r.ml_reputation_score, 50)
        self.assertEqual(r.ml_cluster, "neutral")
        self.assertEqual(r.ml_confidence, 0.0)
        self.assertEqual(r.ml_linked_wallets, 0)


class TestDevTrackerMLInit(unittest.TestCase):
    def test_ml_enabled(self):
        """DevTracker with enable_ml=True should have _ml_predictor."""
        w3 = MagicMock()
        tracker = DevTracker(w3, 56, enable_ml=True)
        self.assertIsNotNone(tracker._ml_predictor)

    def test_ml_disabled(self):
        """DevTracker with enable_ml=False should NOT have _ml_predictor."""
        w3 = MagicMock()
        tracker = DevTracker(w3, 56, enable_ml=False)
        self.assertIsNone(tracker._ml_predictor)

    def test_link_wallets(self):
        """link_wallets should delegate to ML predictor."""
        w3 = MagicMock()
        tracker = DevTracker(w3, 56, enable_ml=True)
        tracker.link_wallets("0xAAA", "0xBBB", "funded_by")
        # Should not crash

    def test_stats_include_ml(self):
        """get_stats should include ML stats when enabled."""
        w3 = MagicMock()
        tracker = DevTracker(w3, 56, enable_ml=True)
        stats = tracker.get_stats()
        self.assertIn("ml_enabled", stats)
        self.assertTrue(stats["ml_enabled"])


# ═══════════════════════════════════════════════════════════════
#  Integration: ML Predictor with multiple token profiles
# ═══════════════════════════════════════════════════════════════

class TestMLPredictorIntegration(unittest.TestCase):
    def test_multiple_predictions_consistent(self):
        """Same token should yield consistent predictions."""
        predictor = PumpDumpPredictor()
        token = _make_token_info()
        r1 = predictor.predict(token)
        r2 = predictor.predict(token)
        self.assertEqual(r1.ml_score, r2.ml_score)

    def test_better_token_scores_higher(self):
        """A clearly better token should score higher than a clearly bad one."""
        predictor = PumpDumpPredictor()
        good = _make_token_info(
            is_honeypot=False,
            lp_locked=True,
            lp_lock_percent=99,
            is_open_source=True,
            pump_score=90,
            smart_money_buyers=5,
            dev_score=80,
        )
        bad = _make_token_info(
            is_honeypot=True,
            lp_locked=False,
            lp_lock_percent=0,
            is_open_source=False,
            pump_score=5,
            smart_money_buyers=0,
            dev_score=0,
            sell_tax=90,
            has_hidden_owner=True,
            bytecode_has_selfdestruct=True,
        )
        good_result = predictor.predict(good)
        bad_result = predictor.predict(bad)
        self.assertGreater(good_result.ml_score, bad_result.ml_score)


if __name__ == "__main__":
    unittest.main()

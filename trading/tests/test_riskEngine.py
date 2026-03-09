"""
test_riskEngine.py — Unit tests for RiskEngine module.
"""
import time
import unittest
from unittest.mock import MagicMock
from types import SimpleNamespace

from trading.Services.riskEngine import (
    RiskEngine, RiskDecision, DEFAULT_WEIGHTS,
)


def _make_token_info(**overrides):
    """Create a mock TokenInfo with sensible defaults for testing."""
    defaults = {
        "address": "0x" + "A" * 40,
        "symbol": "TEST",
        "name": "Test Token",
        "risk": "safe",
        "buy_tax": 3.0,
        "sell_tax": 5.0,
        "is_honeypot": False,
        "is_airdrop_scam": False,
        "_goplus_ok": True,
        "is_open_source": True,
        "is_proxy": False,
        "has_hidden_owner": False,
        "can_take_back_ownership": False,
        "is_mintable": False,
        "has_blacklist": False,
        "can_pause_trading": False,
        "owner_can_change_balance": False,
        "can_self_destruct": False,
        "cannot_sell_all": False,
        "is_true_token": True,
        "top_holder_percent": 5.0,
        "creator_percent": 3.0,
        "_honeypot_ok": True,
        "_dexscreener_ok": True,
        "_coingecko_ok": False,
        "_tokensniffer_ok": True,
        "tokensniffer_is_scam": False,
        "tokensniffer_score": 85,
        # Simulation fields
        "_simulation_ok": False,
        "sim_is_honeypot": False,
        "sim_can_buy": True,
        "sim_can_sell": True,
        "sim_buy_tax": 3.0,
        "sim_sell_tax": 5.0,
        "sim_honeypot_reason": "",
        # Bytecode fields
        "_bytecode_ok": False,
        "bytecode_has_selfdestruct": False,
        "bytecode_has_delegatecall": False,
        "bytecode_is_proxy": False,
        "bytecode_size": 5000,
        "bytecode_flags": [],
        # LP lock
        "lp_locked": True,
        "lp_lock_percent": 95.0,
        "lp_lock_hours_remaining": 720.0,
        # DexScreener price
        "dexscreener_price_change_h1": 5.0,
        "dexscreener_price_change_h24": 20.0,
        # Holder
        "holder_count": 150,
        "owner_address": "0x" + "B" * 40,
        # Risk engine
        "_risk_engine_ok": False,
        "risk_engine_score": 0,
        "risk_engine_action": "",
        "risk_engine_confidence": 0,
        "risk_engine_hard_stop": False,
        "risk_engine_hard_stop_reason": "",
        "risk_engine_signals": [],
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class TestRiskEngineInit(unittest.TestCase):
    """Test RiskEngine initialization."""

    def test_default_weights_sum_to_100(self):
        total = sum(DEFAULT_WEIGHTS.values())
        self.assertEqual(total, 100)

    def test_engine_normalizes_weights(self):
        """If weights don't sum to 100, engine should normalize them."""
        bad_weights = {"security": 50, "pump": 50, "dev": 50,
                       "smart_money": 50, "simulation": 50, "bytecode": 50, "market": 50}
        engine = RiskEngine(weights=bad_weights)
        total = sum(engine.weights.values())
        # After normalization should be ~100 (rounding may differ slightly)
        self.assertAlmostEqual(total, 100, delta=2)

    def test_engine_default_weights(self):
        engine = RiskEngine()
        self.assertEqual(engine.weights, DEFAULT_WEIGHTS)


class TestRiskDecision(unittest.TestCase):
    """Test RiskDecision dataclass."""

    def test_default_values(self):
        dec = RiskDecision(token_address="0x123")
        self.assertEqual(dec.final_score, 0)
        self.assertEqual(dec.action, "IGNORE")
        self.assertEqual(dec.confidence, 0.0)
        self.assertFalse(dec.hard_stop)

    def test_to_dict(self):
        dec = RiskDecision(token_address="0x123", final_score=75, action="BUY")
        d = dec.to_dict()
        self.assertEqual(d["final_score"], 75)
        self.assertEqual(d["action"], "BUY")


class TestRiskEngineEvaluate(unittest.TestCase):
    """Test the evaluate() method with various token profiles."""

    def setUp(self):
        self.engine = RiskEngine()

    def test_honeypot_hard_stop(self):
        """Honeypot token should result in IGNORE with hard_stop."""
        info = _make_token_info(is_honeypot=True)
        decision = self.engine.evaluate(token_info=info)
        self.assertTrue(decision.hard_stop)
        self.assertEqual(decision.action, "IGNORE")
        self.assertEqual(decision.final_score, 0)

    def test_airdrop_scam_hard_stop(self):
        """Airdrop scam should trigger hard stop."""
        info = _make_token_info(is_airdrop_scam=True)
        decision = self.engine.evaluate(token_info=info)
        self.assertTrue(decision.hard_stop)
        self.assertEqual(decision.action, "IGNORE")

    def test_tokensniffer_scam_hard_stop(self):
        """TokenSniffer confirmed scam should hard stop."""
        info = _make_token_info(tokensniffer_is_scam=True)
        decision = self.engine.evaluate(token_info=info)
        self.assertTrue(decision.hard_stop)
        self.assertEqual(decision.final_score, 0)

    def test_serial_scammer_dev_hard_stop(self):
        """Serial scammer dev should trigger hard stop."""
        info = _make_token_info()
        dev_result = SimpleNamespace(
            is_serial_scammer=True,
            dev_score=0,
            is_tracked=True,
            successful_launches=0,
            rug_pulls=5,
            best_multiplier=0,
        )
        decision = self.engine.evaluate(token_info=info, dev_result=dev_result)
        self.assertTrue(decision.hard_stop)
        self.assertIn("scammer", decision.hard_stop_reason.lower())

    def test_simulation_honeypot_hard_stop(self):
        """Simulation-confirmed honeypot should hard stop."""
        info = _make_token_info(
            _simulation_ok=True,
            sim_is_honeypot=True,
            sim_honeypot_reason="Cannot sell",
        )
        decision = self.engine.evaluate(token_info=info)
        self.assertTrue(decision.hard_stop)

    def test_selfdestruct_hard_stop(self):
        """SELFDESTRUCT opcode should trigger hard stop."""
        info = _make_token_info(
            _bytecode_ok=True,
            bytecode_has_selfdestruct=True,
        )
        decision = self.engine.evaluate(token_info=info)
        self.assertTrue(decision.hard_stop)

    def test_safe_token_gets_positive_score(self):
        """A safe token with good metrics should score positively."""
        info = _make_token_info(
            is_open_source=True,
            is_proxy=False,
            tokensniffer_score=90,
            buy_tax=2,
            sell_tax=3,
            lp_locked=True,
            lp_lock_percent=99,
            lp_lock_hours_remaining=720,
        )
        decision = self.engine.evaluate(token_info=info, liquidity_usd=50000)
        self.assertGreater(decision.final_score, 30)
        self.assertFalse(decision.hard_stop)

    def test_pump_result_influence(self):
        """Pump result should affect the score."""
        info = _make_token_info()
        # Test with high pump score
        high_pump = SimpleNamespace(total_score=90, grade="HIGH")
        dec_high = self.engine.evaluate(token_info=info, pump_result=high_pump)

        # Test with low pump score
        low_pump = SimpleNamespace(total_score=15, grade="AVOID")
        dec_low = self.engine.evaluate(token_info=info, pump_result=low_pump)

        self.assertGreater(dec_high.final_score, dec_low.final_score)

    def test_smart_money_influence(self):
        """Multiple whale signals should boost score."""
        info = _make_token_info()
        signal = SimpleNamespace(confidence=90)
        decision = self.engine.evaluate(
            token_info=info,
            smart_signals=[signal, signal, signal],
        )
        self.assertGreater(decision.smart_money_score, 50)

    def test_action_thresholds(self):
        """Test that action labels correspond to score ranges."""
        engine = self.engine
        # We test the scoring function by calling with no extra modules
        info = _make_token_info()
        decision = engine.evaluate(token_info=info)
        # Verify the action-score relationship
        if decision.final_score >= 80:
            self.assertEqual(decision.action, "STRONG_BUY")
        elif decision.final_score >= 65:
            self.assertEqual(decision.action, "BUY")
        elif decision.final_score >= 50:
            self.assertEqual(decision.action, "WATCH")
        elif decision.final_score >= 35:
            self.assertEqual(decision.action, "WEAK")
        else:
            self.assertEqual(decision.action, "IGNORE")

    def test_no_goplus_data_reduces_score(self):
        """Without GoPlus data, security score should be lower."""
        info_with = _make_token_info(_goplus_ok=True, is_open_source=True)
        info_without = _make_token_info(_goplus_ok=False)
        dec_with = self.engine.evaluate(token_info=info_with)
        dec_without = self.engine.evaluate(token_info=info_without)
        # With GoPlus and verified code should score >= without
        self.assertGreaterEqual(dec_with.security_score, dec_without.security_score)


class TestRiskEngineGetStats(unittest.TestCase):
    """Test get_stats method."""

    def test_stats_contains_weights(self):
        engine = RiskEngine()
        stats = engine.get_stats()
        self.assertIn("weights", stats)
        self.assertIn("thresholds", stats)

    def test_stats_thresholds(self):
        stats = RiskEngine().get_stats()
        self.assertEqual(stats["thresholds"]["STRONG_BUY"], 80)
        self.assertEqual(stats["thresholds"]["BUY"], 65)
        self.assertEqual(stats["thresholds"]["WATCH"], 50)


if __name__ == "__main__":
    unittest.main()

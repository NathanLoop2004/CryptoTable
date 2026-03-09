"""
test_sniperService.py — Integration tests for SniperBot core functionality.
Tests the service initialization, configuration, and state management.
"""
import unittest
from unittest.mock import patch, MagicMock
from types import SimpleNamespace

from trading.Services.sniperService import (
    SniperBot, TokenRisk, TokenInfo, NewPair, ActiveSnipe,
    FACTORY_ADDRESSES, ROUTER_ADDRESSES, WETH_ADDRESSES,
    RPC_ENDPOINTS, RPC_FALLBACKS, PAIR_CREATED_TOPIC, SYNC_TOPIC,
)


class TestConstants(unittest.TestCase):
    """Test that all required constants are properly defined."""

    def test_factory_addresses(self):
        self.assertIn(56, FACTORY_ADDRESSES)
        self.assertIn(1, FACTORY_ADDRESSES)
        self.assertTrue(FACTORY_ADDRESSES[56].startswith("0x"))
        self.assertTrue(FACTORY_ADDRESSES[1].startswith("0x"))

    def test_router_addresses(self):
        self.assertIn(56, ROUTER_ADDRESSES)
        self.assertIn(1, ROUTER_ADDRESSES)

    def test_weth_addresses(self):
        self.assertIn(56, WETH_ADDRESSES)
        self.assertIn(1, WETH_ADDRESSES)

    def test_rpc_endpoints(self):
        for chain_id in (56, 1):
            self.assertIn(chain_id, RPC_ENDPOINTS)
            info = RPC_ENDPOINTS[chain_id]
            self.assertIn("http", info)
            self.assertIn("ws", info)
            self.assertIn("name", info)

    def test_rpc_fallbacks(self):
        for chain_id in (56, 1):
            self.assertIn(chain_id, RPC_FALLBACKS)
            self.assertGreater(len(RPC_FALLBACKS[chain_id]), 0)

    def test_event_topics(self):
        self.assertTrue(PAIR_CREATED_TOPIC.startswith("0x"))
        self.assertTrue(SYNC_TOPIC.startswith("0x"))
        self.assertEqual(len(PAIR_CREATED_TOPIC), 66)
        self.assertEqual(len(SYNC_TOPIC), 66)


class TestTokenRisk(unittest.TestCase):
    """Test TokenRisk enum."""

    def test_risk_values(self):
        self.assertEqual(TokenRisk.SAFE.value, "safe")
        self.assertEqual(TokenRisk.WARNING.value, "warning")
        self.assertEqual(TokenRisk.DANGER.value, "danger")
        self.assertEqual(TokenRisk.UNKNOWN.value, "unknown")


class TestTokenInfo(unittest.TestCase):
    """Test TokenInfo dataclass fields."""

    def test_default_values(self):
        info = TokenInfo(address="0x123")
        self.assertEqual(info.symbol, "?")
        self.assertEqual(info.name, "?")
        self.assertFalse(info.is_honeypot)
        self.assertEqual(info.buy_tax, 0)
        self.assertEqual(info.sell_tax, 0)
        self.assertEqual(info.risk, "unknown")

    def test_to_dict(self):
        info = TokenInfo(address="0x123", symbol="TEST")
        d = info.to_dict()
        self.assertEqual(d["symbol"], "TEST")
        self.assertIsInstance(d, dict)

    def test_v2_fields_exist(self):
        """V2 professional fields should be present."""
        info = TokenInfo(address="0x123")
        self.assertEqual(info.pump_score, 0)
        self.assertEqual(info.pump_grade, "")
        self.assertFalse(info.sim_is_honeypot)
        self.assertEqual(info.bytecode_flags, [])
        self.assertEqual(info.smart_money_buyers, 0)

    def test_v3_fields_exist(self):
        """V3 professional fields should be present."""
        info = TokenInfo(address="0x123")
        # Dev tracker
        self.assertFalse(info.dev_is_tracked)
        self.assertEqual(info.dev_score, 0)
        self.assertFalse(info.dev_is_serial_scammer)
        # Risk engine
        self.assertEqual(info.risk_engine_score, 0)
        self.assertEqual(info.risk_engine_action, "")
        self.assertFalse(info.risk_engine_hard_stop)
        # Trade executor
        self.assertFalse(info.backend_buy_available)


class TestNewPair(unittest.TestCase):
    """Test NewPair dataclass."""

    def test_default_values(self):
        pair = NewPair(
            pair_address="0xPAIR",
            token0="0xT0",
            token1="0xT1",
            new_token="0xT0",
            base_token="0xT1",
            chain_id=56,
        )
        self.assertEqual(pair.chain_id, 56)
        self.assertEqual(pair.liquidity_usd, 0)
        self.assertIsNone(pair.token_info)


class TestActiveSnipe(unittest.TestCase):
    """Test ActiveSnipe dataclass."""

    def test_default_values(self):
        snipe = ActiveSnipe(
            token_address="0xTOKEN",
            symbol="TEST",
            chain_id=56,
        )
        self.assertEqual(snipe.status, "active")
        self.assertEqual(snipe.pnl_percent, 0)
        self.assertEqual(snipe.max_hold_hours, 0)

    def test_to_dict(self):
        snipe = ActiveSnipe(
            token_address="0xTOKEN",
            symbol="TEST",
            chain_id=56,
            buy_price_usd=100.0,
        )
        d = snipe.to_dict()
        self.assertEqual(d["symbol"], "TEST")
        self.assertEqual(d["buy_price_usd"], 100.0)


class TestSniperBotInit(unittest.TestCase):
    """Test SniperBot initialization."""

    @patch("trading.Services.sniperService.Web3")
    def test_default_chain_bsc(self, mock_web3):
        mock_web3.HTTPProvider.return_value = MagicMock()
        mock_web3.to_checksum_address.side_effect = lambda x: x
        mock_web3.keccak.return_value = b"\x00" * 32

        bot = SniperBot(chain_id=56)
        self.assertEqual(bot.chain_id, 56)
        self.assertFalse(bot.running)

    @patch("trading.Services.sniperService.Web3")
    def test_settings_defaults(self, mock_web3):
        mock_web3.HTTPProvider.return_value = MagicMock()
        mock_web3.to_checksum_address.side_effect = lambda x: x
        mock_web3.keccak.return_value = b"\x00" * 32

        bot = SniperBot()
        self.assertEqual(bot.settings["min_liquidity_usd"], 5000)
        self.assertEqual(bot.settings["take_profit"], 40)
        self.assertEqual(bot.settings["stop_loss"], 20)
        self.assertFalse(bot.settings["auto_buy"])
        self.assertTrue(bot.settings["only_safe"])
        self.assertTrue(bot.settings["enable_pump_score"])
        self.assertTrue(bot.settings["enable_risk_engine"])

    @patch("trading.Services.sniperService.Web3")
    def test_update_settings(self, mock_web3):
        mock_web3.HTTPProvider.return_value = MagicMock()
        mock_web3.to_checksum_address.side_effect = lambda x: x
        mock_web3.keccak.return_value = b"\x00" * 32

        bot = SniperBot()
        bot.update_settings({
            "min_liquidity_usd": 10000,
            "take_profit": 50,
            "auto_buy": True,
        })
        self.assertEqual(bot.settings["min_liquidity_usd"], 10000)
        self.assertEqual(bot.settings["take_profit"], 50)
        self.assertTrue(bot.settings["auto_buy"])

    @patch("trading.Services.sniperService.Web3")
    def test_update_settings_ignores_unknown(self, mock_web3):
        mock_web3.HTTPProvider.return_value = MagicMock()
        mock_web3.to_checksum_address.side_effect = lambda x: x
        mock_web3.keccak.return_value = b"\x00" * 32

        bot = SniperBot()
        bot.update_settings({"nonexistent": "value"})
        self.assertNotIn("nonexistent", bot.settings)

    @patch("trading.Services.sniperService.Web3")
    def test_get_state(self, mock_web3):
        mock_web3.HTTPProvider.return_value = MagicMock()
        mock_web3.to_checksum_address.side_effect = lambda x: x
        mock_web3.keccak.return_value = b"\x00" * 32

        bot = SniperBot()
        state = bot.get_state()
        self.assertIn("running", state)
        self.assertIn("chain_id", state)
        self.assertIn("settings", state)
        self.assertIn("detected_pairs", state)
        self.assertIn("active_snipes", state)
        self.assertIn("recent_events", state)
        self.assertFalse(state["running"])

    @patch("trading.Services.sniperService.Web3")
    def test_stop(self, mock_web3):
        mock_web3.HTTPProvider.return_value = MagicMock()
        mock_web3.to_checksum_address.side_effect = lambda x: x
        mock_web3.keccak.return_value = b"\x00" * 32

        bot = SniperBot()
        bot.running = True
        bot.stop()
        self.assertFalse(bot.running)

    @patch("trading.Services.sniperService.Web3")
    def test_add_manual_snipe(self, mock_web3):
        mock_web3.HTTPProvider.return_value = MagicMock()
        mock_web3.to_checksum_address.side_effect = lambda x: x
        mock_web3.keccak.return_value = b"\x00" * 32

        bot = SniperBot()
        result = bot.add_manual_snipe(
            token_address="0xTOKEN",
            symbol="TEST",
            buy_price=100.0,
            amount_native=0.5,
            tx_hash="0xTX",
        )
        self.assertEqual(len(bot.active_snipes), 1)
        self.assertEqual(result["symbol"], "TEST")
        self.assertEqual(result["status"], "active")

    @patch("trading.Services.sniperService.Web3")
    def test_mark_snipe_sold(self, mock_web3):
        mock_web3.HTTPProvider.return_value = MagicMock()
        mock_web3.to_checksum_address.side_effect = lambda x: x
        mock_web3.keccak.return_value = b"\x00" * 32

        bot = SniperBot()
        bot.add_manual_snipe("0xTOKEN", "TEST", 100.0, 0.5, "0xTX")
        result = bot.mark_snipe_sold("0xTOKEN", "0xSELLTX")
        self.assertIsNotNone(result)
        self.assertEqual(result["status"], "sold")

    @patch("trading.Services.sniperService.Web3")
    def test_mark_snipe_sold_nonexistent(self, mock_web3):
        mock_web3.HTTPProvider.return_value = MagicMock()
        mock_web3.to_checksum_address.side_effect = lambda x: x
        mock_web3.keccak.return_value = b"\x00" * 32

        bot = SniperBot()
        result = bot.mark_snipe_sold("0xNONEXISTENT")
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()

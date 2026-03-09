"""
test_v6_modules.py — Unit tests for Professional v6 modules.

Tests:
  - BacktestEngine: config, virtual portfolio, trade simulation, results
  - CopyTrader: wallet tracking, signal processing, portfolio guards
  - MEVProtector: threat analysis, strategy selection, protection
  - MultiDexRouter: chain/DEX registry, route finding, scoring
  - StrategyOptimizer: trade recording, regime detection, optimization
"""
import asyncio
import time
import unittest
from unittest.mock import MagicMock, AsyncMock, patch
from dataclasses import asdict

# ═══════════════════════════════════════════════
#  Imports
# ═══════════════════════════════════════════════

from trading.Services.backtestEngine import (
    BacktestConfig, BacktestTrade, BacktestResult,
    VirtualPortfolio, BacktestEngine, HistoricalDataSource,
)
from trading.Services.copyTrader import (
    CopyTrader, CopyTraderConfig, TrackedWallet, CopyTradeSignal,
)
from trading.Services.mevProtection import (
    MEVProtector, MEVConfig, MEVAnalysis, ProtectedTx,
)
from trading.Services.multiDexRouter import (
    MultiDexRouter, DexConfig, ChainConfig, RouteResult,
)
from trading.Services.strategyOptimizer import (
    StrategyOptimizer, StrategyParams, TradeOutcome,
    OptimizationResult, MarketRegime,
)


def _run(coro):
    """Run async coroutine in tests."""
    return asyncio.run(coro)


# ═══════════════════════════════════════════════
#  BacktestEngine Tests
# ═══════════════════════════════════════════════

class TestBacktestConfig(unittest.TestCase):
    """BacktestConfig creation and defaults."""

    def test_default_config(self):
        cfg = BacktestConfig()
        self.assertEqual(cfg.chain_id, 56)
        self.assertEqual(cfg.buy_amount_native, 0.05)
        self.assertEqual(cfg.take_profit_pct, 40.0)
        self.assertEqual(cfg.stop_loss_pct, 15.0)
        self.assertEqual(cfg.max_hold_hours, 24.0)
        self.assertTrue(cfg.only_safe)

    def test_custom_config(self):
        cfg = BacktestConfig(
            chain_id=1,
            buy_amount_native=0.1,
            take_profit_pct=50,
            stop_loss_pct=25,
            max_hold_hours=48,
            name="aggressive",
        )
        self.assertEqual(cfg.chain_id, 1)
        self.assertEqual(cfg.buy_amount_native, 0.1)
        self.assertEqual(cfg.name, "aggressive")

    def test_to_dict(self):
        cfg = BacktestConfig()
        d = cfg.to_dict()
        self.assertIsInstance(d, dict)
        self.assertIn("buy_amount_native", d)
        self.assertIn("chain_id", d)
        self.assertIn("take_profit_pct", d)

    def test_slippage_and_gas(self):
        cfg = BacktestConfig(slippage_pct=15.0, gas_price_gwei=10.0)
        self.assertEqual(cfg.slippage_pct, 15.0)
        self.assertEqual(cfg.gas_price_gwei, 10.0)

    def test_score_minimums(self):
        cfg = BacktestConfig(min_pump_score=50, min_risk_score=40, min_ml_score=30)
        self.assertEqual(cfg.min_pump_score, 50)
        self.assertEqual(cfg.min_risk_score, 40)
        self.assertEqual(cfg.min_ml_score, 30)


class TestBacktestTrade(unittest.TestCase):
    """BacktestTrade dataclass."""

    def test_create_trade(self):
        t = BacktestTrade(
            token_address="0x" + "A" * 40,
            symbol="TEST",
            entry_price_usd=1.0,
            exit_price_usd=1.5,
            entry_timestamp=time.time() - 3600,
            exit_timestamp=time.time(),
            pnl_percent=50.0,
            exit_reason="take_profit",
        )
        self.assertEqual(t.symbol, "TEST")
        self.assertEqual(t.pnl_percent, 50.0)
        self.assertEqual(t.exit_reason, "take_profit")

    def test_default_values(self):
        t = BacktestTrade(token_address="0x" + "B" * 40)
        self.assertEqual(t.symbol, "")
        self.assertEqual(t.pnl_percent, 0.0)
        self.assertEqual(t.exit_reason, "")
        self.assertEqual(t.entry_price_usd, 0.0)
        self.assertEqual(t.gas_cost_usd, 0.0)
        self.assertEqual(t.slippage_cost_usd, 0.0)

    def test_to_dict(self):
        t = BacktestTrade(token_address="0x" + "A" * 40, symbol="T1")
        d = t.to_dict()
        self.assertIsInstance(d, dict)
        self.assertEqual(d["symbol"], "T1")
        self.assertIn("token_address", d)

    def test_losing_trade(self):
        t = BacktestTrade(
            token_address="0x" + "B" * 40,
            symbol="SCAM",
            entry_price_usd=1.0,
            exit_price_usd=0.3,
            pnl_percent=-70.0,
            exit_reason="stop_loss",
        )
        self.assertLess(t.pnl_percent, 0)
        self.assertEqual(t.exit_reason, "stop_loss")

    def test_all_fields_present(self):
        t = BacktestTrade(token_address="0x" + "C" * 40)
        d = t.to_dict()
        expected_fields = [
            "token_address", "symbol", "pair_address",
            "entry_price_usd", "exit_price_usd",
            "pnl_percent", "pnl_usd", "gas_cost_usd",
            "slippage_cost_usd", "hold_seconds",
        ]
        for f in expected_fields:
            self.assertIn(f, d)


class TestBacktestResult(unittest.TestCase):
    """BacktestResult statistics."""

    def test_empty_result(self):
        r = BacktestResult()
        self.assertEqual(r.total_trades, 0)
        self.assertEqual(r.win_rate, 0.0)
        self.assertEqual(r.total_pnl_usd, 0.0)

    def test_result_fields(self):
        r = BacktestResult(
            total_trades=10,
            winning_trades=7,
            losing_trades=3,
            win_rate=70.0,
            total_pnl_usd=150.0,
        )
        self.assertEqual(r.total_trades, 10)
        self.assertEqual(r.win_rate, 70.0)
        self.assertEqual(r.winning_trades, 7)

    def test_to_dict(self):
        r = BacktestResult(total_trades=5)
        d = r.to_dict()
        self.assertIsInstance(d, dict)
        self.assertEqual(d["total_trades"], 5)

    def test_advanced_stats(self):
        r = BacktestResult(
            sharpe_ratio=1.5,
            max_drawdown_pct=12.0,
            profit_factor=2.3,
            avg_hold_seconds=3600.0,
        )
        self.assertEqual(r.sharpe_ratio, 1.5)
        self.assertEqual(r.profit_factor, 2.3)


class TestVirtualPortfolio(unittest.TestCase):
    """VirtualPortfolio tracking."""

    def test_initial_state(self):
        cfg = BacktestConfig(buy_amount_native=0.05)
        vp = VirtualPortfolio(config=cfg)
        self.assertEqual(vp.open_positions, 0)
        self.assertTrue(vp.can_open())

    def test_open_position(self):
        cfg = BacktestConfig(buy_amount_native=0.05, max_concurrent=3)
        vp = VirtualPortfolio(config=cfg)
        t = BacktestTrade(
            token_address="0x" + "A" * 40,
            symbol="TEST",
            entry_price_usd=1.0,
            entry_timestamp=time.time(),
        )
        result = vp.open_position(t)
        self.assertTrue(result)
        self.assertEqual(vp.open_positions, 1)

    def test_close_position(self):
        cfg = BacktestConfig(buy_amount_native=0.05)
        vp = VirtualPortfolio(config=cfg)
        token = "0x" + "A" * 40
        t = BacktestTrade(
            token_address=token,
            symbol="TEST",
            entry_price_usd=1.0,
            entry_timestamp=time.time(),
        )
        vp.open_position(t)
        closed = vp.close_position(token, 1.5, 100, time.time(), "take_profit")
        self.assertIsNotNone(closed)
        self.assertEqual(vp.open_positions, 0)

    def test_max_concurrent(self):
        cfg = BacktestConfig(max_concurrent=2)
        vp = VirtualPortfolio(config=cfg)
        for i in range(3):
            t = BacktestTrade(
                token_address=f"0x{'A' * 39}{i}",
                entry_price_usd=1.0,
            )
            vp.open_position(t)
        self.assertLessEqual(vp.open_positions, 2)

    def test_initial_balance(self):
        cfg = BacktestConfig(buy_amount_native=0.1, native_price_usd=600.0)
        vp = VirtualPortfolio(config=cfg)
        expected = 0.1 * 600.0 * 20
        self.assertEqual(vp.initial_balance, expected)

    def test_close_nonexistent_position(self):
        cfg = BacktestConfig()
        vp = VirtualPortfolio(config=cfg)
        result = vp.close_position("0x" + "Z" * 40, 1.0, 100, time.time(), "manual")
        self.assertIsNone(result)


class TestBacktestEngine(unittest.TestCase):
    """BacktestEngine initialization."""

    def test_create_engine(self):
        w3 = MagicMock()
        engine = BacktestEngine(w3=w3, chain_id=56)
        self.assertIsNotNone(engine)
        self.assertEqual(engine.chain_id, 56)

    def test_create_engine_eth(self):
        w3 = MagicMock()
        engine = BacktestEngine(w3=w3, chain_id=1)
        self.assertEqual(engine.chain_id, 1)

    def test_cancel_flag(self):
        w3 = MagicMock()
        engine = BacktestEngine(w3=w3, chain_id=56)
        self.assertFalse(engine._cancel_flag)
        engine.cancel()
        self.assertTrue(engine._cancel_flag)

    def test_progress_callback(self):
        w3 = MagicMock()
        engine = BacktestEngine(w3=w3, chain_id=56)
        cb = MagicMock()
        engine.set_progress_callback(cb)
        self.assertEqual(engine._progress_callback, cb)

    def test_data_source_created(self):
        w3 = MagicMock()
        engine = BacktestEngine(w3=w3, chain_id=56)
        self.assertIsInstance(engine.data_source, HistoricalDataSource)


class TestHistoricalDataSource(unittest.TestCase):
    """HistoricalDataSource."""

    def test_create_source(self):
        w3 = MagicMock()
        src = HistoricalDataSource(w3=w3, chain_id=56)
        self.assertIsNotNone(src)
        self.assertEqual(src.chain_id, 56)

    def test_create_source_eth(self):
        w3 = MagicMock()
        src = HistoricalDataSource(w3=w3, chain_id=1)
        self.assertEqual(src.chain_id, 1)


# ═══════════════════════════════════════════════
#  CopyTrader Tests
# ═══════════════════════════════════════════════

class TestTrackedWallet(unittest.TestCase):
    """TrackedWallet dataclass."""

    def test_create_wallet(self):
        w = TrackedWallet(
            address="0x" + "D" * 40,
            label="TopTrader1",
            smart_score=85,
        )
        self.assertEqual(w.label, "TopTrader1")
        self.assertEqual(w.smart_score, 85)
        self.assertTrue(w.enabled)
        self.assertTrue(w.follow_buys)

    def test_default_values(self):
        w = TrackedWallet(address="0x" + "E" * 40)
        self.assertEqual(w.label, "")
        self.assertEqual(w.total_copies, 0)
        self.assertEqual(w.source, "auto")
        self.assertTrue(w.follow_sells)
        self.assertEqual(w.max_follow_amount_native, 0.1)

    def test_to_dict(self):
        w = TrackedWallet(address="0x" + "F" * 40, label="W1")
        d = w.to_dict()
        self.assertIsInstance(d, dict)
        self.assertEqual(d["label"], "W1")
        self.assertIn("address", d)


class TestCopyTradeSignal(unittest.TestCase):
    """CopyTradeSignal dataclass."""

    def test_create_signal(self):
        sig = CopyTradeSignal(
            wallet_address="0x" + "F" * 40,
            token_address="0x" + "A" * 40,
            direction="buy",
            whale_amount_native=0.5,
            confidence=85,
        )
        self.assertEqual(sig.direction, "buy")
        self.assertEqual(sig.confidence, 85)
        self.assertEqual(sig.status, "pending")

    def test_to_dict(self):
        sig = CopyTradeSignal(wallet_address="0x" + "F" * 40)
        d = sig.to_dict()
        self.assertIsInstance(d, dict)
        self.assertIn("wallet_address", d)

    def test_sell_signal(self):
        sig = CopyTradeSignal(
            wallet_address="0x" + "A" * 40,
            direction="sell",
            confidence=60,
        )
        self.assertEqual(sig.direction, "sell")


class TestCopyTraderConfig(unittest.TestCase):
    """CopyTraderConfig."""

    def test_default_config(self):
        cfg = CopyTraderConfig()
        self.assertFalse(cfg.enabled)
        self.assertEqual(cfg.max_concurrent_follows, 5)
        self.assertEqual(cfg.max_daily_copy_trades, 20)
        self.assertTrue(cfg.copy_buy)
        self.assertTrue(cfg.copy_sell)
        self.assertEqual(cfg.default_follow_amount_native, 0.05)

    def test_custom_config(self):
        cfg = CopyTraderConfig(
            enabled=True,
            max_concurrent_follows=3,
            default_follow_amount_native=0.1,
        )
        self.assertTrue(cfg.enabled)
        self.assertEqual(cfg.max_concurrent_follows, 3)

    def test_to_dict(self):
        cfg = CopyTraderConfig()
        d = cfg.to_dict()
        self.assertIsInstance(d, dict)
        self.assertIn("enabled", d)

    def test_blacklisted_tokens(self):
        cfg = CopyTraderConfig(blacklisted_tokens=["0xSCAM"])
        self.assertIn("0xSCAM", cfg.blacklisted_tokens)


class TestCopyTrader(unittest.TestCase):
    """CopyTrader core functionality."""

    def test_create_copy_trader(self):
        w3 = MagicMock()
        ct = CopyTrader(w3=w3, chain_id=56)
        self.assertIsNotNone(ct)
        self.assertFalse(ct.running)

    def test_add_wallet(self):
        w3 = MagicMock()
        ct = CopyTrader(w3=w3, chain_id=56)
        ct.add_wallet("0x" + "D" * 40, label="Whale1")
        wallets = ct.get_tracked_wallets()
        self.assertEqual(len(wallets), 1)

    def test_add_wallet_with_params(self):
        w3 = MagicMock()
        ct = CopyTrader(w3=w3, chain_id=56)
        ct.add_wallet(
            "0x" + "D" * 40,
            label="Whale2",
            follow_buys=True,
            follow_sells=False,
            max_amount=0.5,
            source="manual",
        )
        wallets = ct.get_tracked_wallets()
        self.assertEqual(len(wallets), 1)

    def test_remove_wallet(self):
        w3 = MagicMock()
        ct = CopyTrader(w3=w3, chain_id=56)
        addr = "0x" + "D" * 40
        ct.add_wallet(addr, label="Whale1")
        ct.remove_wallet(addr)
        wallets = ct.get_tracked_wallets()
        self.assertEqual(len(wallets), 0)

    def test_remove_nonexistent_wallet(self):
        w3 = MagicMock()
        ct = CopyTrader(w3=w3, chain_id=56)
        ct.remove_wallet("0x" + "Z" * 40)
        self.assertEqual(len(ct.get_tracked_wallets()), 0)

    def test_stop_is_async(self):
        w3 = MagicMock()
        ct = CopyTrader(w3=w3, chain_id=56)
        _run(ct.stop())
        self.assertFalse(ct.running)

    def test_get_stats(self):
        w3 = MagicMock()
        ct = CopyTrader(w3=w3, chain_id=56)
        stats = ct.get_stats()
        self.assertIsInstance(stats, dict)
        self.assertIn("wallets_tracked", stats)
        self.assertIn("signals_received", stats)

    def test_get_signals(self):
        w3 = MagicMock()
        ct = CopyTrader(w3=w3, chain_id=56)
        signals = ct.get_signals()
        self.assertIsInstance(signals, list)

    def test_update_config(self):
        w3 = MagicMock()
        ct = CopyTrader(w3=w3, chain_id=56)
        ct.update_config(max_concurrent_follows=10)
        self.assertEqual(ct.config.max_concurrent_follows, 10)

    def test_set_trade_executor(self):
        w3 = MagicMock()
        ct = CopyTrader(w3=w3, chain_id=56)
        executor = MagicMock()
        ct.set_trade_executor(executor)
        # Just verify no error raised

    def test_routers_constant(self):
        self.assertIn(56, CopyTrader.ROUTERS)
        self.assertIn(1, CopyTrader.ROUTERS)

    def test_to_dict(self):
        w3 = MagicMock()
        ct = CopyTrader(w3=w3, chain_id=56)
        d = ct.to_dict()
        self.assertIsInstance(d, dict)


# ═══════════════════════════════════════════════
#  MEVProtector Tests
# ═══════════════════════════════════════════════

class TestMEVConfig(unittest.TestCase):
    """MEVConfig dataclass."""

    def test_default_config(self):
        cfg = MEVConfig()
        self.assertFalse(cfg.enabled)
        self.assertTrue(cfg.detect_sandwich)
        self.assertGreater(cfg.gas_boost_percent, 0)
        self.assertEqual(cfg.split_chunks, 3)

    def test_custom_config(self):
        cfg = MEVConfig(
            enabled=True,
            gas_boost_percent=20.0,
            gas_boost_max_gwei=100,
        )
        self.assertTrue(cfg.enabled)
        self.assertEqual(cfg.gas_boost_percent, 20.0)

    def test_to_dict_masks_key(self):
        cfg = MEVConfig(flashbots_signer_key="secret123")
        d = cfg.to_dict()
        self.assertIsInstance(d, dict)
        self.assertEqual(d.get("flashbots_signer_key"), "***")

    def test_strategy_priority(self):
        cfg = MEVConfig()
        self.assertIsInstance(cfg.strategy_priority, list)
        self.assertIn("flashbots", cfg.strategy_priority)
        self.assertIn("gas_boost", cfg.strategy_priority)


class TestMEVAnalysis(unittest.TestCase):
    """MEVAnalysis dataclass."""

    def test_create_analysis(self):
        a = MEVAnalysis(
            threat_level="medium",
            frontrun_risk=0.4,
            sandwich_risk=0.6,
            pending_bots_count=3,
            recommended_strategy="gas_boost",
        )
        self.assertEqual(a.threat_level, "medium")
        self.assertGreater(a.sandwich_risk, 0)

    def test_default_values(self):
        a = MEVAnalysis()
        self.assertEqual(a.threat_level, "unknown")
        self.assertEqual(a.frontrun_risk, 0.0)
        self.assertEqual(a.sandwich_risk, 0.0)
        self.assertEqual(a.token_address, "")

    def test_to_dict(self):
        a = MEVAnalysis(threat_level="high")
        d = a.to_dict()
        self.assertEqual(d["threat_level"], "high")

    def test_sandwich_detection_fields(self):
        a = MEVAnalysis(
            was_sandwiched=True,
            sandwich_profit_usd=50.0,
            frontrunner_address="0x" + "B" * 40,
        )
        self.assertTrue(a.was_sandwiched)
        self.assertEqual(a.sandwich_profit_usd, 50.0)


class TestProtectedTx(unittest.TestCase):
    """ProtectedTx dataclass."""

    def test_create(self):
        ptx = ProtectedTx(strategy_used="gas_boost", success=True)
        self.assertTrue(ptx.success)
        self.assertEqual(ptx.strategy_used, "gas_boost")

    def test_default(self):
        ptx = ProtectedTx()
        self.assertFalse(ptx.success)
        self.assertEqual(ptx.strategy_used, "none")
        self.assertEqual(ptx.error, "")

    def test_to_dict(self):
        ptx = ProtectedTx(strategy_used="flashbots")
        d = ptx.to_dict()
        self.assertIsInstance(d, dict)


class TestMEVProtector(unittest.TestCase):
    """MEVProtector core functionality."""

    def test_create_protector(self):
        w3 = MagicMock()
        mp = MEVProtector(w3=w3, chain_id=56)
        self.assertIsNotNone(mp)
        self.assertEqual(mp.chain_id, 56)

    def test_known_bots(self):
        self.assertTrue(len(MEVProtector.KNOWN_MEV_BOTS) >= 4)

    def test_analyze_threat_returns_analysis(self):
        w3 = MagicMock()
        w3.eth.get_block.return_value = {"transactions": []}
        mp = MEVProtector(w3=w3, chain_id=56)
        result = _run(mp.analyze_threat("0x" + "A" * 40, 0.05))
        self.assertIsInstance(result, MEVAnalysis)
        self.assertIn(
            result.threat_level,
            ["low", "medium", "high", "critical", "unknown"],
        )
        self.assertGreaterEqual(result.frontrun_risk, 0)
        self.assertLessEqual(result.frontrun_risk, 1)

    def test_configure(self):
        w3 = MagicMock()
        mp = MEVProtector(w3=w3, chain_id=56)
        mp.configure(gas_boost_percent=25.0)
        self.assertEqual(mp.config.gas_boost_percent, 25.0)

    def test_get_stats(self):
        w3 = MagicMock()
        mp = MEVProtector(w3=w3, chain_id=56)
        stats = mp.get_stats()
        self.assertIsInstance(stats, dict)
        self.assertIn("txs_protected", stats)
        self.assertIn("sandwiches_detected", stats)

    def test_split_transaction(self):
        w3 = MagicMock()
        mp = MEVProtector(w3=w3, chain_id=56)
        raw_tx = {"value": 10**18, "to": "0x" + "1" * 40}
        chunks = mp.split_transaction(raw_tx, chunks=3)
        self.assertIsInstance(chunks, list)
        self.assertGreater(len(chunks), 0)

    def test_protect_transaction(self):
        w3 = MagicMock()
        w3.eth.get_block.return_value = {"transactions": []}
        mp = MEVProtector(w3=w3, chain_id=56)
        raw_tx = {"to": "0x" + "1" * 40, "value": 10**17}
        analysis = MEVAnalysis(threat_level="medium", recommended_strategy="gas_boost")
        result = _run(mp.protect_transaction(raw_tx, analysis))
        self.assertIsInstance(result, ProtectedTx)


# ═══════════════════════════════════════════════
#  MultiDexRouter Tests
# ═══════════════════════════════════════════════

class TestDexConfig(unittest.TestCase):
    """DexConfig dataclass."""

    def test_create_dex_config(self):
        dc = DexConfig(
            name="TestDEX",
            chain_id=56,
            factory_address="0x" + "1" * 40,
            router_address="0x" + "2" * 40,
            version="v2",
        )
        self.assertEqual(dc.name, "TestDEX")
        self.assertEqual(dc.version, "v2")
        self.assertTrue(dc.enabled)
        self.assertEqual(dc.chain_id, 56)

    def test_to_dict(self):
        dc = DexConfig(name="X", chain_id=56)
        d = dc.to_dict()
        self.assertIsInstance(d, dict)
        self.assertEqual(d["chain_id"], 56)


class TestChainConfig(unittest.TestCase):
    """ChainConfig dataclass."""

    def test_create_chain_config(self):
        cc = ChainConfig(
            chain_id=56,
            name="BSC",
            symbol="BNB",
        )
        self.assertEqual(cc.chain_id, 56)
        self.assertEqual(cc.name, "BSC")
        self.assertEqual(cc.symbol, "BNB")
        self.assertTrue(cc.enabled)

    def test_chain_config_dexes(self):
        cc = ChainConfig(chain_id=1, name="Ethereum", symbol="ETH")
        self.assertIsInstance(cc.dexes, list)
        self.assertEqual(len(cc.dexes), 0)


class TestRouteResult(unittest.TestCase):
    """RouteResult dataclass."""

    def test_create_route(self):
        r = RouteResult(
            dex_name="PancakeSwap V2",
            amount_out=1000000000000000000,
            path=["0xWETH", "0xTOKEN"],
            gas_estimate=200000,
        )
        self.assertEqual(r.dex_name, "PancakeSwap V2")
        self.assertGreater(r.amount_out, 0)

    def test_to_dict(self):
        r = RouteResult(dex_name="Test", amount_out=100)
        d = r.to_dict()
        self.assertIsInstance(d, dict)
        self.assertIn("dex_name", d)
        self.assertIn("score", d)

    def test_default_values(self):
        r = RouteResult()
        self.assertEqual(r.dex_name, "")
        self.assertEqual(r.amount_out, 0)
        self.assertEqual(r.score, 0.0)


class TestMultiDexRouter(unittest.TestCase):
    """MultiDexRouter core functionality."""

    def test_create_router_no_args(self):
        router = MultiDexRouter()
        self.assertIsNotNone(router)

    def test_supported_chains(self):
        router = MultiDexRouter()
        chain = router.get_chain(56)
        self.assertIsNotNone(chain)
        self.assertIn("BNB", chain.name)

        chain_eth = router.get_chain(1)
        self.assertIsNotNone(chain_eth)
        self.assertEqual(chain_eth.name, "Ethereum")

    def test_get_dexes_bsc(self):
        router = MultiDexRouter()
        dexes = router.get_dexes(56)
        self.assertGreater(len(dexes), 0)
        dex_names = [d.name for d in dexes]
        self.assertTrue(any("pancake" in n.lower() for n in dex_names))

    def test_get_dexes_eth(self):
        router = MultiDexRouter()
        dexes = router.get_dexes(1)
        self.assertGreater(len(dexes), 0)
        dex_names = [d.name for d in dexes]
        self.assertTrue(any("uniswap" in n.lower() for n in dex_names))

    def test_get_dexes_arbitrum(self):
        router = MultiDexRouter()
        dexes = router.get_dexes(42161)
        self.assertGreater(len(dexes), 0)

    def test_get_dexes_base(self):
        router = MultiDexRouter()
        dexes = router.get_dexes(8453)
        self.assertGreater(len(dexes), 0)

    def test_get_dexes_polygon(self):
        router = MultiDexRouter()
        dexes = router.get_dexes(137)
        self.assertGreater(len(dexes), 0)

    def test_unknown_chain_empty(self):
        router = MultiDexRouter()
        dexes = router.get_dexes(999999)
        self.assertEqual(len(dexes), 0)

    def test_add_chain(self):
        router = MultiDexRouter()
        w3 = MagicMock()
        router.add_chain(56, w3=w3)
        self.assertIn(56, router.get_active_chains())

    def test_add_custom_dex(self):
        router = MultiDexRouter()
        initial = len(router.get_dexes(56))
        router.add_custom_dex(56, DexConfig(
            name="CustomDEX",
            chain_id=56,
            factory_address="0x" + "9" * 40,
            router_address="0x" + "8" * 40,
            version="v2",
        ))
        self.assertEqual(len(router.get_dexes(56)), initial + 1)

    def test_get_wrapped_native(self):
        router = MultiDexRouter()
        wbnb = router.get_wrapped_native(56)
        self.assertTrue(wbnb.startswith("0x"))

    def test_get_factories(self):
        router = MultiDexRouter()
        factories = router.get_factories(56)
        self.assertIsInstance(factories, list)
        self.assertGreater(len(factories), 0)

    def test_enable_disable_dex(self):
        router = MultiDexRouter()
        dexes = router.get_dexes(56)
        if dexes:
            name = dexes[0].name
            router.enable_dex(56, name, False)
            updated = [d for d in router.get_dexes(56) if d.name == name]
            if updated:
                self.assertFalse(updated[0].enabled)

    def test_get_overview(self):
        router = MultiDexRouter()
        overview = router.get_overview()
        self.assertIsInstance(overview, dict)

    def test_get_all_factory_filters(self):
        router = MultiDexRouter()
        filters = router.get_all_factory_filters(56)
        self.assertIsInstance(filters, list)

    def test_unknown_chain_wrapped_native(self):
        router = MultiDexRouter()
        result = router.get_wrapped_native(999999)
        self.assertEqual(result, "")


# ═══════════════════════════════════════════════
#  StrategyOptimizer Tests
# ═══════════════════════════════════════════════

class TestStrategyParams(unittest.TestCase):
    """StrategyParams dataclass."""

    def test_default_params(self):
        sp = StrategyParams()
        self.assertEqual(sp.take_profit_pct, 40.0)
        self.assertEqual(sp.stop_loss_pct, 15.0)
        self.assertEqual(sp.slippage_pct, 12.0)
        self.assertEqual(sp.name, "default")

    def test_custom_params(self):
        sp = StrategyParams(take_profit_pct=60, stop_loss_pct=30)
        self.assertEqual(sp.take_profit_pct, 60)
        self.assertEqual(sp.stop_loss_pct, 30)

    def test_to_dict(self):
        sp = StrategyParams()
        d = sp.to_dict()
        self.assertIsInstance(d, dict)
        self.assertIn("take_profit_pct", d)
        self.assertIn("buy_amount_native", d)

    def test_to_vector(self):
        sp = StrategyParams()
        v = sp.to_vector()
        self.assertIsInstance(v, list)
        self.assertEqual(len(v), 14)
        for val in v:
            self.assertIsInstance(val, float)

    def test_feature_names(self):
        names = StrategyParams.feature_names()
        self.assertEqual(len(names), 14)
        self.assertIn("take_profit_pct", names)

    def test_vector_matches_feature_names(self):
        sp = StrategyParams()
        v = sp.to_vector()
        names = StrategyParams.feature_names()
        self.assertEqual(len(v), len(names))

    def test_serializable(self):
        sp = StrategyParams(take_profit_pct=50, stop_loss_pct=20)
        d = asdict(sp)
        self.assertIsInstance(d, dict)
        self.assertEqual(d["take_profit_pct"], 50)

    def test_generation_tracking(self):
        sp = StrategyParams(name="gen1", generation=1, parent_name="default")
        self.assertEqual(sp.generation, 1)
        self.assertEqual(sp.parent_name, "default")


class TestTradeOutcome(unittest.TestCase):
    """TradeOutcome dataclass."""

    def test_winning_trade(self):
        outcome = TradeOutcome(
            token_address="0x" + "A" * 40,
            pnl_percent=45.0,
            hold_seconds=1800,
            exit_reason="take_profit",
        )
        self.assertGreater(outcome.pnl_percent, 0)

    def test_default(self):
        outcome = TradeOutcome()
        self.assertEqual(outcome.pnl_percent, 0.0)
        self.assertEqual(outcome.params_name, "default")
        self.assertEqual(outcome.exit_reason, "")

    def test_to_dict(self):
        outcome = TradeOutcome(pnl_percent=10.0)
        d = outcome.to_dict()
        self.assertIsInstance(d, dict)
        self.assertEqual(d["pnl_percent"], 10.0)

    def test_losing_trade(self):
        outcome = TradeOutcome(pnl_percent=-50.0, exit_reason="stop_loss")
        self.assertLess(outcome.pnl_percent, 0)

    def test_with_market_context(self):
        outcome = TradeOutcome(
            market_regime="bull",
            gas_price_gwei=5.0,
            liquidity_usd=50000.0,
        )
        self.assertEqual(outcome.market_regime, "bull")


class TestMarketRegime(unittest.TestCase):
    """MarketRegime dataclass."""

    def test_create_regime(self):
        mr = MarketRegime(regime="bull", confidence=0.8)
        self.assertEqual(mr.regime, "bull")
        self.assertGreater(mr.confidence, 0)

    def test_regime_types(self):
        for rt in ["bull", "bear", "sideways", "volatile"]:
            mr = MarketRegime(regime=rt, confidence=0.5)
            self.assertEqual(mr.regime, rt)

    def test_default(self):
        mr = MarketRegime()
        self.assertEqual(mr.regime, "unknown")
        self.assertEqual(mr.confidence, 0.0)
        self.assertEqual(mr.btc_24h_change, 0.0)
        self.assertEqual(mr.eth_24h_change, 0.0)

    def test_to_dict(self):
        mr = MarketRegime(regime="bear")
        d = mr.to_dict()
        self.assertEqual(d["regime"], "bear")
        self.assertIn("volatility_index", d)


class TestOptimizationResult(unittest.TestCase):
    """OptimizationResult dataclass."""

    def test_create_result(self):
        r = OptimizationResult(
            status="completed",
            proposed_params={"take_profit_pct": 55, "stop_loss_pct": 22},
            improvement_predicted_pct=12.5,
            confidence=0.75,
            market_regime="bull",
        )
        self.assertEqual(r.status, "completed")
        self.assertGreater(r.improvement_predicted_pct, 0)

    def test_default(self):
        r = OptimizationResult()
        self.assertEqual(r.status, "pending")
        self.assertEqual(r.confidence, 0.0)
        self.assertEqual(r.candidates_evaluated, 0)

    def test_to_dict(self):
        r = OptimizationResult(status="completed")
        d = r.to_dict()
        self.assertIsInstance(d, dict)
        self.assertEqual(d["status"], "completed")


class TestStrategyOptimizer(unittest.TestCase):
    """StrategyOptimizer core functionality."""

    def test_create_optimizer_no_args(self):
        so = StrategyOptimizer()
        self.assertIsNotNone(so)

    def test_record_trade_with_dataclass(self):
        so = StrategyOptimizer()
        outcome = TradeOutcome(
            token_address="0x" + "A" * 40,
            pnl_percent=15.0,
            exit_reason="take_profit",
            timestamp=time.time(),
        )
        so.record_trade(outcome)
        self.assertEqual(so.stats["trades_recorded"], 1)

    def test_record_trades_batch(self):
        so = StrategyOptimizer()
        outcomes = [
            TradeOutcome(
                token_address=f"0x{'A' * 39}{i}",
                pnl_percent=10.0 * i,
            )
            for i in range(5)
        ]
        so.record_trades_batch(outcomes)
        self.assertEqual(so.stats["trades_recorded"], 5)

    def test_detect_market_regime(self):
        so = StrategyOptimizer()
        regime = so.detect_market_regime()
        self.assertIsInstance(regime, MarketRegime)
        self.assertIn(
            regime.regime,
            ["bull", "bear", "sideways", "volatile", "unknown"],
        )

    def test_get_suggestion(self):
        so = StrategyOptimizer()
        suggestion = so.get_suggestion()
        self.assertIsInstance(suggestion, dict)

    def test_get_stats(self):
        so = StrategyOptimizer()
        stats = so.get_stats()
        self.assertIsInstance(stats, dict)
        self.assertIn("trades_recorded", stats)
        self.assertIn("optimizations_run", stats)
        self.assertIn("params_updated", stats)

    def test_set_current_params(self):
        so = StrategyOptimizer()
        sp = StrategyParams(take_profit_pct=60)
        so.set_current_params(sp)
        self.assertEqual(so._current_params.take_profit_pct, 60)

    def test_detect_strategy_degradation(self):
        so = StrategyOptimizer()
        result = so.detect_strategy_degradation()
        self.assertIsInstance(result, dict)

    def test_multiple_trades_then_regime(self):
        so = StrategyOptimizer()
        for i in range(10):
            pnl = 20.0 if i % 3 != 0 else -15.0
            outcome = TradeOutcome(
                token_address=f"0x{'A' * 39}{i}",
                pnl_percent=pnl,
                exit_reason="take_profit" if pnl > 0 else "stop_loss",
                timestamp=time.time(),
            )
            so.record_trade(outcome)
        self.assertEqual(so.stats["trades_recorded"], 10)
        regime = so.detect_market_regime()
        self.assertIsInstance(regime, MarketRegime)

    def test_graceful_without_ml(self):
        """Optimizer works even without numpy/sklearn."""
        so = StrategyOptimizer()
        regime = so.detect_market_regime()
        self.assertIsNotNone(regime)
        suggestion = so.get_suggestion()
        self.assertIsNotNone(suggestion)

    def test_set_emit_callback(self):
        so = StrategyOptimizer()
        cb = MagicMock()
        so.set_emit_callback(cb)
        self.assertEqual(so._emit_callback, cb)

    def test_min_trades_constants(self):
        self.assertEqual(StrategyOptimizer.MIN_TRADES_FOR_OPTIMIZATION, 20)
        self.assertEqual(StrategyOptimizer.MIN_TRADES_FOR_ML, 50)


# ═══════════════════════════════════════════════
#  Integration / Cross-module Tests
# ═══════════════════════════════════════════════

class TestV6ModuleInteraction(unittest.TestCase):
    """v6 modules can work together."""

    def test_mev_analysis_feeds_router(self):
        w3 = MagicMock()
        w3.eth.get_block.return_value = {"transactions": []}
        mev = MEVProtector(w3=w3, chain_id=56)
        router = MultiDexRouter()

        analysis = _run(mev.analyze_threat("0x" + "A" * 40, 0.05))
        self.assertIsNotNone(analysis)

        dexes = router.get_dexes(56)
        self.assertGreater(len(dexes), 0)

    def test_optimizer_with_copy_trader(self):
        w3 = MagicMock()
        so = StrategyOptimizer()
        ct = CopyTrader(w3=w3, chain_id=56)

        ct.add_wallet("0x" + "D" * 40, label="Whale")
        self.assertEqual(len(ct.get_tracked_wallets()), 1)

        outcome = TradeOutcome(
            token_address="0x" + "A" * 40,
            pnl_percent=12.0,
            exit_reason="take_profit",
        )
        so.record_trade(outcome)
        regime = so.detect_market_regime()
        self.assertIsNotNone(regime)

    def test_all_modules_instantiate(self):
        w3 = MagicMock()
        be = BacktestEngine(w3=w3, chain_id=56)
        ct = CopyTrader(w3=w3, chain_id=56)
        mp = MEVProtector(w3=w3, chain_id=56)
        mdr = MultiDexRouter()
        so = StrategyOptimizer()

        self.assertIsNotNone(be)
        self.assertIsNotNone(ct)
        self.assertIsNotNone(mp)
        self.assertIsNotNone(mdr)
        self.assertIsNotNone(so)

    def test_backtest_config_to_optimizer(self):
        """BacktestConfig fields align with StrategyParams."""
        cfg = BacktestConfig(
            take_profit_pct=50,
            stop_loss_pct=20,
            buy_amount_native=0.1,
        )
        sp = StrategyParams(
            take_profit_pct=cfg.take_profit_pct,
            stop_loss_pct=cfg.stop_loss_pct,
            buy_amount_native=cfg.buy_amount_native,
        )
        self.assertEqual(sp.take_profit_pct, 50)
        self.assertEqual(sp.buy_amount_native, 0.1)

    def test_router_chain_matches_engine(self):
        """Router and engine use same chain IDs."""
        w3 = MagicMock()
        router = MultiDexRouter()
        engine = BacktestEngine(w3=w3, chain_id=56)

        chain = router.get_chain(engine.chain_id)
        self.assertIsNotNone(chain)
        self.assertEqual(chain.chain_id, 56)


# ═══════════════════════════════════════════════
#  Edge Cases
# ═══════════════════════════════════════════════

class TestV6EdgeCases(unittest.TestCase):
    """Edge cases and boundary conditions."""

    def test_mev_zero_amount(self):
        w3 = MagicMock()
        w3.eth.get_block.return_value = {"transactions": []}
        mp = MEVProtector(w3=w3, chain_id=56)
        result = _run(mp.analyze_threat("0x" + "A" * 40, 0.0))
        self.assertIsInstance(result, MEVAnalysis)

    def test_mev_large_amount(self):
        w3 = MagicMock()
        w3.eth.get_block.return_value = {"transactions": []}
        mp = MEVProtector(w3=w3, chain_id=56)
        result = _run(mp.analyze_threat("0x" + "A" * 40, 100.0))
        self.assertIsInstance(result, MEVAnalysis)

    def test_optimizer_empty_trades(self):
        so = StrategyOptimizer()
        regime = so.detect_market_regime()
        self.assertIsNotNone(regime)
        suggestion = so.get_suggestion()
        self.assertIsNotNone(suggestion)

    def test_copy_trader_remove_nonexistent(self):
        w3 = MagicMock()
        ct = CopyTrader(w3=w3, chain_id=56)
        ct.remove_wallet("0x" + "Z" * 40)
        self.assertEqual(len(ct.get_tracked_wallets()), 0)

    def test_backtest_config_zero_values(self):
        cfg = BacktestConfig(buy_amount_native=0.0, take_profit_pct=0.0)
        self.assertEqual(cfg.buy_amount_native, 0.0)

    def test_strategy_params_vector_length(self):
        sp = StrategyParams()
        v = sp.to_vector()
        names = StrategyParams.feature_names()
        self.assertEqual(len(v), len(names))

    def test_router_get_all_factory_filters(self):
        router = MultiDexRouter()
        filters = router.get_all_factory_filters(56)
        self.assertIsInstance(filters, list)

    def test_backtest_trade_required_field(self):
        """BacktestTrade requires token_address."""
        t = BacktestTrade(token_address="0xABC")
        self.assertEqual(t.token_address, "0xABC")

    def test_copy_trade_signal_required_field(self):
        """CopyTradeSignal requires wallet_address."""
        sig = CopyTradeSignal(wallet_address="0xDEF")
        self.assertEqual(sig.wallet_address, "0xDEF")

    def test_dex_config_required_fields(self):
        """DexConfig requires name and chain_id."""
        dc = DexConfig(name="Test", chain_id=1)
        self.assertEqual(dc.name, "Test")
        self.assertEqual(dc.chain_id, 1)

    def test_chain_config_required_fields(self):
        """ChainConfig requires chain_id, name, symbol."""
        cc = ChainConfig(chain_id=42161, name="Arbitrum", symbol="ETH")
        self.assertEqual(cc.chain_id, 42161)
        self.assertEqual(cc.symbol, "ETH")


if __name__ == "__main__":
    unittest.main()

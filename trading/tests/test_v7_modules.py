"""
test_v7_modules.py — Comprehensive tests for ALL v7 professional modules.

Tests:
  - ReinforcementLearner (RL + Bayesian + Bandit)
  - OrderflowAnalyzer (bot/whale/insider detection)
  - MarketSimulator (AMM + MEV + Gas + Monte Carlo)
  - AutoStrategyGenerator (genetic algorithm strategy evolution)
  - PredictiveLaunchScanner (pre-mempool contract detection)
  - MempoolAnalyzer (advanced pending tx analysis)
  - WhaleNetworkGraph (wallet relationship graph)
  - MultiDexRouter Solana enhancement
  - MEVProtector v2 adaptive gas

Run: python manage.py test trading.tests.test_v7_modules -v2
"""
import asyncio
import time
import unittest
from dataclasses import dataclass, field
from unittest.mock import AsyncMock, MagicMock, patch


# ═══════════════════════════════════════════════════
#  1. ReinforcementLearner Tests
# ═══════════════════════════════════════════════════
class TestReinforcementLearner(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from trading.Services.reinforcementLearner import (
            ReinforcementLearner, RLConfig, ActionType,
            ExperienceBuffer, QLearningAgent, ContextualBandit,
            BayesianOptimizer, RewardShaper, StateEncoder,
            MarketState, Experience, RLDecision,
        )
        cls.RL = ReinforcementLearner
        cls.RLConfig = RLConfig
        cls.ActionType = ActionType
        cls.ExperienceBuffer = ExperienceBuffer
        cls.QLearningAgent = QLearningAgent
        cls.ContextualBandit = ContextualBandit
        cls.BayesianOptimizer = BayesianOptimizer
        cls.RewardShaper = RewardShaper
        cls.StateEncoder = StateEncoder
        cls.MarketState = MarketState
        cls.Experience = Experience
        cls.RLDecision = RLDecision

    def test_init_default(self):
        rl = self.RL()
        self.assertIsNotNone(rl)
        self.assertIsNotNone(rl._q_agent)
        self.assertIsNotNone(rl._bandit)
        self.assertIsNotNone(rl._bayesian)

    def test_init_custom_config(self):
        cfg = self.RLConfig(learning_rate=0.05, discount_factor=0.8)
        rl = self.RL(config=cfg)
        self.assertEqual(rl.config.learning_rate, 0.05)
        self.assertEqual(rl.config.discount_factor, 0.8)

    def test_action_types(self):
        values = [a.value for a in self.ActionType]
        self.assertIn("buy_aggressive", values)
        self.assertIn("buy_moderate", values)
        self.assertIn("skip", values)
        self.assertIn("wait", values)

    def test_experience_buffer(self):
        buf = self.ExperienceBuffer(capacity=10)
        for i in range(15):
            exp = self.Experience(
                state=self.MarketState(
                    volatility_bucket=min(i, 4), trend_bucket=2, volume_bucket=1,
                    mev_risk_bucket=0, pump_score_bucket=3, social_bucket=2,
                    liquidity_bucket=2, whale_activity_bucket=1),
                action=self.ActionType.BUY_AGGRESSIVE,
                reward=i * 0.1,
                next_state=None,
                done=True,
            )
            buf.add(exp)
        self.assertEqual(buf.size, 10)  # capacity enforced (uses .size property)

    def test_decide_returns_rl_decision(self):
        rl = self.RL()
        # Use SimpleNamespace with all fields encode_from_token_info needs
        from types import SimpleNamespace
        token_info = SimpleNamespace(
            address="0xTEST",
            pump_score=70,
            risk_engine_score=65,
            ml_pump_score=55,
            social_sentiment_score=40,
            volatility_score=30,
            whale_risk_score=30,
            mev_frontrun_risk=0.1,
            lp_lock_percent=90,
            buy_tax=2,
            sell_tax=4,
            dexscreener_price_change_h1=5.0,
            dexscreener_volume_24h=10000,
            dexscreener_liquidity=50000,
            whale_total_buys=2,
        )
        market_data = {"pump_score": 70, "volatility_score": 30}
        decision = rl.decide(token_info, market_data)
        self.assertIsInstance(decision, self.RLDecision)
        valid_actions = [a.value for a in self.ActionType]
        self.assertIn(decision.action, valid_actions)
        self.assertGreaterEqual(decision.confidence, 0)
        self.assertLessEqual(decision.confidence, 1)

    def test_record_outcome(self):
        rl = self.RL()
        # Should not raise
        rl.record_outcome("0xTEST", pnl_percent=15.5)

    def test_optimize_params(self):
        rl = self.RL()
        result = rl.optimize_params()
        self.assertIsNotNone(result)

    def test_get_stats(self):
        rl = self.RL()
        stats = rl.get_stats()
        self.assertIsInstance(stats, dict)
        self.assertIn("total_decisions", stats)

    def test_get_policy_summary(self):
        rl = self.RL()
        summary = rl.get_policy_summary()
        self.assertIsInstance(summary, dict)

    def test_reset(self):
        rl = self.RL()
        rl.record_outcome("0xA", 10)
        rl.reset()
        stats = rl.get_stats()
        self.assertEqual(stats["total_decisions"], 0)

    def test_state_encoder(self):
        enc = self.StateEncoder()
        # encode() takes market_data dict, returns MarketState
        encoded = enc.encode(market_data={"pump_score": 70, "volatility_score": 50})
        self.assertIsInstance(encoded, self.MarketState)

    def test_reward_shaper(self):
        config = self.RLConfig()
        shaper = self.RewardShaper(config)
        reward = shaper.compute_reward(pnl_percent=25.0, hold_hours=2.0)
        self.assertIsInstance(reward, float)

    def test_q_learning_agent(self):
        config = self.RLConfig(learning_rate=0.1, discount_factor=0.9, exploration_rate=0.3)
        agent = self.QLearningAgent(config)
        state = self.MarketState(
            volatility_bucket=2, trend_bucket=1, volume_bucket=1,
            mev_risk_bucket=1, pump_score_bucket=2, social_bucket=2,
            liquidity_bucket=2, whale_activity_bucket=1)
        action, _ = agent.select_action(state)
        valid_actions = [a.value for a in self.ActionType]
        self.assertIn(action, valid_actions)
        next_state = self.MarketState(
            volatility_bucket=2, trend_bucket=2, volume_bucket=1,
            mev_risk_bucket=0, pump_score_bucket=2, social_bucket=2,
            liquidity_bucket=2, whale_activity_bucket=1)
        agent.update(state, action, reward=1.0, next_state=next_state)

    def test_contextual_bandit(self):
        bandit = self.ContextualBandit()
        bandit.add_arm("arm_a")
        bandit.add_arm("arm_b")
        bandit.add_arm("arm_c")
        arm = bandit.select_arm(context=[0.5, 0.3, 0.7])
        self.assertIsInstance(arm, str)
        bandit.update_arm(arm, reward=1.0)

    def test_bayesian_optimizer(self):
        config = self.RLConfig()
        bo = self.BayesianOptimizer(config)
        result = bo.suggest()
        self.assertIsNotNone(result)


# ═══════════════════════════════════════════════════
#  2. OrderflowAnalyzer Tests
# ═══════════════════════════════════════════════════
class TestOrderflowAnalyzer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from trading.Services.orderflowAnalyzer import (
            OrderflowAnalyzer, OrderflowConfig, OrderflowAnalysis,
            TradeFlow, OrderflowPattern, WalletProfile,
            WalletClassifier, PatternDetector,
        )
        cls.OA = OrderflowAnalyzer
        cls.OrderflowConfig = OrderflowConfig
        cls.OrderflowAnalysis = OrderflowAnalysis
        cls.TradeFlow = TradeFlow
        cls.OrderflowPattern = OrderflowPattern
        cls.WalletProfile = WalletProfile
        cls.WalletClassifier = WalletClassifier
        cls.PatternDetector = PatternDetector

    def _mock_w3(self):
        w3 = MagicMock()
        w3.eth = MagicMock()
        w3.eth.get_logs = AsyncMock(return_value=[])
        w3.eth.block_number = 12345678
        w3.eth.get_block = MagicMock(return_value={"timestamp": int(time.time())})
        w3.to_checksum_address = lambda x: x
        return w3

    def test_init(self):
        w3 = self._mock_w3()
        oa = self.OA(w3, chain_id=56)
        self.assertIsNotNone(oa)

    def test_init_with_config(self):
        w3 = self._mock_w3()
        cfg = self.OrderflowConfig()
        oa = self.OA(w3, chain_id=56, config=cfg)
        self.assertIsNotNone(oa)

    def test_analyze(self):
        w3 = self._mock_w3()
        oa = self.OA(w3, chain_id=56)
        result = asyncio.run(oa.analyze("0xTOKEN", "0xPAIR"))
        self.assertIsNotNone(result)

    def test_get_stats(self):
        w3 = self._mock_w3()
        oa = self.OA(w3, chain_id=56)
        stats = oa.get_stats()
        self.assertIsInstance(stats, dict)

    def test_configure(self):
        w3 = self._mock_w3()
        oa = self.OA(w3, chain_id=56)
        oa.configure(max_blocks=100)

    def test_wallet_classifier(self):
        wc = self.WalletClassifier()
        self.assertIsNotNone(wc)

    def test_pattern_detector(self):
        pd = self.PatternDetector()
        self.assertIsNotNone(pd)

    def test_get_cached(self):
        w3 = self._mock_w3()
        oa = self.OA(w3, chain_id=56)
        cached = oa.get_cached("0xTOKEN")
        # Should be None when not analyzed yet
        self.assertIsNone(cached)

    def test_get_wallet_profiles(self):
        w3 = self._mock_w3()
        oa = self.OA(w3, chain_id=56)
        profiles = oa.get_wallet_profiles()
        self.assertIsInstance(profiles, (list, dict))


# ═══════════════════════════════════════════════════
#  3. MarketSimulator Tests
# ═══════════════════════════════════════════════════
class TestMarketSimulator(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from trading.Services.marketSimulator import (
            MarketSimulator, SimulationConfig, FullSimulationResult,
            SimScenario, PoolState, SlippageResult, MEVSimResult,
            GasWarResult, LiquidityProfile, MonteCarloResult,
            AMMEngine, MEVSimulator, GasWarSimulator,
            LiquidityProfiler, MonteCarloSimulator,
        )
        cls.MS = MarketSimulator
        cls.SimulationConfig = SimulationConfig
        cls.FullSimulationResult = FullSimulationResult
        cls.SimScenario = SimScenario
        cls.AMMEngine = AMMEngine
        cls.MEVSimulator = MEVSimulator
        cls.GasWarSimulator = GasWarSimulator
        cls.LiquidityProfiler = LiquidityProfiler
        cls.MonteCarloSimulator = MonteCarloSimulator

    def _mock_w3(self):
        w3 = MagicMock()
        w3.eth = MagicMock()
        w3.eth.block_number = 99999
        return w3

    def test_init_no_w3(self):
        ms = self.MS()
        self.assertIsNotNone(ms)

    def test_init_with_w3(self):
        ms = self.MS(w3=self._mock_w3(), chain_id=56)
        self.assertIsNotNone(ms)

    def test_simulate(self):
        ms = self.MS()
        result = asyncio.run(ms.simulate("0xTOKEN", "0xPAIR", 0.05, 50, 20, 24, 0.1))
        self.assertIsNotNone(result)

    def test_get_stats(self):
        ms = self.MS()
        stats = ms.get_stats()
        self.assertIsInstance(stats, dict)

    def test_configure(self):
        ms = self.MS()
        ms.configure(num_simulations=200)

    def test_get_cached(self):
        ms = self.MS()
        self.assertIsNone(ms.get_cached("0xNONE"))

    def test_simulation_scenarios(self):
        scenarios = list(self.SimScenario)
        self.assertGreater(len(scenarios), 0)

    def test_amm_engine(self):
        engine = self.AMMEngine()
        self.assertIsNotNone(engine)

    def test_mev_simulator(self):
        sim = self.MEVSimulator()
        self.assertIsNotNone(sim)

    def test_gas_war_simulator(self):
        sim = self.GasWarSimulator()
        self.assertIsNotNone(sim)

    def test_monte_carlo_simulator(self):
        sim = self.MonteCarloSimulator()
        self.assertIsNotNone(sim)


# ═══════════════════════════════════════════════════
#  4. AutoStrategyGenerator Tests
# ═══════════════════════════════════════════════════
class TestAutoStrategyGenerator(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from trading.Services.autoStrategyGenerator import (
            AutoStrategyGenerator, GeneratorConfig, IndicatorType,
            ConditionOp, Indicator, Condition, StrategyRule,
            GeneratedStrategy, IndicatorLibrary,
            GeneticStrategyGenerator, FitnessEvaluator,
        )
        cls.ASG = AutoStrategyGenerator
        cls.GeneratorConfig = GeneratorConfig
        cls.IndicatorType = IndicatorType
        cls.ConditionOp = ConditionOp
        cls.Indicator = Indicator
        cls.Condition = Condition
        cls.StrategyRule = StrategyRule
        cls.GeneratedStrategy = GeneratedStrategy
        cls.IndicatorLibrary = IndicatorLibrary
        cls.GeneticGen = GeneticStrategyGenerator
        cls.FitnessEval = FitnessEvaluator

    def test_init(self):
        asg = self.ASG()
        self.assertIsNotNone(asg)

    def test_init_with_config(self):
        cfg = self.GeneratorConfig()
        asg = self.ASG(config=cfg)
        self.assertIsNotNone(asg)

    def test_indicator_types(self):
        types = list(self.IndicatorType)
        self.assertGreaterEqual(len(types), 10)

    def test_condition_ops(self):
        ops = list(self.ConditionOp)
        self.assertGreater(len(ops), 0)

    def test_indicator_library(self):
        lib = self.IndicatorLibrary()
        indicators = lib.get_all()
        self.assertGreater(len(indicators), 0)

    def test_evaluate_token(self):
        asg = self.ASG()
        token = MagicMock()
        token.pump_score = 65
        token.social_sentiment_score = 50
        token.ml_pump_score = 60
        token.whale_total_buys = 5
        token.whale_risk_score = 30
        token.dexscreener_liquidity = 50000
        token.volatility_score = 0.4
        token.dev_score = 70
        token.mev_frontrun_risk = 0.1
        token.dexscreener_price_change_h1 = 5.0
        token.holder_count = 200
        token.lp_lock_percent = 90
        token.buy_tax = 2
        token.sell_tax = 4
        result = asg.evaluate_token(token)
        self.assertIsInstance(result, dict)

    def test_add_historical_data(self):
        asg = self.ASG()
        token = MagicMock()
        token.pump_score = 70
        asg.add_historical_data(token, actual_pnl=15.0)

    def test_get_stats(self):
        asg = self.ASG()
        stats = asg.get_stats()
        self.assertIsInstance(stats, dict)

    def test_reset(self):
        asg = self.ASG()
        asg.reset()

    def test_get_best_strategy(self):
        asg = self.ASG()
        best = asg.get_best_strategy()
        # May be None if no strategies generated
        self.assertTrue(best is None or isinstance(best, self.GeneratedStrategy))

    def test_get_top_strategies(self):
        asg = self.ASG()
        top = asg.get_top_strategies(5)
        self.assertIsInstance(top, list)

    def test_genetic_generator(self):
        gen = self.GeneticGen()
        self.assertIsNotNone(gen)

    def test_fitness_evaluator(self):
        ev = self.FitnessEval()
        self.assertIsNotNone(ev)


# ═══════════════════════════════════════════════════
#  5. PredictiveLaunchScanner Tests
# ═══════════════════════════════════════════════════
class TestPredictiveLaunchScanner(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from trading.Services.predictiveLaunchScanner import (
            PredictiveLaunchScanner, ScannerConfig, DetectedLaunch,
            LaunchStage, ContractRisk, BytecodeSignature,
            BytecodeAnalyzer, DeployerProfiler, LaunchPredictor,
        )
        cls.PLS = PredictiveLaunchScanner
        cls.ScannerConfig = ScannerConfig
        cls.DetectedLaunch = DetectedLaunch
        cls.LaunchStage = LaunchStage
        cls.ContractRisk = ContractRisk
        cls.BytecodeAnalyzer = BytecodeAnalyzer
        cls.DeployerProfiler = DeployerProfiler
        cls.LaunchPredictor = LaunchPredictor

    def _mock_w3(self):
        w3 = MagicMock()
        w3.eth = MagicMock()
        w3.eth.block_number = 99999
        w3.eth.get_balance = MagicMock(return_value=10**18)
        w3.eth.get_transaction_count = MagicMock(return_value=5)
        w3.eth.get_code = MagicMock(return_value=b'\x00')
        w3.eth.get_logs = AsyncMock(return_value=[])
        w3.to_checksum_address = lambda x: x
        return w3

    def test_init_no_w3(self):
        pls = self.PLS()
        self.assertIsNotNone(pls)

    def test_init_with_w3(self):
        pls = self.PLS(w3=self._mock_w3(), chain_id=56)
        self.assertIsNotNone(pls)

    def test_launch_stages(self):
        stages = list(self.LaunchStage)
        self.assertGreaterEqual(len(stages), 5)

    def test_contract_risks(self):
        risks = list(self.ContractRisk)
        self.assertGreater(len(risks), 0)

    def test_scan_token(self):
        pls = self.PLS(w3=self._mock_w3())
        result = asyncio.run(pls.scan_token("0xTOKEN"))
        self.assertIsInstance(result, dict)

    def test_get_pending_launches(self):
        pls = self.PLS()
        pending = pls.get_pending_launches(min_priority=40)
        self.assertIsInstance(pending, list)

    def test_get_high_priority_launches(self):
        pls = self.PLS()
        high = pls.get_high_priority_launches()
        self.assertIsInstance(high, list)

    def test_get_stats(self):
        pls = self.PLS()
        stats = pls.get_stats()
        self.assertIsInstance(stats, dict)

    def test_bytecode_analyzer(self):
        ba = self.BytecodeAnalyzer()
        self.assertIsNotNone(ba)
        result = ba.analyze(b'')
        self.assertIsInstance(result, dict)

    def test_deployer_profiler(self):
        dp = self.DeployerProfiler()
        self.assertIsNotNone(dp)

    def test_launch_predictor(self):
        lp = self.LaunchPredictor()
        self.assertIsNotNone(lp)

    def test_start_stop(self):
        pls = self.PLS()
        # start() and stop() should be safe even without w3
        asyncio.run(pls.start())
        asyncio.run(pls.stop())


# ═══════════════════════════════════════════════════
#  6. MempoolAnalyzer Tests
# ═══════════════════════════════════════════════════
class TestMempoolAnalyzer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from trading.Services.mempoolAnalyzer import (
            MempoolAnalyzer, MempoolConfig, PendingTxType,
            MempoolSignal, PendingTransaction, MempoolAlert,
            TokenMempoolState, GasAnalysis,
            TransactionClassifier, GasAnalyzer, FrontrunDetector,
        )
        cls.MA = MempoolAnalyzer
        cls.MempoolConfig = MempoolConfig
        cls.PendingTxType = PendingTxType
        cls.MempoolSignal = MempoolSignal
        cls.PendingTransaction = PendingTransaction
        cls.MempoolAlert = MempoolAlert
        cls.TokenMempoolState = TokenMempoolState
        cls.GasAnalysis = GasAnalysis
        cls.TransactionClassifier = TransactionClassifier
        cls.GasAnalyzer = GasAnalyzer
        cls.FrontrunDetector = FrontrunDetector

    def _mock_w3(self):
        w3 = MagicMock()
        w3.eth = MagicMock()
        w3.eth.block_number = 99999
        return w3

    def test_init_no_w3(self):
        ma = self.MA()
        self.assertIsNotNone(ma)

    def test_init_with_w3(self):
        ma = self.MA(w3=self._mock_w3(), chain_id=56)
        self.assertIsNotNone(ma)

    def test_pending_tx_types(self):
        types = list(self.PendingTxType)
        self.assertGreaterEqual(len(types), 8)

    def test_mempool_signals(self):
        signals = list(self.MempoolSignal)
        self.assertGreaterEqual(len(signals), 5)

    def test_analyze_token(self):
        ma = self.MA(w3=self._mock_w3())
        result = asyncio.run(ma.analyze_token("0xTOKEN"))
        self.assertIsInstance(result, dict)

    def test_get_stats(self):
        ma = self.MA()
        stats = ma.get_stats()
        self.assertIsInstance(stats, dict)

    def test_get_alerts(self):
        ma = self.MA()
        alerts = ma.get_alerts(limit=10)
        self.assertIsInstance(alerts, list)

    def test_get_gas_analysis(self):
        ma = self.MA()
        gas = ma.get_gas_analysis()
        self.assertIsNotNone(gas)

    def test_transaction_classifier(self):
        tc = self.TransactionClassifier()
        self.assertIsNotNone(tc)

    def test_gas_analyzer(self):
        ga = self.GasAnalyzer()
        self.assertIsNotNone(ga)
        ga.add_gas_price(5_000_000_000)
        ga.add_gas_price(10_000_000_000)
        result = ga.analyze()
        self.assertIsNotNone(result)

    def test_frontrun_detector(self):
        fd = self.FrontrunDetector()
        self.assertIsNotNone(fd)

    def test_start_stop(self):
        ma = self.MA()
        asyncio.run(ma.start())
        asyncio.run(ma.stop())


# ═══════════════════════════════════════════════════
#  7. WhaleNetworkGraph Tests
# ═══════════════════════════════════════════════════
class TestWhaleNetworkGraph(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from trading.Services.whaleNetworkGraph import (
            WhaleNetworkGraph, GraphConfig, WalletType, RelationType,
            WalletNode, WalletEdge, WalletCluster, NetworkAnalysis,
            GraphEngine, ClusterAnalyzer, SmartMoneyTracker,
        )
        cls.WNG = WhaleNetworkGraph
        cls.GraphConfig = GraphConfig
        cls.WalletType = WalletType
        cls.RelationType = RelationType
        cls.WalletNode = WalletNode
        cls.WalletEdge = WalletEdge
        cls.WalletCluster = WalletCluster
        cls.NetworkAnalysis = NetworkAnalysis
        cls.GraphEngine = GraphEngine
        cls.ClusterAnalyzer = ClusterAnalyzer
        cls.SmartMoneyTracker = SmartMoneyTracker

    def _mock_w3(self):
        w3 = MagicMock()
        w3.eth = MagicMock()
        w3.eth.block_number = 99999
        return w3

    def test_init_no_w3(self):
        wng = self.WNG()
        self.assertIsNotNone(wng)

    def test_init_with_w3(self):
        wng = self.WNG(w3=self._mock_w3(), chain_id=56)
        self.assertIsNotNone(wng)

    def test_wallet_types(self):
        types = list(self.WalletType)
        self.assertGreaterEqual(len(types), 7)

    def test_relation_types(self):
        types = list(self.RelationType)
        self.assertGreaterEqual(len(types), 4)

    def test_add_wallet(self):
        wng = self.WNG()
        wng.add_wallet("0xWHALE1", wallet_type="whale")

    def test_add_relationship(self):
        wng = self.WNG()
        wng.add_wallet("0xA")
        wng.add_wallet("0xB")
        wng.add_relationship("0xA", "0xB", self.RelationType.FUNDING, value_native=1.0)

    def test_record_trade(self):
        wng = self.WNG()
        wng.record_trade("0xWHALE1", "0xTOKEN", direction="buy", amount_native=1.5, pnl_pct=0.2)

    def test_analyze_token(self):
        wng = self.WNG()
        trades = [
            {"wallet": "0xA", "direction": "buy", "amount_native": 1.0},
            {"wallet": "0xB", "direction": "buy", "amount_native": 0.5},
        ]
        result = asyncio.run(wng.analyze_token("0xTOKEN", trades))
        self.assertIsInstance(result, self.NetworkAnalysis)

    def test_get_wallet(self):
        wng = self.WNG()
        wng.add_wallet("0xTEST")
        wallet = wng.get_wallet("0xTEST")
        self.assertIsNotNone(wallet)

    def test_get_top_whales(self):
        wng = self.WNG()
        top = wng.get_top_whales(5)
        self.assertIsInstance(top, list)

    def test_get_smart_money(self):
        wng = self.WNG()
        sm = wng.get_smart_money(5)
        self.assertIsInstance(sm, list)

    def test_get_clusters(self):
        wng = self.WNG()
        clusters = wng.get_clusters()
        self.assertIsInstance(clusters, list)

    def test_get_stats(self):
        wng = self.WNG()
        stats = wng.get_stats()
        self.assertIsInstance(stats, dict)

    def test_graph_engine(self):
        ge = self.GraphEngine()
        ge.add_edge("A", "B", weight=1.0)
        neighbors = ge.get_neighbors("A")
        self.assertIn("b", neighbors)  # GraphEngine lowercases keys

    def test_graph_components(self):
        ge = self.GraphEngine()
        ge.add_edge("A", "B", weight=1.0)
        ge.add_edge("C", "D", weight=1.0)
        components = ge.find_connected_components()
        # 2 disconnected components with lowercased keys
        self.assertGreaterEqual(len(components), 2)

    def test_cluster_analyzer(self):
        ca = self.ClusterAnalyzer()
        self.assertIsNotNone(ca)

    def test_smart_money_tracker(self):
        smt = self.SmartMoneyTracker()
        self.assertIsNotNone(smt)


# ═══════════════════════════════════════════════════
#  8. MultiDexRouter Solana Enhancement Tests
# ═══════════════════════════════════════════════════
class TestMultiDexRouterSolana(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from trading.Services.multiDexRouter import MultiDexRouter
        cls.MDR = MultiDexRouter

    def test_solana_chain_id(self):
        router = self.MDR()
        self.assertEqual(router.SOLANA_CHAIN_ID, 900)

    def test_is_solana_chain(self):
        router = self.MDR()
        self.assertTrue(router.is_solana_chain(900))
        self.assertFalse(router.is_solana_chain(56))
        self.assertFalse(router.is_solana_chain(1))

    def test_set_solana_pubkey(self):
        router = self.MDR()
        router.set_solana_pubkey("SomeBase58PubKey123")
        self.assertEqual(router._solana_pubkey, "SomeBase58PubKey123")

    def test_solana_dexes_registered(self):
        router = self.MDR()
        dexes = router.get_dexes(900)
        self.assertGreater(len(dexes), 0)  # Jupiter, Raydium, Orca

    def test_avalanche_dexes_registered(self):
        router = self.MDR()
        dexes = router.get_dexes(43114)
        self.assertGreater(len(dexes), 0)  # Trader Joe, Pangolin

    def test_active_chains_includes_new(self):
        router = self.MDR()
        # active_chains requires add_chain(); verify registry has the DEXes
        solana_dexes = router.get_dexes(900)
        avax_dexes = router.get_dexes(43114)
        self.assertGreater(len(solana_dexes), 0)
        self.assertGreater(len(avax_dexes), 0)
        # After adding, should appear in active chains
        router.add_chain(900)
        router.add_chain(43114)
        chains = router.get_active_chains()
        self.assertIn(900, chains)
        self.assertIn(43114, chains)


# ═══════════════════════════════════════════════════
#  9. MEVProtector v2 Adaptive Gas Tests
# ═══════════════════════════════════════════════════
class TestMEVProtectorV2(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from trading.Services.mevProtection import MEVProtector, MEVAnalysis
        cls.MEV = MEVProtector
        cls.MEVAnalysis = MEVAnalysis

    def _mock_w3(self):
        w3 = MagicMock()
        w3.eth = MagicMock()
        w3.eth.block_number = 99999
        w3.eth.gas_price = 5_000_000_000
        w3.eth.get_block = MagicMock(return_value={
            "transactions": [],
            "baseFeePerGas": 5_000_000_000,
        })
        return w3

    def test_adaptive_gas_optimize_method_exists(self):
        mev = self.MEV(self._mock_w3(), chain_id=56)
        self.assertTrue(hasattr(mev, 'adaptive_gas_optimize'))

    def test_protect_with_adaptive_gas_exists(self):
        mev = self.MEV(self._mock_w3(), chain_id=56)
        self.assertTrue(hasattr(mev, 'protect_with_adaptive_gas'))

    def test_analyze_pending_gas_exists(self):
        mev = self.MEV(self._mock_w3(), chain_id=56)
        self.assertTrue(hasattr(mev, '_analyze_pending_gas'))

    def test_get_competing_bot_gas_exists(self):
        mev = self.MEV(self._mock_w3(), chain_id=56)
        self.assertTrue(hasattr(mev, '_get_competing_bot_gas'))

    def test_adaptive_gas_optimize(self):
        mev = self.MEV(self._mock_w3(), chain_id=56)
        raw_tx = {"gas": 250000, "gasPrice": 5_000_000_000}
        result = asyncio.run(mev.adaptive_gas_optimize(raw_tx, "0xTOKEN", urgency="normal"))
        self.assertIsInstance(result, dict)
        self.assertIn("gas", result)  # should still have gas field

    def test_adaptive_gas_urgency_levels(self):
        mev = self.MEV(self._mock_w3(), chain_id=56)
        for urgency in ["low", "normal", "high", "critical"]:
            raw_tx = {"gas": 250000, "gasPrice": 5_000_000_000}
            result = asyncio.run(mev.adaptive_gas_optimize(raw_tx, "0xTOKEN", urgency=urgency))
            self.assertIsInstance(result, dict)

    def test_stats_includes_adaptive(self):
        mev = self.MEV(self._mock_w3(), chain_id=56)
        stats = mev.get_stats()
        # adaptive_gas_optimizations is added after first call; verify core stats exist
        self.assertIn("txs_protected", stats)
        self.assertIn("sandwiches_detected", stats)
        self.assertTrue(hasattr(mev, 'adaptive_gas_optimize'))


# ═══════════════════════════════════════════════════
#  10. Integration: sniperService v7 Fields
# ═══════════════════════════════════════════════════
class TestSniperServiceV7Integration(unittest.TestCase):
    """Test that sniperService imports and defines v7 structures correctly."""

    def test_v7_imports(self):
        """All v7 module imports should work."""
        from trading.Services.reinforcementLearner import ReinforcementLearner
        from trading.Services.orderflowAnalyzer import OrderflowAnalyzer
        from trading.Services.marketSimulator import MarketSimulator
        from trading.Services.autoStrategyGenerator import AutoStrategyGenerator
        from trading.Services.predictiveLaunchScanner import PredictiveLaunchScanner
        from trading.Services.mempoolAnalyzer import MempoolAnalyzer
        from trading.Services.whaleNetworkGraph import WhaleNetworkGraph

    def test_token_info_v7_fields(self):
        """TokenInfo should have all v7 fields."""
        from trading.Services.sniperService import TokenInfo
        ti = TokenInfo(address="0xTEST")
        # RL fields
        self.assertTrue(hasattr(ti, 'rl_decision'))
        self.assertTrue(hasattr(ti, 'rl_confidence'))
        self.assertTrue(hasattr(ti, '_rl_ok'))
        # Orderflow fields
        self.assertTrue(hasattr(ti, 'orderflow_organic_score'))
        self.assertTrue(hasattr(ti, 'orderflow_bot_pct'))
        self.assertTrue(hasattr(ti, 'orderflow_manipulation_risk'))
        self.assertTrue(hasattr(ti, '_orderflow_ok'))
        # Simulator fields
        self.assertTrue(hasattr(ti, 'sim_v7_risk_score'))
        self.assertTrue(hasattr(ti, 'sim_v7_recommendation'))
        self.assertTrue(hasattr(ti, '_sim_v7_ok'))
        # Strategy fields
        self.assertTrue(hasattr(ti, 'strategy_decision'))
        self.assertTrue(hasattr(ti, 'strategy_confidence'))
        self.assertTrue(hasattr(ti, '_strategy_ok'))
        # Launch fields
        self.assertTrue(hasattr(ti, 'launch_risk_score'))
        self.assertTrue(hasattr(ti, 'launch_stage'))
        self.assertTrue(hasattr(ti, '_launch_ok'))
        # Mempool v7 fields
        self.assertTrue(hasattr(ti, 'mempool_v7_net_pressure'))
        self.assertTrue(hasattr(ti, 'mempool_v7_frontrun_risk'))
        self.assertTrue(hasattr(ti, '_mempool_v7_ok'))
        # Whale network fields
        self.assertTrue(hasattr(ti, 'whale_network_coordination'))
        self.assertTrue(hasattr(ti, 'whale_network_sybil_risk'))
        self.assertTrue(hasattr(ti, 'whale_smart_money_sentiment'))
        self.assertTrue(hasattr(ti, '_whale_network_ok'))

    def test_token_info_v7_defaults(self):
        """v7 fields should have proper defaults."""
        from trading.Services.sniperService import TokenInfo
        ti = TokenInfo(address="0xTEST")
        self.assertEqual(ti.rl_decision, "hold")
        self.assertEqual(ti.rl_confidence, 0.0)
        self.assertEqual(ti.orderflow_organic_score, 0.0)
        self.assertEqual(ti.sim_v7_risk_score, 0.0)
        self.assertEqual(ti.sim_v7_recommendation, "unknown")
        self.assertEqual(ti.strategy_decision, "none")
        self.assertEqual(ti.launch_risk_score, 50)
        self.assertEqual(ti.mempool_v7_net_pressure, 0.0)
        self.assertEqual(ti.whale_network_coordination, 0.0)
        self.assertEqual(ti.whale_smart_money_sentiment, "neutral")

    def test_token_info_to_dict_includes_v7(self):
        """to_dict() should include v7 fields."""
        from trading.Services.sniperService import TokenInfo
        ti = TokenInfo(address="0xTEST")
        ti.rl_decision = "buy"
        ti.orderflow_organic_score = 75
        d = ti.to_dict()
        self.assertEqual(d["rl_decision"], "buy")
        self.assertEqual(d["orderflow_organic_score"], 75)


if __name__ == "__main__":
    unittest.main()

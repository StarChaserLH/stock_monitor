"""测试回测引擎核心功能。"""
import pytest
import json
from app.config import AppConfig
from app.strategy.engine import StrategyEngine
from app.market.data import MarketData
from app.backtest.engine import BacktestEngine, BacktestBroker
from app.symbols.manager import SymbolManager


class TestBacktestBroker:
    def test_etf_lot_size(self):
        broker = BacktestBroker(100000, etf_lot=3000, stock_lot=300)
        assert broker.calc_quantity("510050", 3.0) == 3000

    def test_stock_lot_size(self):
        broker = BacktestBroker(100000, etf_lot=3000, stock_lot=300)
        assert broker.calc_quantity("002261", 30.0) == 300

    def test_buy_and_sell(self):
        broker = BacktestBroker(100000)
        assert broker.buy("510050", 3.0, 3000, "2026-01-05") is True
        assert broker.positions["510050"]["shares"] == 3000
        assert broker.cash < 100000 - 3 * 3000

        assert broker.sell("510050", 3.1, 1000, "2026-01-10") is True
        assert broker.positions["510050"]["shares"] == 2000

    def test_sell_more_than_held(self):
        broker = BacktestBroker(100000)
        broker.buy("510050", 3.0, 1000, "2026-01-05")
        assert broker.sell("510050", 3.1, 2000, "2026-01-10") is False

    def test_sell_unknown_symbol(self):
        broker = BacktestBroker(100000)
        assert broker.sell("unknown", 3.0, 1000, "2026-01-05") is False

    def test_equity_calculation(self):
        broker = BacktestBroker(100000)
        broker.buy("510050", 3.0, 3000, "2026-01-05")
        eq = broker.equity({"510050": 3.15})
        assert eq > 100000  # paper profit

    def test_partial_sell_keeps_position(self):
        broker = BacktestBroker(100000)
        broker.buy("510050", 3.0, 3000, "2026-01-05")
        broker.sell("510050", 3.1, 1000, "2026-01-10")
        assert "510050" in broker.positions
        assert broker.positions["510050"]["shares"] == 2000

    def test_full_sell_removes_position(self):
        broker = BacktestBroker(100000)
        broker.buy("510050", 3.0, 3000, "2026-01-05")
        broker.sell("510050", 3.1, 3000, "2026-01-10")
        assert "510050" not in broker.positions


class TestBacktestEngine:
    @pytest.fixture(scope="class")
    def engine(self):
        config = AppConfig()
        market = MarketData(config.market)
        symbols = SymbolManager(config.symbols, market)
        strat_engine = StrategyEngine(config)
        bt = BacktestEngine(config, strat_engine, market)
        return bt, strat_engine, symbols

    def test_run_with_single_strategy(self, engine):
        bt, strat_eng, syms = engine
        strategies = strat_eng.list_all(enabled_only=True)
        if not strategies:
            pytest.skip("No strategies available")
        active = [s for s in syms.get_active_symbols() if s.startswith("51")][:3]

        result = bt.run(
            strategy_id=strategies[0].id,
            symbols=active,
            start_date="2026-06-01",
            end_date="2026-07-18",
            skip_filter=True,
        )

        assert "metrics" in result
        assert "equity" in result
        assert "trades" in result
        m = result["metrics"]
        assert "total_return" in m
        assert "sharpe_ratio" in m
        assert len(result["equity"]) > 0

    def test_run_combined(self, engine):
        bt, strat_eng, syms = engine
        strategies = strat_eng.list_all(enabled_only=True)[:2]
        if len(strategies) < 2:
            pytest.skip("Need at least 2 strategies")
        active = [s for s in syms.get_active_symbols() if s.startswith("51")][:2]

        result = bt.run_combined(
            strategy_ids=[s.id for s in strategies],
            symbols=active,
            start_date="2026-06-01",
            end_date="2026-07-18",
            skip_filter=True,
        )

        assert "metrics" in result
        assert "trades" in result

    def test_invalid_strategy_returns_error(self, engine):
        bt, _, syms = engine
        active = [s for s in syms.get_active_symbols() if s.startswith("51")][:1]
        result = bt.run(
            strategy_id="nonexistent",
            symbols=active,
            start_date="2026-01-01",
            end_date="2026-01-31",
        )
        assert "error" in result

    def test_equity_starts_from_start_date(self, engine):
        bt, strat_eng, syms = engine
        strategies = strat_eng.list_all(enabled_only=True)
        if not strategies:
            pytest.skip("No strategies")
        active = [s for s in syms.get_active_symbols() if s.startswith("51")][:2]

        result = bt.run(
            strategy_id=strategies[0].id,
            symbols=active,
            start_date="2026-03-01",
            end_date="2026-03-31",
            skip_filter=True,
        )

        eq = result["equity"]
        assert eq[0]["date"] == "2026-03-01"
        assert eq[0]["value"] == 100000

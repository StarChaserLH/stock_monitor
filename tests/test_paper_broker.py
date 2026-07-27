"""模拟交易模块测试。"""

import json
from pathlib import Path

import pytest

from app.config import AppConfig
from app.trade.broker import OrderSide, OrderStatus, Position, Account
from app.trade.paper import PaperBroker


@pytest.fixture
def broker(app_config: AppConfig):
    """创建测试用 PaperBroker。"""
    # 使用独立的数据文件
    app_config.system.data_dir = "data/test_broker"
    b = PaperBroker(app_config)
    yield b
    # 清理
    state_file = Path(app_config.system.data_dir) / "paper_account.json"
    if state_file.exists():
        state_file.unlink()
    db_dir = Path(app_config.system.data_dir)
    if db_dir.exists():
        import shutil
        shutil.rmtree(db_dir, ignore_errors=True)


class TestPaperBrokerInit:
    """账户初始化测试。"""

    def test_initial_capital(self, broker):
        assert broker._initial_capital == 100000.0
        assert broker._cash == 100000.0

    def test_empty_positions(self, broker):
        assert len(broker.get_positions()) == 0

    def test_account_summary(self, broker):
        acc = broker.get_account()
        assert acc.total_capital == 100000.0
        assert acc.available_cash == 100000.0
        assert acc.market_value == 0.0
        assert acc.total_pnl == 0.0


class TestBuy:
    """买入测试。"""

    def test_simple_buy(self, broker):
        order = broker.submit_order("510050", OrderSide.BUY, 2.50, 1000, "测试买入")
        assert order.status == OrderStatus.FILLED
        assert order.filled_quantity == 1000
        pos = broker.get_position("510050")
        assert pos is not None
        assert pos.shares == 1000
        assert pos.avg_cost > 0

    def test_buy_rounds_to_lot_size(self, broker):
        """买入数量自动取整到100的倍数。"""
        order = broker.submit_order("510050", OrderSide.BUY, 2.50, 155, "测试")
        assert order.filled_quantity == 100  # 155 -> 100

    def test_insufficient_funds(self, broker):
        """资金不足时应被拒绝。"""
        order = broker.submit_order("510050", OrderSide.BUY, 2.50, 100000, "大额买入")
        # 应该被拒绝或减少数量
        assert order.filled_quantity < 100000
        if order.status == OrderStatus.FILLED:
            # 或者系统自动调整数量
            assert order.filled_quantity * order.filled_price <= broker._initial_capital

    def test_commission_charged(self, broker):
        """佣金应该被扣除。"""
        initial_cash = broker._cash
        order = broker.submit_order("510050", OrderSide.BUY, 2.50, 1000, "测试")
        assert order.commission >= 5.0  # 最低佣金 5 元
        acc = broker.get_account()
        assert acc.available_cash < initial_cash


class TestSell:
    """卖出测试。"""

    def test_simple_sell(self, broker):
        """买入后卖出。"""
        broker.submit_order("510050", OrderSide.BUY, 2.50, 1000, "买")
        cash_before = broker._cash
        order = broker.submit_order("510050", OrderSide.SELL, 2.60, 1000, "卖")
        assert order.status == OrderStatus.FILLED
        assert order.filled_price <= 2.60  # 考虑滑点
        pos = broker.get_position("510050")
        assert pos is None or pos.shares == 0

    def test_sell_without_position(self, broker):
        """无持仓卖出应被拒绝。"""
        order = broker.submit_order("510050", OrderSide.SELL, 2.50, 1000, "无持仓卖")
        assert order.status == OrderStatus.REJECTED

    def test_sell_partial(self, broker):
        """部分卖出。"""
        broker.submit_order("510050", OrderSide.BUY, 2.50, 1000, "买")
        order = broker.submit_order("510050", OrderSide.SELL, 2.60, 500, "卖一半")
        assert order.status == OrderStatus.FILLED
        pos = broker.get_position("510050")
        assert pos is not None
        assert pos.shares == 500


class TestContextAndOrders:
    """Context 生成和委托记录测试。"""

    def test_get_context(self, broker):
        broker.submit_order("510050", OrderSide.BUY, 2.50, 1000, "金叉买入")
        ctx = broker.get_context()
        assert "510050" in ctx["positions"]
        assert ctx["positions"]["510050"] == 1000
        assert "510050" in ctx["holdings"]
        assert ctx["cash"] > 0

    def test_recent_signals(self, broker):
        broker.submit_order("510050", OrderSide.BUY, 2.50, 1000, "信号1")
        broker.submit_order("159915", OrderSide.BUY, 1.80, 2000, "信号2")
        ctx = broker.get_context()
        signals = ctx["signals"]
        assert len(signals) == 2
        assert signals[0]["reason"] == "信号2"
        assert signals[1]["reason"] == "信号1"

    def test_get_orders(self, broker):
        broker.submit_order("510050", OrderSide.BUY, 2.50, 1000, "")
        broker.submit_order("159915", OrderSide.SELL, 1.80, 100, "")
        orders = broker.get_orders()
        assert len(orders) == 2  # 1 filled (buy) + 1 rejected (sell, no position)
        rejected = [o for o in orders if o.status == OrderStatus.REJECTED]
        assert len(rejected) == 1
        assert rejected[0].symbol == "159915"


class TestPositionTracking:
    """持仓跟踪测试。"""

    def test_multiple_buys_avg_cost(self, broker):
        """多次买入应正确计算平均成本。"""
        broker.submit_order("510050", OrderSide.BUY, 2.00, 1000, "")
        broker.submit_order("510050", OrderSide.BUY, 3.00, 1000, "")
        pos = broker.get_position("510050")
        assert pos.shares == 2000
        # 平均成本 = (2.0*1000 + 3.0*1000) / 2000 ≈ 2.50 (考虑滑点)
        assert 2.0 < pos.avg_cost < 3.5

    def test_update_market_prices(self, broker):
        """更新市价应更新持仓市值和浮动盈亏。"""
        broker.submit_order("510050", OrderSide.BUY, 2.50, 1000, "")
        broker.update_market_prices({"510050": 2.80})
        pos = broker.get_position("510050")
        assert pos.market_value == 2800.0
        assert pos.unrealized_pnl > 0


class TestPersistence:
    """账户状态持久化测试。"""

    def test_save_and_load(self, app_config):
        """保存后重新加载应保持状态一致。"""
        import shutil
        orig_dir = app_config.system.data_dir
        app_config.system.data_dir = "data/test_persist"
        try:
            broker1 = PaperBroker(app_config)
            broker1.submit_order("510050", OrderSide.BUY, 2.50, 1000, "")
            cash1 = broker1._cash

            broker2 = PaperBroker(app_config)
            assert broker2._cash == cash1
            pos = broker2.get_position("510050")
            assert pos is not None
            assert pos.shares == 1000
        finally:
            app_config.system.data_dir = orig_dir
            shutil.rmtree("data/test_persist", ignore_errors=True)

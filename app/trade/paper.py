"""
模拟交易模块。

在虚拟账户中执行买卖操作，记录全部委托和持仓变化。
ETF 交易规则：
  - 最小交易单位 100 份
  - 佣金万分之一（0.01%）
  - 免印花税
"""

import json
import logging
import math
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from app.trade.broker import (
    Account,
    BaseBroker,
    Order,
    OrderSide,
    OrderStatus,
    Position,
)

logger = logging.getLogger(__name__)


class PaperBroker(BaseBroker):
    """模拟交易券商。

    在内存中维护虚拟账户，并持久化到 JSON 文件。

    Usage:
        broker = PaperBroker(config)
        order = broker.submit_order("510050", OrderSide.BUY, 2.50, 1000, "均线金叉")
        account = broker.get_account()
    """

    def __init__(self, config):
        """
        Args:
            config: TradingConfig 实例。
        """
        self._app_config = config
        # 提取交易专用配置，兼容传入 AppConfig 或 TradingConfig
        self._cfg = config.trading if hasattr(config, 'trading') else config
        self._data_dir = Path(config.system.data_dir) if hasattr(config, 'system') else Path("data")
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._state_file = self._data_dir / "paper_account.json"

        # 账户状态
        self._cash: float = self._cfg.initial_capital
        self._initial_capital: float = self._cash
        self._positions: dict[str, Position] = {}
        self._orders: list[Order] = []
        self._order_counter: int = 0

        self._load_state()

    # ------------------------------------------------------------------
    # 委托接口
    # ------------------------------------------------------------------

    def submit_order(
        self,
        symbol: str,
        side: OrderSide,
        price: float,
        quantity: int,
        reason: str = "",
    ) -> Order:
        """提交模拟委托并立即成交。

        ETF 规则：最小 100 份，自动取整。
        """
        # 取整到 100 的整数倍
        quantity = (quantity // self._cfg.min_lot_size) * self._cfg.min_lot_size
        if quantity <= 0:
            return self._rejected_order(symbol, side, price, quantity, "数量不足最低交易单位")

        if side == OrderSide.BUY:
            return self._execute_buy(symbol, price, quantity, reason)
        else:
            return self._execute_sell(symbol, price, quantity, reason)

    def cancel_order(self, order_id: str) -> bool:
        """模拟撤单（已成交订单不能撤销）。"""
        for o in self._orders:
            if o.order_id == order_id and o.status == OrderStatus.PENDING:
                o.status = OrderStatus.CANCELLED
                o.updated_at = datetime.now().isoformat()
                self._save_state()
                return True
        return False

    # ------------------------------------------------------------------
    # 查询接口
    # ------------------------------------------------------------------

    def get_account(self) -> Account:
        """获取账户摘要。"""
        market_value = sum(
            p.market_value for p in self._positions.values()
        )
        total = self._cash + market_value
        total_pnl = total - self._initial_capital
        total_pnl_pct = (total_pnl / self._initial_capital * 100) if self._initial_capital > 0 else 0.0

        return Account(
            total_capital=round(total, 2),
            available_cash=round(self._cash, 2),
            market_value=round(market_value, 2),
            total_pnl=round(total_pnl, 2),
            total_pnl_pct=round(total_pnl_pct, 4),
            positions=dict(self._positions),
        )

    def get_position(self, symbol: str) -> Optional[Position]:
        return self._positions.get(symbol)

    def get_positions(self) -> dict[str, Position]:
        return dict(self._positions)

    def get_orders(self, status: OrderStatus | None = None) -> list[Order]:
        if status is None:
            return list(self._orders)
        return [o for o in self._orders if o.status == status]

    # ------------------------------------------------------------------
    # 持仓更新（由外部驱动）
    # ------------------------------------------------------------------

    def update_market_prices(self, prices: dict[str, float]) -> None:
        """根据最新行情更新持仓市值和浮动盈亏。

        Args:
            prices: {symbol: latest_price}
        """
        for symbol, pos in self._positions.items():
            if symbol in prices:
                new_price = prices[symbol]
                pos.market_value = round(pos.shares * new_price, 2)
                pos.unrealized_pnl = round(
                    pos.shares * (new_price - pos.avg_cost), 2
                )

    def get_context(self) -> dict:
        """生成策略执行所需的 context 字典。"""
        account = self.get_account()
        return {
            "positions": {
                sym: pos.shares for sym, pos in self._positions.items()
            },
            "cash": self._cash,
            "holdings": {
                sym: pos.market_value for sym, pos in self._positions.items()
            },
            "signals": self._recent_signals(20),
        }

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _execute_buy(self, symbol: str, price: float, quantity: int, reason: str) -> Order:
        """执行买入。"""
        # 计算含滑点的成交价
        fill_price = price * (1 + self._cfg.slippage)

        # 佣金
        trade_value = fill_price * quantity
        commission = max(5.0, trade_value * self._cfg.commission_rate)

        total_cost = trade_value + commission

        # 检查资金
        if total_cost > self._cash:
            # 计算最大可买数量
            max_qty = int((self._cash - 5.0) / (fill_price * (1 + self._cfg.commission_rate)))
            max_qty = (max_qty // self._cfg.min_lot_size) * self._cfg.min_lot_size
            if max_qty <= 0:
                return self._rejected_order(symbol, OrderSide.BUY, price, quantity, "资金不足")
            quantity = max_qty
            trade_value = fill_price * quantity
            commission = max(5.0, trade_value * self._cfg.commission_rate)
            total_cost = trade_value + commission

        # 检查单标的最大仓位
        max_allowed = self._initial_capital * self._cfg.max_position_ratio
        existing = self._positions.get(symbol, Position(symbol=symbol)).market_value
        if existing + trade_value > max_allowed:
            return self._rejected_order(
                symbol, OrderSide.BUY, price, quantity,
                f"超过单一标的最大仓位限制 ({self._cfg.max_position_ratio * 100:.0f}%)"
            )

        # 扣款
        self._cash -= total_cost

        # 更新持仓
        pos = self._positions.get(symbol)
        if pos is None:
            pos = Position(symbol=symbol, shares=0, avg_cost=0.0)
            self._positions[symbol] = pos

        total_cost_basis = pos.avg_cost * pos.shares + trade_value
        pos.shares += quantity
        pos.avg_cost = total_cost_basis / pos.shares if pos.shares > 0 else 0.0
        pos.market_value = pos.shares * fill_price

        order = Order(
            order_id=self._next_order_id(),
            symbol=symbol,
            side=OrderSide.BUY,
            price=price,
            quantity=quantity,
            status=OrderStatus.FILLED,
            filled_price=fill_price,
            filled_quantity=quantity,
            commission=commission,
            reason=reason,
        )
        self._orders.append(order)
        self._save_state()

        logger.info(
            f"[BUY] {symbol} x{quantity} @ {fill_price:.4f} "
            f"佣金={commission:.2f} 剩余资金={self._cash:.2f}"
        )
        return order

    def _execute_sell(self, symbol: str, price: float, quantity: int, reason: str) -> Order:
        """执行卖出。"""
        pos = self._positions.get(symbol)
        if pos is None or pos.shares <= 0:
            return self._rejected_order(symbol, OrderSide.SELL, price, quantity, "无持仓")

        quantity = min(quantity, pos.shares)
        quantity = (quantity // self._cfg.min_lot_size) * self._cfg.min_lot_size
        if quantity <= 0:
            return self._rejected_order(symbol, OrderSide.SELL, price, quantity, "持仓不足最低交易单位")

        fill_price = price * (1 - self._cfg.slippage)
        trade_value = fill_price * quantity
        commission = max(5.0, trade_value * self._cfg.commission_rate)
        stamp_duty = trade_value * self._cfg.stamp_duty  # ETF 为 0

        net_proceeds = trade_value - commission - stamp_duty
        self._cash += net_proceeds

        pos.shares -= quantity
        if pos.shares <= 0:
            del self._positions[symbol]
        else:
            pos.market_value = pos.shares * fill_price

        order = Order(
            order_id=self._next_order_id(),
            symbol=symbol,
            side=OrderSide.SELL,
            price=price,
            quantity=quantity,
            status=OrderStatus.FILLED,
            filled_price=fill_price,
            filled_quantity=quantity,
            commission=commission,
            reason=reason,
        )
        self._orders.append(order)
        self._save_state()

        logger.info(
            f"[SELL] {symbol} x{quantity} @ {fill_price:.4f} "
            f"佣金={commission:.2f} 可用资金={self._cash:.2f}"
        )
        return order

    def _rejected_order(
        self, symbol: str, side: OrderSide, price: float, quantity: int, reason: str
    ) -> Order:
        order = Order(
            order_id=self._next_order_id(),
            symbol=symbol,
            side=side,
            price=price,
            quantity=quantity,
            status=OrderStatus.REJECTED,
            reason=reason,
        )
        self._orders.append(order)
        logger.warning(f"[REJECTED] {side.value} {symbol} x{quantity}: {reason}")
        return order

    def _next_order_id(self) -> str:
        self._order_counter += 1
        return f"ORD{datetime.now().strftime('%Y%m%d')}-{self._order_counter:05d}"

    def _recent_signals(self, limit: int = 20) -> list[dict]:
        """从历史订单中提取最近信号记录。"""
        signals = []
        for o in reversed(self._orders):
            if o.status == OrderStatus.FILLED and o.reason:
                signals.append({
                    "timestamp": o.created_at,
                    "action": o.side.value,
                    "symbol": o.symbol,
                    "reason": o.reason,
                })
            if len(signals) >= limit:
                break
        return signals

    def _save_state(self) -> None:
        """持久化账户状态。"""
        state = {
            "cash": self._cash,
            "initial_capital": self._initial_capital,
            "order_counter": self._order_counter,
            "positions": {
                sym: {
                    "symbol": p.symbol,
                    "shares": p.shares,
                    "avg_cost": p.avg_cost,
                    "market_value": p.market_value,
                    "unrealized_pnl": p.unrealized_pnl,
                }
                for sym, p in self._positions.items()
            },
            "orders": [
                {
                    "order_id": o.order_id,
                    "symbol": o.symbol,
                    "side": o.side.value,
                    "price": o.price,
                    "quantity": o.quantity,
                    "status": o.status.value,
                    "filled_price": o.filled_price,
                    "filled_quantity": o.filled_quantity,
                    "commission": o.commission,
                    "created_at": o.created_at,
                    "updated_at": o.updated_at,
                    "reason": o.reason,
                }
                for o in self._orders[-500:]  # 只保留最近 500 条
            ],
        }
        self._state_file.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")

    def _load_state(self) -> None:
        """从文件恢复账户状态。"""
        if not self._state_file.exists():
            return

        try:
            state = json.loads(self._state_file.read_text(encoding="utf-8"))
            self._cash = state.get("cash", self._cash)
            self._initial_capital = state.get("initial_capital", self._initial_capital)
            self._order_counter = state.get("order_counter", 0)

            for sym, pos_data in state.get("positions", {}).items():
                self._positions[sym] = Position(
                    symbol=pos_data["symbol"],
                    shares=pos_data["shares"],
                    avg_cost=pos_data["avg_cost"],
                    market_value=pos_data.get("market_value", 0),
                    unrealized_pnl=pos_data.get("unrealized_pnl", 0),
                )

            for od in state.get("orders", []):
                self._orders.append(Order(
                    order_id=od["order_id"],
                    symbol=od["symbol"],
                    side=OrderSide(od["side"]),
                    price=od["price"],
                    quantity=od["quantity"],
                    status=OrderStatus(od["status"]),
                    filled_price=od.get("filled_price", 0),
                    filled_quantity=od.get("filled_quantity", 0),
                    commission=od.get("commission", 0),
                    created_at=od.get("created_at", ""),
                    updated_at=od.get("updated_at", ""),
                    reason=od.get("reason", ""),
                ))

            logger.info(
                f"账户状态已恢复: 资金={self._cash:.2f}, "
                f"持仓={len(self._positions)} 个, 委托={len(self._orders)} 条"
            )
        except Exception as e:
            logger.error(f"账户状态恢复失败: {e}")

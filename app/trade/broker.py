"""
交易接口抽象基类。

定义了统一的委托接口，方便未来对接实盘券商（XTP、EasyTrader 等）。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderStatus(str, Enum):
    PENDING = "pending"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


@dataclass
class Order:
    """委托单。"""
    order_id: str
    symbol: str
    side: OrderSide
    price: float
    quantity: int         # 股数
    status: OrderStatus = OrderStatus.PENDING
    filled_price: float = 0.0
    filled_quantity: int = 0
    commission: float = 0.0
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())
    reason: str = ""


@dataclass
class Position:
    """持仓信息。"""
    symbol: str
    shares: int = 0
    avg_cost: float = 0.0
    market_value: float = 0.0
    unrealized_pnl: float = 0.0


@dataclass
class Account:
    """账户信息。"""
    total_capital: float = 0.0
    available_cash: float = 0.0
    market_value: float = 0.0
    total_pnl: float = 0.0
    total_pnl_pct: float = 0.0
    positions: dict[str, Position] = field(default_factory=dict)


class BaseBroker(ABC):
    """交易接口抽象基类。

    所有交易实现（模拟/实盘）必须继承此类。
    """

    @abstractmethod
    def submit_order(
        self,
        symbol: str,
        side: OrderSide,
        price: float,
        quantity: int,
        reason: str = "",
    ) -> Order:
        """提交委托。

        Args:
            symbol: 证券代码。
            side: 买卖方向。
            price: 委托价格。
            quantity: 委托数量（股）。
            reason: 委托原因（策略信号描述）。

        Returns:
            Order 对象。
        """
        ...

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """撤销委托。"""
        ...

    @abstractmethod
    def get_account(self) -> Account:
        """获取账户信息。"""
        ...

    @abstractmethod
    def get_position(self, symbol: str) -> Optional[Position]:
        """获取指定标的持仓。"""
        ...

    @abstractmethod
    def get_positions(self) -> dict[str, Position]:
        """获取所有持仓。"""
        ...

    @abstractmethod
    def get_orders(self, status: OrderStatus | None = None) -> list[Order]:
        """获取委托列表。"""
        ...

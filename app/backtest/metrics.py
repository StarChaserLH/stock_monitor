"""
回测绩效指标计算。

输入每日权益曲线，输出标准量化指标。
"""

import numpy as np
import pandas as pd


def compute_metrics(equity_curve: list[dict], trades: list[dict],
                    initial_capital: float, risk_free_rate: float = 0.03) -> dict:
    """从权益曲线和交易记录计算绩效指标。

    Args:
        equity_curve: [{"date": ..., "value": ...}, ...]
        trades: [{"symbol": ..., "action": ..., "price": ..., "quantity": ..., "date": ...}, ...]
        initial_capital: 初始资金
        risk_free_rate: 无风险利率（默认3%）

    Returns:
        指标 dict
    """
    if not equity_curve or len(equity_curve) < 2:
        return _empty_metrics()

    values = pd.Series([e["value"] for e in equity_curve])
    dates = pd.to_datetime([e["date"] for e in equity_curve])

    # 日收益率
    daily_returns = values.pct_change().dropna()

    # 总交易日数
    total_days = (dates[-1] - dates[0]).days
    trading_days = len(values)

    # 总收益率
    total_return = (values.iloc[-1] / initial_capital) - 1

    # 年化收益率
    if total_days > 0:
        annual_return = (1 + total_return) ** (365 / total_days) - 1
    else:
        annual_return = 0.0

    # 最大回撤
    cummax = values.cummax()
    drawdowns = (values - cummax) / cummax
    max_drawdown = drawdowns.min()

    # 夏普比率
    if len(daily_returns) > 0 and daily_returns.std() > 0:
        excess = daily_returns.mean() - risk_free_rate / 252
        sharpe = (excess / daily_returns.std()) * np.sqrt(252)
    else:
        sharpe = 0.0

    # 交易统计
    buy_trades = [t for t in trades if t["action"] == "buy"]
    sell_trades = [t for t in trades if t["action"] == "sell"]
    total_trades = len(trades)

    # 胜率 & 盈亏比（配对 buy-sell）
    win_rate, profit_factor, avg_win_pct, avg_loss_pct, win_count, loss_count = \
        _trade_analysis(trades)

    # 日均换手（平均每天交易次数）
    avg_daily_trades = total_trades / trading_days if trading_days > 0 else 0

    # 波动率
    volatility = daily_returns.std() * np.sqrt(252) if len(daily_returns) > 0 else 0

    # 卡尔马比率（年化收益 / 最大回撤绝对值）
    calmar = annual_return / abs(max_drawdown) if max_drawdown != 0 else 0

    return {
        "total_return": round(total_return * 100, 2),
        "annual_return": round(annual_return * 100, 2),
        "max_drawdown": round(max_drawdown * 100, 2),
        "sharpe_ratio": round(sharpe, 2),
        "calmar_ratio": round(calmar, 2),
        "volatility": round(volatility * 100, 2),
        "total_trades": total_trades,
        "buy_count": len(buy_trades),
        "sell_count": len(sell_trades),
        "win_count": win_count,
        "loss_count": loss_count,
        "win_rate": round(win_rate * 100, 2),
        "profit_factor": round(profit_factor, 2) if profit_factor != float("inf") else 99.99,
        "avg_win_pct": round(avg_win_pct * 100, 2),
        "avg_loss_pct": round(avg_loss_pct * 100, 2),
        "avg_daily_trades": round(avg_daily_trades, 2),
        "initial_capital": initial_capital,
        "final_value": round(values.iloc[-1], 2),
        "trading_days": trading_days,
    }


def _trade_analysis(trades: list[dict]) -> tuple:
    """分析交易胜率和盈亏比。

    将 buy-sell 配对计算每笔交易的盈亏。
    未平仓的 buy 按最后一笔价格估值。
    """
    buy_stack = []  # [(price, quantity), ...]
    completed = []

    for t in trades:
        if t["action"] == "buy":
            buy_stack.append((t["price"], t["quantity"]))
        elif t["action"] == "sell" and buy_stack:
            buy_price, qty = buy_stack.pop(0)
            sell_price = t["price"]
            pnl_pct = (sell_price / buy_price) - 1
            completed.append(pnl_pct)

    if not completed:
        return 0.0, 0.0, 0.0, 0.0, 0, 0

    wins = [p for p in completed if p > 0]
    losses = [p for p in completed if p <= 0]

    win_rate = len(wins) / len(completed)
    avg_win = sum(wins) / len(wins) if wins else 0
    avg_loss = sum(losses) / len(losses) if losses else 0
    profit_factor = (sum(wins) / abs(sum(losses))) if losses and sum(losses) != 0 else float("inf")

    return win_rate, profit_factor, avg_win, avg_loss, len(wins), len(losses)


def _empty_metrics() -> dict:
    return {
        "total_return": 0, "annual_return": 0, "max_drawdown": 0,
        "sharpe_ratio": 0, "calmar_ratio": 0, "volatility": 0,
        "total_trades": 0, "buy_count": 0, "sell_count": 0,
        "win_count": 0, "loss_count": 0, "win_rate": 0,
        "profit_factor": 0, "avg_win_pct": 0, "avg_loss_pct": 0,
        "avg_daily_trades": 0, "initial_capital": 0, "final_value": 0,
        "trading_days": 0,
    }

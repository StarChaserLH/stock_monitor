"""
LOF 溢价率套利策略（预置模板）。

当 LOF 基金溢价率大于阈值时产生卖出信号（如果持有），
当折价率大于阈值时产生买入信号。

此文件可直接被策略引擎加载使用。
用户也可在 Web 界面用自然语言描述生成类似策略。
"""


def strategy(context: dict, data) -> dict:
    """LOF 溢价率策略。

    Args:
        context: 包含 positions/cash/signals 的上下文。
        data: 必须包含 premium_rate 列的 DataFrame。

    Returns:
        {"action": "buy"/"sell"/"hold", "reason": str, "strength": float}
    """
    if data is None or len(data) == 0:
        return {"action": "hold", "reason": "数据不足", "strength": 0.0}

    if "premium_rate" not in data.columns:
        return {"action": "hold", "reason": "缺少溢价率数据", "strength": 0.0}

    latest = data.iloc[-1]
    premium_rate = latest.get("premium_rate")

    if premium_rate is None or pd.isna(premium_rate):
        return {"action": "hold", "reason": "溢价率数据缺失", "strength": 0.0}

    # 需要将 numpy 类型转为 Python 原生类型避免序列化问题
    premium_rate = float(premium_rate)

    # LOF 高溢价 → 卖出信号
    PREMIUM_SELL_THRESHOLD = 8.0
    # LOF 高折价 → 买入信号
    DISCOUNT_BUY_THRESHOLD = 3.0

    if premium_rate >= PREMIUM_SELL_THRESHOLD:
        strength = min(1.0, premium_rate / 15.0)
        return {
            "action": "sell",
            "reason": f"LOF溢价率 {premium_rate:.2f}% 超过卖出阈值 {PREMIUM_SELL_THRESHOLD}%",
            "strength": strength,
        }

    if premium_rate <= -DISCOUNT_BUY_THRESHOLD:
        strength = min(1.0, abs(premium_rate) / 10.0)
        return {
            "action": "buy",
            "reason": f"LOF折价率 {abs(premium_rate):.2f}% 超过买入阈值 {DISCOUNT_BUY_THRESHOLD}%",
            "strength": strength,
        }

    return {"action": "hold", "reason": "无信号", "strength": 0.0}


# pandas 引用供沙箱使用
try:
    import pandas as pd
except ImportError:
    pd = None

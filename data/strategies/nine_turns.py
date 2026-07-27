def strategy(context, data):
    """神奇九转: 连续9天收盘价高于/低于4天前则反转。

    买入(红九): 连续9天 close[i] < close[i-4], 第9天成买入信号
    卖出(绿九): 连续9天 close[i] > close[i-4], 第9天成卖出信号
    """
    if data is None or len(data) < 13:
        return {"action": "hold", "reason": "数据不足(需13条K线)", "strength": 0.0}

    close = data["close"]
    n = len(close)

    # 买入: 检查最近是否连续9天低于4天前
    buy_count = 0
    for i in range(n - 1, n - 10, -1):
        if i - 4 >= 0 and close.iloc[i] < close.iloc[i - 4]:
            buy_count += 1
        else:
            break

    # 卖出: 检查最近是否连续9天高于4天前
    sell_count = 0
    for i in range(n - 1, n - 10, -1):
        if i - 4 >= 0 and close.iloc[i] > close.iloc[i - 4]:
            sell_count += 1
        else:
            break

    if buy_count >= 9:
        # 信号强度基于与阈值的天数差
        s = min(1.0, buy_count / 10)
        return {"action": "buy", "reason": f"红九买入(连续{buy_count}天低于4日前, 底部反转)", "strength": s}
    if sell_count >= 9:
        s = min(1.0, sell_count / 10)
        return {"action": "sell", "reason": f"绿九卖出(连续{sell_count}天高于4日前, 顶部反转)", "strength": s}
    return {"action": "hold", "reason": "", "strength": 0.0}

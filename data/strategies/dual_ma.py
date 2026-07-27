def symbols(all_symbols, meta):
    return [s for s in all_symbols if s.startswith(("5", "1", "58"))]

def strategy(context, data):
    """双均线金叉死叉 (MA50 × MA200)。

    金叉买入、死叉卖出，适合中长期趋势跟踪。
    持仓周期通常以月计，虚假信号少但信号数量也少。
    """
    if data is None or len(data) < 201:
        return {"action": "hold", "reason": "数据不足(需200+条K线)", "strength": 0.0}

    close = data["close"]
    ma50 = close.rolling(50).mean()
    ma200 = close.rolling(200).mean()

    cur_50, cur_200 = ma50.iloc[-1], ma200.iloc[-1]
    prev_50, prev_200 = ma50.shift(1).iloc[-1], ma200.shift(1).iloc[-1]

    if pd.isna(cur_200) or cur_200 <= 0:
        return {"action": "hold", "reason": "MA200未就绪", "strength": 0.0}

    # 买入：金叉 — MA50 上穿 MA200
    if prev_50 <= prev_200 and cur_50 > cur_200:
        dist = (cur_50 / cur_200 - 1) * 100
        s = min(1.0, round(dist / 2, 2) if dist > 0 else 0.3)
        return {"action": "buy", "reason": f"MA50上穿MA200(金叉, 间距{dist:.2f}%)", "strength": max(0.5, s)}

    # 卖出：死叉 — MA50 下穿 MA200
    if prev_50 >= prev_200 and cur_50 < cur_200:
        dist = (cur_200 / cur_50 - 1) * 100
        s = min(1.0, round(dist / 2, 2) if dist > 0 else 0.3)
        return {"action": "sell", "reason": f"MA50下穿MA200(死叉, 间距{dist:.2f}%)", "strength": max(0.5, s)}

    return {"action": "hold", "reason": "", "strength": 0.0}

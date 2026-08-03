def strategy(context, data):
    """布林带收缩突破 (Bollinger Squeeze)。

    带宽压缩到 60 日最低时预示变盘，方向确认后大概率走一段趋势。
    买入：收缩 + 价格上穿中轨(MA20)
    卖出：收缩 + 价格下穿中轨
    """
    if data is None or len(data) < 61:
        return {"action": "hold", "reason": "数据不足(需60+条K线)", "strength": 0.0}

    close = data["close"]
    ma20 = close.rolling(20).mean()
    std20 = close.rolling(20).std()
    upper = ma20 + 2 * std20
    lower = ma20 - 2 * std20

    # 带宽
    band_width = (upper - lower) / ma20
    # 带宽 60 日最低
    bw_min_60 = band_width.rolling(60).min()

    cur_c = close.iloc[-1]
    cur_m = ma20.iloc[-1]
    cur_bw = band_width.iloc[-1]
    cur_bw_min = bw_min_60.iloc[-1]

    prev_c = close.shift(1).iloc[-1]
    prev_m = ma20.shift(1).iloc[-1]

    if pd.isna(cur_bw) or pd.isna(cur_bw_min) or cur_m <= 0:
        return {"action": "hold", "reason": "", "strength": 0.0}

    # 收缩判断：当前带宽在 60 日最低的 1.05 倍以内
    is_squeeze = cur_bw / cur_bw_min < 1.05

    if is_squeeze:
        # 买入：收缩 + 价格上穿中轨
        if prev_c <= prev_m and cur_c > cur_m:
            s = min(1.0, round((cur_c / cur_m - 1) * 10, 2))
            return {"action": "buy", "reason": f"布林收缩突破(带宽压至{cur_bw:.1%})", "strength": max(0.5, s)}
        # 卖出：收缩 + 价格下穿中轨
        if prev_c >= prev_m and cur_c < cur_m:
            s = min(1.0, round((cur_m / cur_c - 1) * 10, 2))
            return {"action": "sell", "reason": f"布林收缩下破(带宽{cur_bw:.1%})", "strength": max(0.4, s)}

    return {"action": "hold", "reason": "", "strength": 0.0}

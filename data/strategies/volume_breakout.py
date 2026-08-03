def strategy(context, data):
    if data is None or len(data) < 21:
        return {"action": "hold", "reason": "数据不足", "strength": 0.0}
    close = data["close"]
    volume = data["volume"]
    if volume.isna().all() or (volume == 0).all():
        return {"action": "hold", "reason": "无成交量数据", "strength": 0.0}
    ma20 = close.rolling(20).mean()
    avg_vol = volume.rolling(20).mean()

    cur_c, cur_m = close.iloc[-1], ma20.iloc[-1]
    prev_c, prev_m = close.shift(1).iloc[-1], ma20.shift(1).iloc[-1]
    cur_v, avg_v = volume.iloc[-1], avg_vol.iloc[-1]

    if avg_v <= 0 or cur_m <= 0:
        return {"action": "hold", "reason": "", "strength": 0.0}

    # 买入：上穿MA20 + 放量1.5倍
    if prev_c <= prev_m and cur_c > cur_m and cur_v > avg_v * 1.5:
        ratio = cur_v / avg_v
        s = min(1.0, round((ratio - 1) * 0.5, 2))
        return {"action": "buy", "reason": f"放量突破MA20(量比{ratio:.1f})", "strength": max(0.5, s)}

    # 卖出：下穿MA20 或 放量跌破MA20
    if prev_c >= prev_m and cur_c < cur_m:
        s = min(1.0, round((cur_m / cur_c - 1) * 10, 2))
        tag = "放量" if cur_v > avg_v * 1.5 else ""
        return {"action": "sell", "reason": f"{tag}跌破MA20(价格{cur_c:.2f}<MA20{cur_m:.2f})", "strength": max(0.4, s)}

    return {"action": "hold", "reason": "", "strength": 0.0}

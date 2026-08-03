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
    cur_v, avg_v = volume.iloc[-1], avg_vol.iloc[-1]

    if cur_m <= 0 or avg_v <= 0:
        return {"action": "hold", "reason": "", "strength": 0.0}

    dist = cur_c / cur_m - 1

    # 买入：价格在MA20上方2%以内 + 缩量 < 70%
    if 0 < dist < 0.02 and cur_v < avg_v * 0.7:
        shrink = cur_v / avg_v
        s = min(1.0, round((1 - shrink) * 2, 2))
        return {"action": "buy", "reason": f"缩量回踩MA20(缩至{shrink:.0%})", "strength": max(0.3, s)}

    # 卖出：远离MA20超过5%且开始放量（主力出货信号）
    if dist > 0.05 and cur_v > avg_v * 1.2:
        s = min(1.0, round(dist * 10, 2))
        return {"action": "sell", "reason": f"放量滞涨(偏离MA20 {dist:.1%})", "strength": max(0.4, s)}

    return {"action": "hold", "reason": "", "strength": 0.0}

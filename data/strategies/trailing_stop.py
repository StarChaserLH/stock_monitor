def symbols(all_symbols, meta):
    return [s for s in all_symbols if s.startswith(("5", "1", "58"))]

def strategy(context, data):
    """ATR 动态移动止盈。

    用 ATR(14) × 2 替代固定 5% 回撤，高波动标止盈位宽、低波动窄。
    """
    if data is None or len(data) < 21:
        return {"action": "hold", "reason": "数据不足", "strength": 0.0}

    high = data["high"]
    low = data["low"]
    close = data["close"]

    # ATR(14)
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = true_range.rolling(14).mean()

    highest_20 = high.rolling(20).max()

    cur_c = close.iloc[-1]
    cur_high20 = highest_20.iloc[-1]
    cur_atr = atr.iloc[-1]

    if cur_high20 <= 0 or pd.isna(cur_atr) or cur_atr <= 0:
        return {"action": "hold", "reason": "", "strength": 0.0}

    stop_level = cur_high20 - 2 * cur_atr

    if cur_c < stop_level:
        drawdown_pct = (cur_c / cur_high20 - 1) * 100
        s = min(1.0, round(abs(drawdown_pct) / 10, 2))
        return {"action": "sell", "reason": f"ATR止盈: ATR14={cur_atr:.3f}, 2×ATR={cur_atr*2:.3f}, 20日高点={cur_high20:.2f}, 止损线={stop_level:.2f}, 现价{cur_c:.2f}低于止损线", "strength": max(0.4, s)}

    return {"action": "hold", "reason": "", "strength": 0.0}

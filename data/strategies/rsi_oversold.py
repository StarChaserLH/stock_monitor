def symbols(all_symbols, meta):
    return [s for s in all_symbols if s.startswith(("5", "1", "58"))]

def strategy(context, data):
    if data is None or len(data) < 15:
        return {"action": "hold", "reason": "数据不足(需15条K线)", "strength": 0.0}
    close = data["close"]
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.rolling(14).mean()
    avg_loss = loss.rolling(14).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - 100 / (1 + rs)
    if rsi.isna().all() or len(rsi) < 3:
        return {"action": "hold", "reason": "", "strength": 0.0}

    prev_rsi = rsi.iloc[-2]
    cur_rsi = rsi.iloc[-1]
    rsi_3d_ago = rsi.iloc[-3]
    if pd.isna(prev_rsi) or pd.isna(cur_rsi):
        return {"action": "hold", "reason": "", "strength": 0.0}

    # 买入：RSI从30以下回升
    if prev_rsi < 30 and cur_rsi > prev_rsi:
        s = min(1.0, round((cur_rsi - prev_rsi) / 10, 2))
        return {"action": "buy", "reason": f"RSI超卖反弹(RSI {prev_rsi:.0f}→{cur_rsi:.0f})", "strength": max(0.4, s)}

    # 卖出：RSI从70以上回落
    if not pd.isna(rsi_3d_ago) and prev_rsi > 70 and cur_rsi < prev_rsi:
        s = min(1.0, round((prev_rsi - cur_rsi) / 10, 2))
        return {"action": "sell", "reason": f"RSI超买回落(RSI {prev_rsi:.0f}→{cur_rsi:.0f})", "strength": max(0.4, s)}

    return {"action": "hold", "reason": "", "strength": 0.0}

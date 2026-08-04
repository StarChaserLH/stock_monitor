def symbols(all_symbols, meta):
    return [s for s in all_symbols if s.startswith(("5", "1", "58"))]

def strategy(context, data):
    """均线交叉策略（参数化快/慢线）。

    context 可选配置（未设置时使用默认值）：
      - ma_fast: 快线周期，默认 50（短线轮动用 10，中长线趋势用 50）
      - ma_slow: 慢线周期，默认 200（短线轮动用 30，中长线趋势用 200）
      - min_strength: 最小信号强度，默认 0.5

    典型参数组合：
      - 短线轮动: fast=10, slow=30
      - 中线波段: fast=20, slow=60
      - 长线趋势: fast=50, slow=200
    """
    fast_p = context.get("ma_fast", 50)
    slow_p = context.get("ma_slow", 200)
    min_s = context.get("min_strength", 0.5)

    if data is None or len(data) < slow_p + 1:
        return {"action": "hold", "reason": f"数据不足(需{slow_p + 1}条)", "strength": 0.0}

    close = data["close"]
    ma_fast = close.rolling(fast_p).mean()
    ma_slow = close.rolling(slow_p).mean()

    cur_fast = ma_fast.iloc[-1]
    cur_slow = ma_slow.iloc[-1]
    prev_fast = ma_fast.shift(1).iloc[-1]
    prev_slow = ma_slow.shift(1).iloc[-1]

    if pd.isna(cur_slow) or cur_slow <= 0:
        return {"action": "hold", "reason": "", "strength": 0.0}

    # 金叉买入
    if prev_fast <= prev_slow and cur_fast > cur_slow:
        dist = (cur_fast / cur_slow - 1) * 100
        s = round(min(1.0, max(0.3, dist / 2 if dist > 0 else 0.3)), 2)
        if s >= min_s:
            return {"action": "buy", "reason": f"MA{fast_p}上穿MA{slow_p}(金叉, 间距{dist:.2f}%)", "strength": s}
        return {"action": "hold", "reason": "", "strength": 0.0}

    # 死叉卖出
    if prev_fast >= prev_slow and cur_fast < cur_slow:
        dist = (cur_slow / cur_fast - 1) * 100
        s = round(min(1.0, max(0.3, dist / 2 if dist > 0 else 0.3)), 2)
        if s >= min_s:
            return {"action": "sell", "reason": f"MA{fast_p}下穿MA{slow_p}(死叉, 间距{dist:.2f}%)", "strength": s}
        return {"action": "hold", "reason": "", "strength": 0.0}

    return {"action": "hold", "reason": "", "strength": 0.0}
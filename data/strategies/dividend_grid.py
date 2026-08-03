def symbols(all_symbols, meta):
    """蓝筹+ETF：有稳定股息历史的标的。"""
    return [s for s in all_symbols if s.startswith(("5", "1", "6", "0", "3", "58"))]

def cooldown():
    """回测跨日冷却：(买入冷却天数, 卖出冷却天数)。0 表示仅当日去重。"""
    return (20, 5)

def strategy(context, data):
    """红利网格 + 布林收口 + 月线方向（真实股息率版）。

    股息率数据源：
      - 个股：stock_dividend_cninfo → 最近一次每股分红 / 当前股价
      - ETF：510880 红利ETF 股息率作为统一基准
    股息率通过 context["div_yield"] 传入，由调度循环预先计算。

    周期三阶段：
      下跌段 — 股息率低(~4%)、布林上轨下移，大资金撤退
      横盘段 — 布林收口、缩量换手，高位筹码成本下移
      突破段 — 收口完成 + 放量，洗干净的标志

    分级网格（以真实股息率为锚）：
      股息率 > 5.0%  → 观察区  strength ~0.3
      股息率 > 5.5%  → 价值区  strength ~0.5
      股息率 > 6.0%  → 深度价值 strength ~0.7+
    """
    if data is None or len(data) < 250:
        return {"action": "hold", "reason": "数据不足(需250条)", "strength": 0.0}

    close = data["close"]
    volume = data["volume"]

    # 均线
    ma20 = close.rolling(20).mean()
    ma60 = close.rolling(60).mean()

    # 布林 (MA20 ± 2σ)
    std20 = close.rolling(20).std()
    upper = ma20 + 2 * std20
    lower = ma20 - 2 * std20
    band_width = (upper - lower) / ma20

    # 成交量
    avg_vol_20 = volume.rolling(20).mean()

    cur_c = close.iloc[-1]
    cur_ma20 = ma20.iloc[-1]
    cur_ma60 = ma60.iloc[-1]
    cur_bw = band_width.iloc[-1]
    cur_upper = upper.iloc[-1]
    cur_lower = lower.iloc[-1]

    prev_c = close.iloc[-2]
    prev_ma60 = ma60.iloc[-2]
    prev_upper = upper.iloc[-2]
    prev_lower = lower.iloc[-2]
    prev_bw = band_width.iloc[-2]

    bw_min_60 = band_width.rolling(60).min().iloc[-1]

    # =================================================================
    # 因子 0: 真实股息率（从 context 获取）
    # =================================================================
    div_yield = context.get("div_yield", 0.0)  # 单位：%

    # 非分红股直接跳过：股息率 < 2.5% 的个股不适用红利策略
    # ETF 使用 510880 红利基准（通常 > 4%），不会被此条件过滤
    if div_yield < 2.5:
        return {"action": "hold", "reason": "", "strength": 0.0}

    # =================================================================
    # 因子 1: 布林收口（洗盘进度）
    # =================================================================
    upper_desc = bool(cur_upper < prev_upper)   # 上轨下移：高位筹码离场/摊低成本
    lower_asc = bool(cur_lower > prev_lower)     # 下轨上移：承接力量聚集
    bw_shrink = bool(cur_bw < prev_bw)           # 带宽正在收缩
    bw_tight = bool(cur_bw / bw_min_60 < 1.06) if bw_min_60 > 0 else False  # 接近60日最窄

    # =================================================================
    # 因子 2: 月线方向（MA60 20日斜率）
    # =================================================================
    ma60_20d = ma60.iloc[-20] if len(ma60) >= 20 else cur_ma60
    ma60_slope = (cur_ma60 - ma60_20d) / ma60_20d if ma60_20d > 0 else 0
    ma60_flat = abs(ma60_slope) < 0.03     # 月线走平
    ma60_up = bool(ma60_slope > 0) and not bool(ma60_flat)  # 月线开始上翘

    # =================================================================
    # 因子 3: 缩量（底部换手）
    # =================================================================
    cur_vol = volume.iloc[-1]
    avg_vol = avg_vol_20.iloc[-1]
    vol_shrink = bool(cur_vol < avg_vol * 0.75) if avg_vol > 0 else False

    # =================================================================
    # 买入：三因子共振
    #   A. 股息率有吸引力（>= 4.5%）
    #   B. 布林收口中（上轨下移 或 下轨上移）
    #   C. 月线不再加速下跌（走平或拐头）
    # =================================================================
    buy_div = div_yield >= 4.5
    buy_sq = bw_shrink and (upper_desc or lower_asc)
    buy_tr = ma60_flat or ma60_up

    if buy_div and buy_sq and buy_tr:
        # 估值维度：股息率越高强度越大
        div_score = min(1.0, (div_yield - 4.0) / 2.5)  # 4%→0, 5%→0.4, 6%→0.8, 6.5%→1.0
        # 收口维度
        squeeze_score = 0.5 if bw_tight else 0.25 if bw_shrink else 0
        # 缩量加分
        vol_score = 0.15 if vol_shrink else 0

        strength = min(1.0, div_score * 0.6 + squeeze_score + vol_score)

        if div_yield >= 6.0:
            zone = "深度价值区"
        elif div_yield >= 5.5:
            zone = "价值区"
        elif div_yield >= 5.0:
            zone = "观察区"
        else:
            zone = "关注区"

        note = ""
        if bw_tight:
            note += " 收口完成"
        elif upper_desc and lower_asc:
            note += " 双向收口中"
        elif upper_desc:
            note += " 上轨下移"
        elif lower_asc:
            note += " 下轨上移"
        if ma60_flat:
            note += " 月线走平"
        elif ma60_up:
            note += " 月线拐头"

        return {
            "action": "buy",
            "reason": f"红利网格·{zone}(股息率{div_yield:.1f}% 布林{cur_bw:.1%}{note})",
            "strength": max(0.25, round(strength, 2))
        }

    is_etf = context.get("is_etf", False)

    # =================================================================
    # 卖出 1：股息率止盈（个股：股息率跌破 3.5% + 价格 > MA60）
    # =================================================================
    if not is_etf and div_yield > 0 and div_yield < 3.5 and bool(cur_c > cur_ma60):
        strength = min(1.0, round((3.5 - div_yield) * 0.6, 2))
        return {
            "action": "sell",
            "reason": f"股息率止盈(当前{div_yield:.1f}%<3.5% 价格在MA60上方 涨幅已兑现)",
            "strength": max(0.4, strength)
        }

    # =================================================================
    # 卖出 2：上轨下移 + 价格跌破 MA20
    #   个股附加条件：股息率 < 3.5%（大资金撤退）
    #   ETF：只看技术面（510880 基准股息率稳定在 4%+，永远不会 < 3.5%）
    # =================================================================
    sell_tech = upper_desc and bool(cur_c < cur_ma20)
    sell_div_ok = is_etf or (div_yield > 0 and div_yield < 3.5)
    if sell_tech and sell_div_ok:
        if is_etf:
            reason = f"技术卖出(上轨下移 价格跌破MA20 当前{cur_c:.2f}<MA20{cur_ma20:.2f})"
            strength = 0.5
        else:
            strength = min(1.0, (3.5 - div_yield) * 0.8)
            reason = f"高位撤退(股息率仅{div_yield:.1f}% 上轨下移)"
        return {
            "action": "sell",
            "reason": reason,
            "strength": max(0.3, round(strength, 2))
        }

    return {"action": "hold", "reason": "", "strength": 0.0}

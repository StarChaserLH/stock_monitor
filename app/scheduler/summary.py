"""收盘小结 HTML 生成器。"""
import logging; logger = logging.getLogger(__name__)
import html as _html
from datetime import datetime
import pandas as pd


def _esc(text: str) -> str:
    """转义 HTML 特殊字符（< > &）。"""
    return _html.escape(str(text), quote=False)


def _n(code, nm):
    n = nm.get(code, "")
    if n and n != code and n != "nan":
        return _esc(f"{code}({n})")
    return _esc(code)


def _health(vs20, vs60, rsi, pnl):
    s = 0
    if vs20 is not None and abs(vs20) < 50: s += 1 if vs20 > 0 else -1
    if vs60 is not None and abs(vs60) < 50: s += 1 if vs60 > 0 else -1
    if rsi is not None:
        if 30 <= rsi <= 70: s += 1
        elif rsi > 80 or rsi < 20: s -= 1
    if pnl is not None: s += 1 if pnl >= 0 else -1
    return ("<span style='color:#16a34a'>●</span>" if s >= 2
            else "<span style='color:#d97706'>●</span>" if s >= 0
            else "<span style='color:#dc2626'>●</span>")


def _ma(v20, v60):
    def f(v):
        if v is None or abs(v) > 50: return "--"
        c = "#dc2626" if v >= 0 else "#16a34a"
        return f"<span style='color:{c}'>{'↑'if v>=0 else'↓'}{abs(v):.0f}%</span>"
    return f"<span style='font-size:10px'>M20 {f(v20)}<br>M60 {f(v60)}</span>"


def _ind(p, gh):
    try: h = gh(p["symbol"])
    except: return (None,)*5
    if h is None or h.empty or "close" not in h.columns or len(h) < 20: return (None,)*5
    c = h["close"]; pr = p.get("price",0)
    m20 = c.rolling(20).mean().iloc[-1]
    m60 = c.rolling(60).mean().iloc[-1] if len(c) >= 60 else None
    d = c.diff(); g = d.clip(0).rolling(14).mean().iloc[-1]
    l = (-d).clip(0).rolling(14).mean().iloc[-1]
    r = 100-100/(1+g/l) if l>0 else 50
    c5 = (c.iloc[-1]/c.iloc[-5]-1)*100 if len(c)>=5 else None
    c20 = (c.iloc[-1]/c.iloc[-20]-1)*100 if len(c)>=20 else None
    v20 = (pr/m20-1)*100 if pr>0 and m20>0 else None
    v60 = (pr/m60-1)*100 if pr>0 and m60 and m60>0 else None
    return v20,v60,r,c5,c20


def build_closing_summary(quotes, active_symbols, signals_today, positions_with_pnl,
                          strategies, get_history_fn, signal_reviews=None, signal_stats=None):
    sc = "symbol" if "symbol" in quotes.columns else "代码"
    nc = "name" if "name" in quotes.columns else "名称"
    nm = {}
    for _,r in quotes.iterrows():
        s = str(r.get(sc,""))
        if len(s) > 6: s = s[2:]
        nv = str(r.get(nc,""))
        if nv and nv != s and nv != "nan":
            nm[s] = nv

    # Also fill names from active symbols' meta
    now = datetime.now()
    pos_pnl = sum(p.get("pnl",0) for p in positions_with_pnl) if positions_with_pnl else 0
    buy_n = len([s for s in signals_today if s.get("action")=="buy"])
    sell_n = len([s for s in signals_today if s.get("action")=="sell"])

    body = []

    # ── 持仓表 ──
    if positions_with_pnl:
        rows = ""
        for p in positions_with_pnl:
            sym = p["symbol"]
            v20, v60, rsi, c5, c20 = _ind(p, get_history_fn)
            pp = p.get("pnl_pct",0)
            pnl_c = "#dc2626" if (pp or 0)>=0 else "#16a34a"  # 红涨绿跌
            ps = f"+{pp:.1f}%" if (pp or 0)>=0 else f"{pp:.1f}%"
            h = _health(v20 if abs(v20 or 0)<50 else None,
                        v60 if abs(v60 or 0)<50 else None, rsi, pp)
            # 红涨绿跌
            c5c = "#dc2626" if (c5 or 0)>=0 else "#16a34a"
            c20c = "#dc2626" if (c20 or 0)>=0 else "#16a34a"
            rows += (
                f"<tr style='border-bottom:1px solid #f1f5f9'>"
                f"<td style='padding:5px 8px;font-size:12px'>{_n(sym,nm)}</td>"
                f"<td style='padding:5px 8px;font-size:12px'>{h}</td>"
                f"<td style='padding:5px 8px;text-align:right;font-family:monospace;font-size:12px'>{p.get('price',0):.3f}</td>"
                f"<td style='padding:5px 8px;text-align:right;font-family:monospace;font-size:12px;color:{pnl_c}'>{ps}</td>"
                f"<td style='padding:5px 8px;font-size:11px;color:#64748b'>{_ma(v20,v60)}</td>"
                f"<td style='padding:5px 8px;text-align:right;font-family:monospace;font-size:11px;color:{'#dc2626' if (rsi or 0)>=70 else '#16a34a' if (rsi or 0)<=30 else '#334155'}'>{f'{rsi:.0f}' if rsi else '--'}</td>"
                f"<td style='padding:5px 8px;text-align:right;font-family:monospace;font-size:11px;color:{c5c}'>{f'{c5:+.1f}%' if c5 is not None else '--'}</td>"
                f"<td style='padding:5px 8px;text-align:right;font-family:monospace;font-size:11px;color:{c20c}'>{f'{c20:+.1f}%' if c20 is not None else '--'}</td>"
                f"</tr>")
        body.append(
            f"<div style='padding:16px 16px 0'><table style='width:100%;border-collapse:collapse'>"
            f"<thead><tr style='background:#f8fafc;color:#64748b;font-size:11px'>"
            f"<th style='text-align:left;padding:6px 8px'>持仓 {len(positions_with_pnl)} 只</th>"
            f"<th style='text-align:left;padding:6px 8px;width:40px'>状态</th>"
            f"<th style='text-align:right;padding:6px 8px'>现价</th>"
            f"<th style='text-align:right;padding:6px 8px'>盈亏</th>"
            f"<th style='text-align:left;padding:6px 8px'>均线</th>"
            f"<th style='text-align:right;padding:6px 8px'>RSI (14)</th>"
            f"<th style='text-align:right;padding:6px 8px'>5日</th>"
            f"<th style='text-align:right;padding:6px 8px'>20日</th>"
            f"</tr></thead><tbody>{rows}</tbody></table>"
            f"<div style='padding:4px 8px 0;font-size:10px;color:#64748b'>"
            f"<span style='color:#16a34a'>●</span>健康 "
            f"<span style='color:#d97706'>●</span>关注 "
            f"<span style='color:#dc2626'>●</span>危险 · "
            f"RSI <span style='color:#dc2626'>≥70</span>超买 <span style='color:#16a34a'>≤30</span>超卖 · "
            f"涨跌 <span style='color:#dc2626'>红</span>/<span style='color:#16a34a'>绿</span>"
            f"</div></div>")
    else:
        body.append("<div style='padding:16px;text-align:center;color:#94a3b8;font-size:12px'>暂无持仓</div>")

    # ── 信号 ──
    if signals_today:
        held_syms = {p["symbol"] for p in positions_with_pnl}
        hs = [s for s in signals_today if s["symbol"] in held_syms]
        os_ = [s for s in signals_today if s["symbol"] not in held_syms]
        sl = []
        def _render_section(title, color, sg_list, max_show=30):
            if not sg_list: return
            total = len(sg_list)
            shown = sg_list[:max_show]
            sl.append(f"<div style='font-size:12px;font-weight:600;color:{color};margin-bottom:2px'>{_esc(title)} ({total}条)</div>")
            groups: dict[str, list] = {}
            for s in shown:
                groups.setdefault(s.get("symbol",""), []).append(s)
            for sym, items in groups.items():
                tag = "<span style='color:#16a34a'>[卖]</span>" if items[0]["action"]=="sell" else "<span style='color:#dc2626'>[买]</span>"
                reasons = "<br>".join(f"　@{i.get('price',0):.2f} — {_esc(i.get('reason',''))} <span style='color:#94a3b8'>({_esc(i.get('strategy_name',''))})</span>" for i in items)
                sl.append(f"<div style='font-size:12px;padding:1px 0'>{tag} {_n(sym,nm)}<br>{reasons}</div>")
            if total > max_show:
                sl.append(f"<div style='font-size:11px;color:#94a3b8;padding:2px 0'>以上为部分信号，完整列表见 Web 端信号历史</div>")
        _render_section("持仓信号", "#334155", hs, 20)
        _render_section("自选信号", "#64748b", os_, 10)
        body.append(f"<div style='padding:8px 16px'><div style='border-top:1px solid #e2e8f0;padding-top:8px'>{''.join(sl)}</div></div>")

    # ── 警告 ──
    warns = []
    for p in positions_with_pnl:
        sym = p["symbol"]
        v20,v60,rsi,c5,c20 = _ind(p, get_history_fn)
        pp = p.get("pnl_pct",0)
        if pp < -15: warns.append(f"<span style='color:#dc2626'>{_n(sym,nm)}</span> 浮亏{pp:+.1f}%")
        elif rsi and rsi < 25: warns.append(f"<span style='color:#d97706'>{_n(sym,nm)}</span> RSI {rsi:.0f}")
        elif v20 and v60 and abs(v20)<50 and abs(v60)<50 and v20<-10 and v60<-10:
            warns.append(f"<span style='color:#dc2626'>{_n(sym,nm)}</span> 空头排列")
    if warns:
        body.append(
            f"<div style='margin:0 16px 8px;padding:8px 12px;background:#fef2f2;border-left:3px solid #dc2626;font-size:12px'>"
            f"<b>需关注</b> · {' · '.join(warns)}</div>")

    # ── 接近触发 ──
    app = _approaching(quotes, active_symbols, get_history_fn, nm)
    app = [a for a in app if a[0] in {p["symbol"] for p in positions_with_pnl}]
    if app:
        items = " ".join(f"<span style='font-family:monospace'>{n}</span> {d:.1f}%" for _,n,d,_,_ in app[:4])
        body.append(
            f"<div style='margin:0 16px 8px;padding:6px 12px;background:#f8fafc;font-size:11px;color:#64748b'>接近触发: {items}</div>")

    html = f"""<div style='max-width:660px;margin:0 auto;background:#fff;border-radius:6px;overflow:hidden'>
{"".join(body)}
</div>"""
    return html


def _movers(quotes, nm):
    sc = "symbol" if "symbol" in quotes.columns else "代码"
    pc = "pct_change" if "pct_change" in quotes.columns else "涨跌幅"
    if quotes.empty or pc not in quotes.columns: return [],[]
    df = quotes.copy(); df[pc] = pd.to_numeric(df[pc], errors="coerce")
    v = df.dropna(subset=[pc])
    def f(d,c):
        r = []
        for _,rw in d.iterrows():
            s = str(rw.get(sc,"")); s = s[2:] if len(s)>6 else s
            r.append((s, nm.get(s,s), rw[c]))
        return r
    return f(v.nlargest(3,pc), pc), f(v.nsmallest(3,pc), pc)


def _approaching(quotes, syms, gh, nm):
    sc = "symbol" if "symbol" in quotes.columns else "代码"
    pc = "price" if "price" in quotes.columns else "最新价"
    pm = {}
    for _,r in quotes.iterrows():
        s = str(r.get(sc,"")); s = s[2:] if len(s)>6 else s
        pm[s] = float(r.get(pc,0) or 0)
    res = []
    for s in syms:
        p = pm.get(s,0)
        if p <= 0: continue
        try: h = gh(s)
        except: continue
        if h is None or h.empty or "close" not in h.columns or len(h)<20: continue
        m20 = h["close"].rolling(20).mean().iloc[-1]
        if m20 <= 0: continue
        d = abs(p/m20-1)*100
        if d < 2: res.append((s, nm.get(s,s), round(d,1), p, m20))
    res.sort(key=lambda x:x[2])
    return res[:5]

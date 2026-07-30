"""生成邮件预览 HTML，含完整壳子（模拟收件箱效果）。"""
import sys, io, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

from datetime import datetime
from app.config import AppConfig
from app.market.data import MarketData
from app.strategy.engine import StrategyEngine
from app.symbols.manager import SymbolManager
from app.trade.positions import PositionStore
from app.scheduler.summary import build_closing_summary
from app.market.lof_data import LOFDataProvider
from app.market.lof_report import build_report
from app.notify.email_ import EmailNotifier
from app.notify.base import Notification, NotifyLevel
import sqlite3

config = AppConfig()
market = MarketData(config.market)
symbols = SymbolManager(config.symbols, market)
engine = StrategyEngine(config)
store = PositionStore('data/positions.json')
notifier = EmailNotifier(config)

out = os.path.join(os.path.dirname(__file__), '..', 'templates', 'email')
os.makedirs(out, exist_ok=True)

# ── 1. 收盘小结 ──
active = symbols.get_active_symbols()
quotes = market.get_realtime_quotes(active)
market.preload_kline_cache(active[:3])
pos_list = []
for p in store.list_all():
    for _, r in quotes.iterrows():
        s = str(r.get('symbol','')); s = s[2:] if len(s) > 6 else s
        if s == p['symbol']:
            price = float(r.get('price',0) or 0)
            pnl = (price - p['cost']) * p['shares']
            pos_list.append({**p, 'price': price, 'pnl': round(pnl,2), 'pnl_pct': round(pnl/p['cost']/p['shares']*100,2)})
conn = sqlite3.connect('data/strategies/strategies.db'); conn.row_factory = sqlite3.Row
signals = [dict(r) for r in conn.execute(
    'SELECT * FROM signal_log ORDER BY timestamp DESC LIMIT 20').fetchall()]
conn.close()

body1 = build_closing_summary(quotes, active, signals, pos_list, [],
    lambda s: market.get_history(s, 'days', 'daily'),
    engine.review_signals({}), engine.signal_stats())
n1 = Notification(title="收盘小结", content=body1, level=NotifyLevel.WARNING)
with open(os.path.join(out, 'closing_summary.html'), 'w', encoding='utf-8') as f:
    f.write(notifier._format_html(n1))
print(f'closing_summary.html ({len(notifier._format_html(n1))} chars)')

# ── 2. LOF 日报 ──
provider = LOFDataProvider(config.lof)
df = provider.get_lof_premium()
df = df[(df['price'] > 0) & (df['amount'] >= 10000000)]
trade_df = provider.apply_filter(df, min_amount=10000000, subscribe_only=True)
alerts = [a for a in provider.check_alerts(trade_df) if a.get('direction') == 'premium']
body2 = build_report(df, alerts, config.lof.premium_threshold)
n2 = Notification(title="LOF 日报", content=body2, level=NotifyLevel.WARNING)
with open(os.path.join(out, 'lof_report.html'), 'w', encoding='utf-8') as f:
    f.write(notifier._format_html(n2))
print(f'lof_report.html ({len(notifier._format_html(n2))} chars)')

# ── 3. 持仓信号 ──
body_held = (
    "<div style='padding:8px 0;border-bottom:1px solid #f1f5f9'>"
    "<p style='margin:0;font-size:16px;font-weight:700;color:#16a34a'>卖出</p>"
    "<p style='margin:6px 0 0;font-size:15px'>513050[中概互联网ETF]</p>"
    "<p style='margin:2px 0;font-size:13px;color:#64748b'>策略: RSI超卖反弹 · 当前价 1.10</p>"
    "<p style='margin:4px 0;font-size:13px;color:#334155'>RSI超买回落(RSI 79→77)</p>"
    "<p style='margin:0;font-size:11px;color:#94a3b8'>强度: 0.4</p>"
    "</div>"
    "<div style='padding:8px 0;border-bottom:1px solid #f1f5f9'>"
    "<p style='margin:0;font-size:16px;font-weight:700;color:#16a34a'>卖出</p>"
    "<p style='margin:6px 0 0;font-size:15px'>563020[红利低波ETF]</p>"
    "<p style='margin:2px 0;font-size:13px;color:#64748b'>策略: RSI超卖反弹 · 当前价 1.17</p>"
    "<p style='margin:4px 0;font-size:13px;color:#334155'>RSI超买回落(RSI 81→74)</p>"
    "<p style='margin:0;font-size:11px;color:#94a3b8'>强度: 0.8</p>"
    "</div>"
)
n3 = Notification(title="持仓信号 (2条)", content=body_held, level=NotifyLevel.WARNING)
with open(os.path.join(out, 'signal_held.html'), 'w', encoding='utf-8') as f:
    f.write(notifier._format_html(n3))
print(f'signal_held.html ({len(notifier._format_html(n3))} chars)')

# ── 4. 自选信号 ──
body_watch = (
    "<span style='color:#16a34a'>卖</span> 512760[芯片ETF国泰]: RSI超卖反弹、移动止盈<br>"
    "<span style='color:#16a34a'>卖</span> 600703[三安光电]: RSI超卖反弹<br>"
    "<span style='color:#dc2626'>买</span> 515030[新能源车ETF]: RSI超卖反弹、双均线金叉"
)
n4 = Notification(title="自选信号 (2买 8卖)", content=body_watch, level=NotifyLevel.INFO)
with open(os.path.join(out, 'signal_watch.html'), 'w', encoding='utf-8') as f:
    f.write(notifier._format_html(n4))
print(f'signal_watch.html ({len(notifier._format_html(n4))} chars)')

print(f'\nDone -> {out}/')

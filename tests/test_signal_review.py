"""测试信号回看功能。"""
import sqlite3
import datetime
from pathlib import Path
from app.config import AppConfig
from app.strategy.engine import StrategyEngine


class TestSignalReview:
    def test_review_buy_signal_profitable(self, tmp_path):
        """买入信号后价格上涨，应为命中。"""
        db = tmp_path / "test.db"
        engine = _make_engine(str(db))

        # 插入3天前的买入信号
        three_days_ago = (datetime.datetime.now() - datetime.timedelta(days=3)).strftime("%Y-%m-%d")
        _insert_signal(db, three_days_ago, "510050", "buy", 3.0)

        # 当前价格高于信号价
        reviews = engine.review_signals({"510050": 3.15})
        assert len(reviews) > 0
        r = reviews[0]
        assert r["review"]["pnl_pct"] > 0
        assert r["review"]["correct"] is True

    def test_review_sell_signal_profitable(self, tmp_path):
        """卖出信号后价格下跌，应为命中。"""
        db = tmp_path / "test.db"
        engine = _make_engine(str(db))

        three_days_ago = (datetime.datetime.now() - datetime.timedelta(days=3)).strftime("%Y-%m-%d")
        _insert_signal(db, three_days_ago, "510050", "sell", 3.5)

        # 当前价格低于卖出价
        reviews = engine.review_signals({"510050": 3.0})
        r = reviews[0]
        assert r["review"]["pnl_pct"] > 0
        assert r["review"]["correct"] is True

    def test_review_dedup(self, tmp_path):
        """同一信号不会被重复回看。"""
        db = tmp_path / "test.db"
        engine = _make_engine(str(db))

        three_days_ago = (datetime.datetime.now() - datetime.timedelta(days=3)).strftime("%Y-%m-%d")
        _insert_signal(db, three_days_ago, "510050", "buy", 3.0)

        # 第一次回看应有结果
        r1 = engine.review_signals({"510050": 3.15})
        # 第二次回看应跳过（已回看过）
        r2 = engine.review_signals({"510050": 3.15})
        assert len(r2) == 0  # 不会重复回看

    def test_signal_stats(self, tmp_path):
        """信号统计应正确聚合。"""
        db = tmp_path / "test.db"
        engine = _make_engine(str(db))

        three_days_ago = (datetime.datetime.now() - datetime.timedelta(days=3)).strftime("%Y-%m-%d")
        _insert_signal(db, three_days_ago, "510050", "buy", 3.0)

        engine.review_signals({"510050": 3.15})
        stats = engine.signal_stats()
        assert stats["reviewed"] >= 1
        assert stats["accuracy"] >= 0

    def test_empty_review_no_crash(self, tmp_path):
        """没有可回看的信号也不应报错。"""
        db = tmp_path / "test.db"
        engine = _make_engine(str(db))
        reviews = engine.review_signals({"510050": 3.0})
        assert reviews == []


def _make_engine(db_path: str):
    """创建使用测试数据库的 StrategyEngine。"""
    config = AppConfig()
    config.strategy.storage_dir = str(Path(db_path).parent)
    engine = StrategyEngine(config)
    # Override db path
    engine._db_path = Path(db_path)
    engine._init_db()
    return engine


def _insert_signal(db_path: str, date: str, symbol: str, action: str, price: float):
    """向测试数据库插入信号记录。"""
    conn = sqlite3.connect(db_path)
    conn.execute(
        """INSERT INTO signal_log (timestamp, strategy_id, strategy_name, symbol, action, price, reason, strength)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (date + "T10:00:00", "test", "test策略", symbol, action, price, "test_reason", 0.5),
    )
    conn.commit()
    conn.close()

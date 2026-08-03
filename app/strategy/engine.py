"""
策略引擎模块。

负责策略的加载、存储、管理和安全执行。
使用受限环境执行策略代码，避免安全风险。
"""

import hashlib
import logging
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from app.strategy.llm import _safe_builtins

logger = logging.getLogger(__name__)


class StrategyRecord:
    """策略记录数据类。"""

    def __init__(
        self,
        strategy_id: str,
        name: str,
        description: str,
        code: str,
        enabled: bool = True,
        created_at: str | None = None,
        updated_at: str | None = None,
    ):
        self.id = strategy_id
        self.name = name
        self.description = description
        self.code = code
        self.enabled = enabled
        self.created_at = created_at or datetime.now().isoformat()
        self.updated_at = updated_at or datetime.now().isoformat()

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "enabled": self.enabled,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class StrategyEngine:
    """策略引擎。

    管理策略的全生命周期：创建、加载、启用/禁用、删除、执行。

    Usage:
        engine = StrategyEngine(config)
        record = engine.create("均线交叉", "当5日线上穿20日线时买入", code)
        result = engine.execute(record.id, context, data)
    """

    def __init__(self, config):
        """
        Args:
            config: 应用配置实例。
        """
        self._config = config
        self._storage_dir = Path(config.system.data_dir) / config.strategy.storage_dir
        self._storage_dir.mkdir(parents=True, exist_ok=True)
        self._db_path = self._storage_dir / "strategies.db"
        self._init_db()
        self._cache: dict[str, tuple[str, object, object | None]] = {}  # id -> (code_hash, func, filter_func)
        self._record_cache: dict[str, StrategyRecord] = {}  # id -> record

    @contextmanager
    def _get_conn(self):
        """获取数据库连接（上下文管理器，确保关闭）。"""
        conn = sqlite3.connect(str(self._db_path))
        try:
            yield conn
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # CRUD 操作
    # ------------------------------------------------------------------

    def create(
        self,
        name: str,
        description: str,
        code: str,
        enabled: bool = True,
    ) -> StrategyRecord:
        """创建并持久化一条策略。

        Args:
            name: 策略名称。
            description: 自然语言描述。
            code: Python 策略函数源代码。
            enabled: 是否立即启用。

        Returns:
            StrategyRecord。
        """
        strategy_id = uuid.uuid4().hex[:12]
        now = datetime.now().isoformat()

        # 保存源代码文件
        code_file = self._storage_dir / f"{strategy_id}.py"
        code_file.write_text(code, encoding="utf-8")

        # 保存到数据库
        with self._get_conn() as conn:
            conn.execute(
                """INSERT INTO strategies (id, name, description, code_path, enabled, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (strategy_id, name, description, str(code_file), int(enabled), now, now),
            )
            conn.commit()

        record = StrategyRecord(strategy_id, name, description, code, enabled, now, now)
        logger.info(f"策略已创建: {name} (id={strategy_id})")
        return record

    def get(self, strategy_id: str) -> Optional[StrategyRecord]:
        """获取指定策略。"""
        if strategy_id in self._record_cache:
            return self._record_cache[strategy_id]

        with self._get_conn() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM strategies WHERE id = ?", (strategy_id,)
            ).fetchone()

        if row is None:
            return None

        code_path = Path(row["code_path"])
        code = code_path.read_text(encoding="utf-8") if code_path.exists() else ""

        record = StrategyRecord(
            strategy_id=row["id"],
            name=row["name"],
            description=row["description"],
            code=code,
            enabled=bool(row["enabled"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
        self._record_cache[strategy_id] = record
        return record

    def list_all(self, enabled_only: bool = False) -> list[StrategyRecord]:
        """列出所有策略。"""
        with self._get_conn() as conn:
            conn.row_factory = sqlite3.Row
            if enabled_only:
                rows = conn.execute(
                    "SELECT * FROM strategies WHERE enabled = 1 ORDER BY updated_at DESC"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM strategies ORDER BY updated_at DESC"
                ).fetchall()

        records = []
        for row in rows:
            code_path = Path(row["code_path"])
            code = code_path.read_text(encoding="utf-8") if code_path.exists() else ""
            records.append(StrategyRecord(
                strategy_id=row["id"],
                name=row["name"],
                description=row["description"],
                code=code,
                enabled=bool(row["enabled"]),
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            ))
        return records

    def set_enabled(self, strategy_id: str, enabled: bool) -> bool:
        """启用或禁用策略。"""
        with self._get_conn() as conn:
            conn.execute(
                "UPDATE strategies SET enabled = ?, updated_at = ? WHERE id = ?",
                (int(enabled), datetime.now().isoformat(), strategy_id),
            )
            affected = conn.total_changes
            conn.commit()

        self._cache.pop(strategy_id, None)
        self._record_cache.pop(strategy_id, None)

        return affected > 0

    def delete(self, strategy_id: str) -> bool:
        """删除策略。"""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT code_path FROM strategies WHERE id = ?", (strategy_id,)
            ).fetchone()
            conn.execute("DELETE FROM strategies WHERE id = ?", (strategy_id,))
            conn.commit()

        if row:
            code_path = Path(row[0])
            if code_path.exists():
                code_path.unlink()

        self._cache.pop(strategy_id, None)
        self._record_cache.pop(strategy_id, None)
        logger.info(f"策略已删除: {strategy_id}")
        return True

    # ------------------------------------------------------------------
    # 策略执行
    # ------------------------------------------------------------------

    def execute(
        self,
        strategy_id: str,
        context: dict,
        data: pd.DataFrame,
    ) -> dict:
        """安全执行指定策略。

        Args:
            strategy_id: 策略ID。
            context: 上下文字典（持仓、资金等）。
            data: 行情 DataFrame。

        Returns:
            策略执行结果 dict，格式 {"action": ..., "reason": ..., "strength": ...}。
        """
        record = self.get(strategy_id)
        if record is None:
            return {"action": "hold", "reason": "策略不存在", "strength": 0.0}

        if not record.enabled:
            return {"action": "hold", "reason": "策略已禁用", "strength": 0.0}

        try:
            func = self._load_func(record)
            result = func(context, data)
            return self._normalize_result(result)
        except Exception as e:
            logger.error(f"策略执行异常 [{record.name}]: {e}", exc_info=True)
            return {
                "action": "hold",
                "reason": f"执行异常: {e}",
                "strength": 0.0,
            }

    def review_signals(self, price_map: dict[str, float], days_list: list[int] | None = None) -> list[dict]:
        """回看过往信号的盈亏。

        对 days_list 中每个天数的历史信号，对比当日价格计算是否盈利。
        buy 信号: 当日价 > 信号价 = 正确; sell 信号: 当日价 < 信号价 = 正确

        Returns:
            [{"signal": dict, "review": {"days": N, "pnl_pct": X, "correct": bool}}, ...]
        """
        if days_list is None:
            days_list = [3, 7, 21]
        today = datetime.now().strftime("%Y-%m-%d")
        results = []
        with self._get_conn() as conn:
            conn.row_factory = None
            for days in days_list:
                from datetime import timedelta as _td
                target_date = (datetime.now() - _td(days=days)).strftime("%Y-%m-%d")
                rows = conn.execute(
                    """SELECT s.id, s.symbol, s.action, s.price, s.strategy_name, s.timestamp, s.reason
                       FROM signal_log s
                       LEFT JOIN signal_review r ON s.id = r.signal_id AND r.review_date = ?
                       WHERE date(s.timestamp) = ? AND r.signal_id IS NULL
                       ORDER BY s.timestamp""",
                    (today, target_date)
                ).fetchall()
                for row in rows:
                    sig_id, sym, action, sig_price, sname, ts, reason = row
                    cur_price = price_map.get(sym, 0)
                    if cur_price <= 0 or sig_price <= 0:
                        continue
                    if action == "buy":
                        pnl_pct = round((cur_price / sig_price - 1) * 100, 2)
                        correct = 1 if cur_price > sig_price else 0
                    else:
                        pnl_pct = round((sig_price / cur_price - 1) * 100, 2)
                        correct = 1 if cur_price < sig_price else 0
                    # 写入 review
                    conn.execute(
                        """INSERT OR REPLACE INTO signal_review
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        (sig_id, today, sig_price, cur_price, pnl_pct, correct)
                    )
                    results.append({
                        "signal": {"id": sig_id, "symbol": sym, "action": action,
                                   "price": sig_price, "strategy_name": sname,
                                   "timestamp": ts, "reason": reason},
                        "review": {"days": days, "pnl_pct": pnl_pct, "correct": bool(correct),
                                   "review_price": cur_price},
                    })
            conn.commit()
        return results

    def signal_stats(self) -> dict:
        """获取信号全局统计。"""
        with self._get_conn() as conn:
            total_row = conn.execute("SELECT COUNT(*) FROM signal_log").fetchone()
            total = total_row[0] if total_row else 0
            rev_row = conn.execute(
                "SELECT COUNT(*), SUM(correct) FROM signal_review"
            ).fetchone()
            reviewed = rev_row[0] if rev_row else 0
            correct = rev_row[1] if rev_row and rev_row[1] else 0
        return {
            "total_signals": total,
            "reviewed": reviewed,
            "correct": correct or 0,
            "accuracy": round((correct or 0) / reviewed * 100, 1) if reviewed > 0 else 0,
        }

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _init_db(self) -> None:
        """初始化 SQLite 数据库。"""
        with self._get_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS strategies (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    code_path TEXT NOT NULL,
                    enabled INTEGER DEFAULT 1,
                    created_at TEXT,
                    updated_at TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS signal_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    strategy_id TEXT NOT NULL,
                    strategy_name TEXT,
                    symbol TEXT NOT NULL,
                    action TEXT NOT NULL,
                    price REAL,
                    reason TEXT,
                    strength REAL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS signal_review (
                    signal_id INTEGER,
                    review_date TEXT,
                    signal_price REAL,
                    review_price REAL,
                    pnl_pct REAL,
                    correct INTEGER,
                    PRIMARY KEY (signal_id, review_date)
                )
            """)
            conn.commit()

    def _load_func(self, record: StrategyRecord):
        """加载并编译策略函数（带缓存）。"""
        code_hash = hashlib.md5(record.code.encode()).hexdigest()

        if record.id in self._cache:
            cached_hash, cached_func, cached_filter, cached_cooldown = self._cache[record.id]
            if cached_hash == code_hash:
                return cached_func

        import numpy as np
        local_ns: dict = {}
        exec(record.code, {"pd": pd, "np": np, "__builtins__": _safe_builtins()}, local_ns)
        func = local_ns["strategy"]
        filter_func = local_ns.get("symbols")  # 可选的标的过滤函数
        cooldown_func = local_ns.get("cooldown")  # 可选的回测冷却函数
        self._cache[record.id] = (code_hash, func, filter_func, cooldown_func)
        return func

    def get_symbol_filter(self, strategy_id: str):
        """获取策略的标的过滤函数（如果有的话）。

        Returns:
            callable(all_symbols, meta) -> list[str]，或 None。
        """
        record = self._record_cache.get(strategy_id) or self.get(strategy_id)
        if record is None:
            return None
        self._load_func(record)  # ensure cached
        return self._cache[record.id][2]

    def get_cooldown(self, strategy_id: str) -> tuple[int, int]:
        """获取策略的回测冷却周期 (buy_days, sell_days)。0 表示仅当日去重。"""
        record = self._record_cache.get(strategy_id) or self.get(strategy_id)
        if record is None:
            return (0, 0)
        self._load_func(record)
        cooldown_func = self._cache[record.id][3]
        if cooldown_func:
            try:
                return cooldown_func()
            except Exception:
                pass
        return (0, 0)

    @staticmethod
    def _normalize_result(result: dict) -> dict:
        """标准化执行结果。"""
        action = str(result.get("action", "hold")).lower()
        if action not in ("buy", "sell", "hold"):
            action = "hold"
        return {
            "action": action,
            "reason": str(result.get("reason", "")),
            "strength": max(0.0, min(1.0, float(result.get("strength", 0.0)))),
        }

    def log_signal(
        self,
        strategy_id: str,
        strategy_name: str,
        symbol: str,
        action: str,
        price: float,
        reason: str,
        strength: float,
    ) -> None:
        """记录信号到数据库。"""
        with self._get_conn() as conn:
            conn.execute(
                """INSERT INTO signal_log
                   (timestamp, strategy_id, strategy_name, symbol, action, price, reason, strength)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (datetime.now().isoformat(), strategy_id, strategy_name, symbol, action, price, reason, strength),
            )
            conn.commit()

    def has_signal_today(self, strategy_id: str, symbol: str, action: str) -> bool:
        """检查今天同一标的+策略+方向是否已产生过信号。"""
        today = datetime.now().strftime("%Y-%m-%d")
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM signal_log "
                "WHERE strategy_id=? AND symbol=? AND action=? AND timestamp >= ?",
                (strategy_id, symbol, action, today),
            ).fetchone()
            return row[0] > 0

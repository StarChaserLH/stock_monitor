"""
持仓持久化存储。

将用户实际持仓记录在本地 JSON 文件中，支持增删改查。
"""

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class PositionStore:
    """本地 JSON 持仓存储。

    Usage:
        store = PositionStore("data/positions.json")
        store.add("510050", shares=10000, cost=2.935)
        positions = store.list_all()
    """

    def __init__(self, filepath: str = "data/positions.json"):
        self._path = Path(filepath)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._positions: dict[str, dict] = {}
        self._load()

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    def list_all(self) -> list[dict]:
        """返回所有持仓列表。"""
        return list(self._positions.values())

    def get(self, symbol: str) -> dict | None:
        """获取单只持仓。"""
        return self._positions.get(symbol)

    def add(self, symbol: str, shares: int, cost: float, date: str | None = None) -> None:
        """添加或更新持仓。"""
        from datetime import datetime

        self._positions[symbol] = {
            "symbol": symbol,
            "shares": shares,
            "cost": cost,
            "date": date or datetime.now().strftime("%Y-%m-%d"),
        }
        self._save()
        logger.info(f"持仓已更新: {symbol} {shares}股 @{cost}")

    def remove(self, symbol: str) -> bool:
        """删除持仓。"""
        if symbol in self._positions:
            del self._positions[symbol]
            self._save()
            logger.info(f"持仓已删除: {symbol}")
            return True
        return False

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _load(self) -> None:
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
                for item in data:
                    sym = item.get("symbol", "")
                    if sym:
                        self._positions[sym] = item
            except (json.JSONDecodeError, KeyError):
                logger.warning("持仓文件损坏，从空开始")

    def _save(self) -> None:
        self._path.write_text(
            json.dumps(list(self._positions.values()), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

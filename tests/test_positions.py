"""测试持仓存储 PositionStore。"""
import json
import tempfile
from pathlib import Path
from app.trade.positions import PositionStore


class TestPositionStore:
    def test_add_and_get(self, tmp_path):
        f = tmp_path / "test_positions.json"
        store = PositionStore(str(f))
        store.add("510050", shares=10000, cost=2.935, date="2026-07-15")

        pos = store.get("510050")
        assert pos is not None
        assert pos["symbol"] == "510050"
        assert pos["shares"] == 10000
        assert pos["cost"] == 2.935

    def test_list_all(self, tmp_path):
        f = tmp_path / "test_positions.json"
        store = PositionStore(str(f))
        store.add("510050", 10000, 2.935)
        store.add("159915", 5000, 2.410)

        all_pos = store.list_all()
        assert len(all_pos) == 2

    def test_remove(self, tmp_path):
        f = tmp_path / "test_positions.json"
        store = PositionStore(str(f))
        store.add("510050", 10000, 2.935)
        assert store.remove("510050") is True
        assert store.get("510050") is None
        assert store.remove("nonexistent") is False

    def test_update_existing(self, tmp_path):
        f = tmp_path / "test_positions.json"
        store = PositionStore(str(f))
        store.add("510050", 10000, 2.935)
        store.add("510050", 20000, 3.100)  # 更新同一标的

        pos = store.get("510050")
        assert pos["shares"] == 20000
        assert pos["cost"] == 3.100
        assert len(store.list_all()) == 1

    def test_persistence(self, tmp_path):
        f = tmp_path / "test_positions.json"
        store1 = PositionStore(str(f))
        store1.add("510050", 10000, 2.935)

        # 重新加载
        store2 = PositionStore(str(f))
        pos = store2.get("510050")
        assert pos is not None
        assert pos["shares"] == 10000

    def test_empty_store(self, tmp_path):
        f = tmp_path / "test_positions.json"
        store = PositionStore(str(f))
        assert store.list_all() == []
        assert store.get("any") is None

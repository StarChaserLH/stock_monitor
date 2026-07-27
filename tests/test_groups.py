"""测试分组存储 GroupStore 及 API。"""
import json
import pytest
from pathlib import Path
from app.symbols.groups import GroupStore, DEFAULT_GROUPS
from app.config import AppConfig
from app.web.server import create_app


class TestGroupStore:
    def test_default_groups_exist(self):
        assert "全部" in DEFAULT_GROUPS
        assert "ETF" in DEFAULT_GROUPS
        assert "个股" in DEFAULT_GROUPS

    def test_default_etf_filter(self):
        syms = ["510050", "510300", "159915", "002261", "601111"]
        result = DEFAULT_GROUPS["ETF"](syms)
        assert result == ["510050", "510300", "159915"]

    def test_default_stock_filter(self):
        syms = ["510050", "002261", "601111"]
        result = DEFAULT_GROUPS["个股"](syms)
        assert result == ["002261", "601111"]

    def test_save_and_get(self, tmp_path):
        f = tmp_path / "groups.json"
        store = GroupStore(str(f))
        store.save("测试组", ["510050", "159915"], "测试备注")

        syms = store.get_symbols("测试组")
        assert syms == ["510050", "159915"]
        assert store.get_note("测试组") == "测试备注"

    def test_save_default_group_raises(self, tmp_path):
        f = tmp_path / "groups.json"
        store = GroupStore(str(f))
        with pytest.raises(ValueError, match="默认分组"):
            store.save("ETF", ["510050"])

    def test_delete(self, tmp_path):
        f = tmp_path / "groups.json"
        store = GroupStore(str(f))
        store.save("测试组", ["510050"])
        assert store.delete("测试组") is True
        assert store.get_symbols("测试组") == []
        assert store.delete("nonexistent") is False

    def test_list_names(self, tmp_path):
        f = tmp_path / "groups.json"
        store = GroupStore(str(f))
        store.save("A", ["510050"])
        store.save("B", ["159915"])
        names = store.list_names()
        assert "A" in names
        assert "B" in names

    def test_old_format_compat(self, tmp_path):
        """兼容旧格式 {name: [symbols]}"""
        f = tmp_path / "groups.json"
        f.write_text(json.dumps({"旧分组": ["510050", "159915"]}), encoding="utf-8")
        store = GroupStore(str(f))
        assert store.get_symbols("旧分组") == ["510050", "159915"]

    def test_persistence(self, tmp_path):
        f = tmp_path / "groups.json"
        store1 = GroupStore(str(f))
        store1.save("测试", ["510050"], "备注")
        store2 = GroupStore(str(f))
        assert store2.get_symbols("测试") == ["510050"]
        assert store2.get_note("测试") == "备注"


class TestGroupsAPI:
    @pytest.fixture
    def client(self, tmp_path):
        import os
        os.environ["TEST_GROUPS_PATH"] = str(tmp_path / "groups.json")
        config = AppConfig()
        app = create_app(config)
        with app.test_client() as c:
            c.post("/login", data={"password": config.web.password})
            yield c

    def test_list_groups(self, client):
        resp = client.get("/api/groups")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "default" in data
        assert "全G" in data["default"] or "全部" in data["default"]

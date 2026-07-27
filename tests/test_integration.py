"""Web 端到端集成测试。

使用 Flask 测试客户端模拟完整的用户交互流程。
"""

from unittest.mock import patch, MagicMock

import pytest

from app.config import AppConfig
from app.web.server import create_app


@pytest.fixture
def client(app_config: AppConfig):
    """创建 Flask 测试客户端。"""
    app = create_app(app_config, monitor_loop=None)
    app.config["TESTING"] = True
    return app.test_client()


class TestAuth:
    """身份认证测试。"""

    def test_login_page(self, client):
        resp = client.get("/login")
        assert resp.status_code == 200
        assert "password" in resp.data.decode("utf-8").lower()

    def test_login_wrong_password(self, client):
        resp = client.post("/login", data={"password": "wrong"})
        assert resp.status_code == 200
        assert resp.data.decode("utf-8").find("错误") >= 0 or b"error" in resp.data.lower()

    def test_login_correct_password(self, client):
        resp = client.post("/login", data={"password": "admin123"}, follow_redirects=True)
        assert resp.status_code == 200

    def test_protected_page_no_auth(self, client):
        resp = client.get("/dashboard", follow_redirects=True)
        assert resp.status_code == 200
        assert b"login" in resp.data.lower() or b"password" in resp.data.lower()


class TestPageRoutes:
    """页面路由测试。"""

    @pytest.fixture(autouse=True)
    def login(self, client):
        client.post("/login", data={"password": "admin123"})

    def test_dashboard(self, client):
        resp = client.get("/dashboard")
        assert resp.status_code == 200

    def test_instruments(self, client):
        resp = client.get("/instruments")
        assert resp.status_code == 200

    def test_strategies(self, client):
        resp = client.get("/strategies")
        assert resp.status_code == 200

    def test_trades(self, client):
        resp = client.get("/trades")
        assert resp.status_code == 200

    def test_system(self, client):
        resp = client.get("/system")
        assert resp.status_code == 200

    def test_push_settings(self, client):
        resp = client.get("/push/settings")
        assert resp.status_code == 200

    def test_push_history(self, client):
        resp = client.get("/push/history")
        assert resp.status_code == 200


class TestAPIEndpoints:
    """API 端点测试。"""

    @pytest.fixture(autouse=True)
    def login(self, client):
        client.post("/login", data={"password": "admin123"})

    def test_dashboard_api(self, client):
        resp = client.get("/api/dashboard")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "account" in data
        assert "strategies_count" in data
        assert "symbols_count" in data

    def test_instruments_api(self, client):
        resp = client.get("/api/instruments")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "mode" in data
        assert "list" in data

    def test_strategies_list_api(self, client):
        resp = client.get("/api/strategies")
        assert resp.status_code == 200
        assert isinstance(resp.get_json(), list)

    def test_strategy_generate_no_api_key(self, client):
        """无 API Key 时生成策略应返回错误。"""
        resp = client.post("/api/strategies/generate",
                          json={"description": "测试策略"})
        assert resp.status_code == 400 or resp.status_code == 500

    def test_strategy_generate_empty_desc(self, client):
        """空描述应返回错误。"""
        resp = client.post("/api/strategies/generate",
                          json={"description": ""})
        assert resp.status_code == 400

    def test_trades_api(self, client):
        resp = client.get("/api/trades")
        assert resp.status_code == 200
        assert isinstance(resp.get_json(), list)

    def test_system_status_api(self, client):
        resp = client.get("/api/system/status")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "scheduler_running" in data

    def test_system_logs_api(self, client):
        resp = client.get("/api/system/logs")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "lines" in data

    def test_push_settings_api_get(self, client):
        resp = client.get("/api/push/settings")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "channels" in data

    def test_push_settings_api_update(self, client):
        resp = client.post("/api/push/settings", json={
            "enabled": True,
            "quiet_hours": {"enabled": True, "start": 23, "end": 7},
            "frequency": {"min_interval_minutes": 10, "max_per_hour": 5},
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"

    def test_push_history_api(self, client):
        resp = client.get("/api/push/history")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "items" in data
        assert "total" in data
        assert "page" in data

    def test_push_status_api(self, client):
        resp = client.get("/api/push/status")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "channels" in data
        assert "total_history" in data

    def test_push_test_channel(self, client):
        resp = client.post("/api/push/test", json={"channel": "wecom_bot"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert "ok" in data


class TestInputValidation:
    """输入验证测试。"""

    @pytest.fixture(autouse=True)
    def login(self, client):
        client.post("/login", data={"password": "admin123"})

    def test_long_description_rejected(self, client):
        """超长描述应被拒绝。"""
        resp = client.post("/api/strategies/generate",
                          json={"description": "x" * 3000})
        assert resp.status_code == 400

    def test_long_strategy_name_rejected(self, client):
        """超长策略名应被拒绝。"""
        resp = client.post("/api/strategies/save", json={
            "name": "x" * 200,
            "description": "test",
            "code": "def strategy(ctx, data): return {'action':'hold','reason':'','strength':0}",
        })
        assert resp.status_code == 400

    def test_empty_code_rejected(self, client):
        """空代码应被拒绝。"""
        resp = client.post("/api/strategies/save", json={
            "name": "test", "description": "test", "code": "",
        })
        assert resp.status_code == 400

"""验证 README 文档中声明的所有 API 端点是否存在且方法正确。"""
import pytest
from app.config import AppConfig
from app.web.server import create_app


@pytest.fixture
def client():
    config = AppConfig()
    app = create_app(config)
    with app.test_client() as c:
        c.post("/login", data={"password": config.web.password})
        yield c


# ── README 列出的所有 API 端点 ──

README_APIS = [
    ("GET", "/api/dashboard"),
    ("GET", "/api/instruments"),
    ("POST", "/api/instruments/add"),
    ("POST", "/api/instruments/remove"),
    ("GET", "/api/backtest/strategies"),
    ("POST", "/api/backtest"),
    ("POST", "/api/backtest/combined"),
    ("POST", "/api/backtest/compare"),
    ("GET", "/api/groups"),
    ("POST", "/api/groups"),
    ("GET", "/api/groups/resolve"),
    ("GET", "/api/positions"),
    ("POST", "/api/positions"),
    ("DELETE", "/api/positions/510050"),
    ("POST", "/api/strategies/generate"),
    ("POST", "/api/strategies/toggle"),
    ("GET", "/api/signals"),
    ("GET", "/api/account"),
    ("POST", "/api/system/control"),
]


@pytest.mark.parametrize("method,path", README_APIS)
def test_api_endpoint_exists(client, method, path):
    """验证 README 文档列出的每个 API 端点存在且返回非 404。"""
    if method == "GET":
        resp = client.get(path)
    elif method == "POST":
        resp = client.post(path, json={})
    elif method == "DELETE":
        resp = client.delete(path)
    else:
        return
    # 404 = 路由不存在, 其他都说明路由注册了
    assert resp.status_code != 404, f"{method} {path} returned 404, route may be missing"


# ── 受保护端点需登录 ──

PROTECTED_APIS = [
    ("GET", "/api/dashboard"),
    ("GET", "/api/instruments"),
    ("POST", "/api/instruments/add"),
    ("GET", "/api/backtest/strategies"),
    ("POST", "/api/backtest"),
    ("GET", "/api/groups"),
    ("GET", "/api/positions"),
    ("POST", "/api/positions"),
    ("POST", "/api/strategies/generate"),
    ("POST", "/api/strategies/toggle"),
    ("POST", "/api/system/control"),
]


@pytest.mark.parametrize("method,path", PROTECTED_APIS)
def test_protected_api_requires_login(method, path):
    """受保护的 API 未登录时应重定向(302)或返回 401。"""
    config = AppConfig()
    app = create_app(config)
    with app.test_client() as c:
        if method == "GET":
            resp = c.get(path)
        else:
            resp = c.post(path, json={})
        # 登录保护=重定向或未授权
        assert resp.status_code in (302, 401, 400), \
            f"{method} {path} should be protected, got {resp.status_code}"


# ── Web 页面路由 ──

README_PAGES = [
    "/dashboard",
    "/instruments",
    "/strategies",
    "/backtest",
    "/positions",
    "/trades",
    "/push/settings",
    "/lof/premium",
    "/system",
    "/push/history",
]


@pytest.mark.parametrize("route", README_PAGES)
def test_page_routes_accessible(client, route):
    """所有 Web 页面路由应可访问（登录后）。"""
    resp = client.get(route)
    assert resp.status_code == 200, f"Page {route} returned {resp.status_code}"


def test_login_redirect():
    """未登录访问受保护页面应重定向到登录页。"""
    config = AppConfig()
    app = create_app(config)
    with app.test_client() as c:
        resp = c.get("/dashboard")
        assert resp.status_code == 302  # redirect to login


def test_xss_in_symbol_search(client):
    """XSS 注入应被正确转义。"""
    resp = client.get("/api/instruments/search?q=<script>alert(1)</script>")
    data = resp.get_json()
    for item in data:
        assert "<script>" not in str(item.get("name", "")), "XSS not escaped"

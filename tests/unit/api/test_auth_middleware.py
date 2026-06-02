import time
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from apps.api.auth import AuthMiddleware, sign_token, COOKIE_NAME, router


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("APP_SECRET", "s")
    monkeypatch.setenv("APP_PASSCODES", "pw")
    app = FastAPI()
    app.add_middleware(AuthMiddleware)
    app.include_router(router)

    @app.get("/api/health")
    async def health():
        return {"ok": True}

    @app.get("/api/symbols/x")
    async def guarded():
        return {"data": 1}

    return TestClient(app)


def test_auth_disabled_when_no_secret(monkeypatch):
    monkeypatch.delenv("APP_SECRET", raising=False)
    app = FastAPI()
    app.add_middleware(AuthMiddleware)

    @app.get("/api/symbols/x")
    async def guarded():
        return {"data": 1}

    c = TestClient(app)
    assert c.get("/api/symbols/x").status_code == 200


def test_health_exempt_no_cookie(client):
    assert client.get("/api/health").status_code == 200


def test_login_exempt_no_cookie(client):
    assert client.post("/api/auth/login", json={"passcode": "x"}).status_code == 401


def test_guarded_without_cookie_401(client):
    assert client.get("/api/symbols/x").status_code == 401


def test_guarded_with_valid_cookie_200(client):
    client.cookies.set(COOKIE_NAME, sign_token(int(time.time())))
    assert client.get("/api/symbols/x").status_code == 200


def test_options_preflight_exempt(client):
    assert client.options("/api/symbols/x").status_code != 401

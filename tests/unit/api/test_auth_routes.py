import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from apps.api.auth import router, COOKIE_NAME


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("APP_SECRET", "s")
    monkeypatch.setenv("APP_PASSCODES", "letmein,invite2")
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_login_correct_passcode_sets_cookie(client):
    r = client.post("/api/auth/login", json={"passcode": "letmein"})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert COOKIE_NAME in r.cookies


def test_login_second_invite_code_ok(client):
    assert client.post("/api/auth/login", json={"passcode": "invite2"}).status_code == 200


def test_login_wrong_passcode_401(client):
    r = client.post("/api/auth/login", json={"passcode": "nope"})
    assert r.status_code == 401 and COOKIE_NAME not in r.cookies


def test_logout_clears_cookie(client):
    assert client.post("/api/auth/logout").status_code == 200

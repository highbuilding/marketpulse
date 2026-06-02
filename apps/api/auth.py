"""轻量口令鉴权: HMAC 签名 token 写 HttpOnly cookie + 中间件校验。

零新依赖(stdlib hmac)。口令圈子用, 不做用户表。
env: APP_SECRET(签名密钥) / APP_PASSCODES(逗号分隔允许口令)。
未配 APP_SECRET → 鉴权关闭(本地 dev 无需登录)。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time

from fastapi import APIRouter, Body, HTTPException, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

COOKIE_NAME = "mp_session"
TTL_S = 30 * 86400


def _secret() -> bytes:
    s = os.getenv("APP_SECRET", "")
    if not s:
        raise RuntimeError("APP_SECRET 未配置")
    return s.encode()


def sign_token(now: int) -> str:
    payload = base64.urlsafe_b64encode(
        json.dumps({"exp": now + TTL_S}).encode()).decode()
    sig = hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def verify_token(token: str, now: int) -> bool:
    try:
        payload, sig = token.rsplit(".", 1)
        expected = hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return False
        data = json.loads(base64.urlsafe_b64decode(payload))
        return int(data["exp"]) > now
    except Exception:  # noqa: BLE001
        return False


# ── 路由 ──────────────────────────────────────────────────────────────────────

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _passcodes() -> set[str]:
    raw = os.getenv("APP_PASSCODES", "")
    if not raw:
        raise RuntimeError("APP_PASSCODES 未配置")
    return {c.strip() for c in raw.split(",") if c.strip()}


@router.post("/login")
async def login(response: Response, passcode: str = Body("", embed=True)):
    if passcode not in _passcodes():
        raise HTTPException(status_code=401, detail="invalid passcode")
    token = sign_token(int(time.time()))
    response.set_cookie(COOKIE_NAME, token, httponly=True, samesite="lax",
                        max_age=TTL_S, path="/")
    return {"ok": True}


@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


# ── 中间件 ────────────────────────────────────────────────────────────────────

_EXEMPT = ("/api/auth/login", "/api/health")


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # 未配 APP_SECRET → 鉴权关闭(本地 dev 单机无需登录)
        if not os.getenv("APP_SECRET"):
            return await call_next(request)
        path = request.url.path
        if request.method == "OPTIONS" or not path.startswith("/api/"):
            return await call_next(request)
        if any(path.startswith(p) for p in _EXEMPT):
            return await call_next(request)
        token = request.cookies.get(COOKIE_NAME, "")
        if not verify_token(token, int(time.time())):
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
        return await call_next(request)

# 多用户化 ① 安全闸(鉴权 + refill 防护)实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 给 api 加轻量口令鉴权(登录页 + HMAC 签名 cookie + 中间件)+ refill 白名单/去重防护,作为公网部署的最低安全门槛(并顺带给 B-sina 减压)。

**Architecture:** stdlib HMAC 签名 token(零新依赖)写进 HttpOnly cookie;Starlette `BaseHTTPMiddleware` 校验 `/api/*`(豁免 login/health/OPTIONS),失败 401;前端 `/login` 页 + fetch 401→跳登录。refill 在发布前加"白名单(CORE∪watchlist)+ inflight 去重"两道闸。nginx 样例含 TLS+HTTP/2+限流(运维文件)。

**Tech Stack:** FastAPI/Starlette、stdlib hmac/hashlib、Next.js、Redis、pytest。

**Spec:** `docs/superpowers/specs/2026-06-02-multiuser-scaling-design.md` §2。

---

## 文件结构

**新建:**
- `apps/api/auth.py` — `sign_token`/`verify_token`(HMAC)+ `AuthMiddleware` + login/logout router
- `apps/web/app/login/page.tsx` — 登录页
- `apps/web/lib/api_fetch.ts` — fetch 包装(401 → 跳 /login)
- `deploy/nginx.conf` — nginx 样例(TLS/HTTP2/限流/SSE proxy)
- `tests/unit/api/test_auth_token.py` / `test_auth_middleware.py` / `test_refill_guard.py`

**改造:**
- `apps/api/main.py` — 挂 AuthMiddleware + include auth.router
- `apps/api/routes/symbols.py` — `_publish_refill_request` 加白名单+去重;两处调用传 watchlist

---

## Task 1: HMAC 签名 token(sign/verify 纯函数)

**Files:** Create `apps/api/auth.py`(先只放 token 逻辑);Test `tests/unit/api/test_auth_token.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/unit/api/test_auth_token.py
import time
from apps.api.auth import sign_token, verify_token

SECRET_ENV = {"APP_SECRET": "test-secret-xyz"}


def test_sign_verify_roundtrip(monkeypatch):
    monkeypatch.setenv("APP_SECRET", "test-secret-xyz")
    now = 1_000_000
    tok = sign_token(now)
    assert verify_token(tok, now + 10) is True            # 未过期


def test_expired_token_rejected(monkeypatch):
    monkeypatch.setenv("APP_SECRET", "test-secret-xyz")
    tok = sign_token(1_000_000)
    assert verify_token(tok, 1_000_000 + 31 * 86400) is False   # 超 30 天


def test_tampered_token_rejected(monkeypatch):
    monkeypatch.setenv("APP_SECRET", "test-secret-xyz")
    tok = sign_token(1_000_000)
    bad = tok[:-2] + ("aa" if not tok.endswith("aa") else "bb")
    assert verify_token(bad, 1_000_000 + 10) is False


def test_wrong_secret_rejected(monkeypatch):
    monkeypatch.setenv("APP_SECRET", "secret-A")
    tok = sign_token(1_000_000)
    monkeypatch.setenv("APP_SECRET", "secret-B")
    assert verify_token(tok, 1_000_000 + 10) is False


def test_garbage_token_rejected(monkeypatch):
    monkeypatch.setenv("APP_SECRET", "s")
    assert verify_token("not-a-token", 1) is False
    assert verify_token("", 1) is False
```

- [ ] **Step 2: 运行确认失败** — `. .venv/bin/activate && pytest tests/unit/api/test_auth_token.py -v`(ModuleNotFoundError / ImportError)

- [ ] **Step 3: 实现**

```python
# apps/api/auth.py
"""轻量口令鉴权: HMAC 签名 token 写 HttpOnly cookie + 中间件校验。

零新依赖(stdlib hmac)。口令圈子用, 不做用户表。
env: APP_SECRET(签名密钥, 必配) / APP_PASSCODES(逗号分隔的允许口令, 必配)。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time

COOKIE_NAME = "mp_session"
TTL_S = 30 * 86400  # 30 天


def _secret() -> bytes:
    s = os.getenv("APP_SECRET", "")
    if not s:
        raise RuntimeError("APP_SECRET 未配置(公网鉴权必需)")
    return s.encode()


def sign_token(now: int) -> str:
    """签发 token: base64(payload).hexsig, payload={exp}。"""
    payload = base64.urlsafe_b64encode(
        json.dumps({"exp": now + TTL_S}).encode()).decode()
    sig = hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def verify_token(token: str, now: int) -> bool:
    """验签 + 未过期。任何异常视为无效。"""
    try:
        payload, sig = token.rsplit(".", 1)
        expected = hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return False
        data = json.loads(base64.urlsafe_b64decode(payload))
        return int(data["exp"]) > now
    except Exception:  # noqa: BLE001
        return False
```

- [ ] **Step 4: 运行确认通过** — `pytest tests/unit/api/test_auth_token.py -v`(5 PASS)

- [ ] **Step 5: 提交**

```bash
git add apps/api/auth.py tests/unit/api/test_auth_token.py
git commit -m "feat: 鉴权 HMAC 签名 token sign/verify (stdlib, 零依赖)"
```

---

## Task 2: 登录/登出路由

**Files:** Modify `apps/api/auth.py`(加 router);Test `tests/unit/api/test_auth_routes.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/unit/api/test_auth_routes.py
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
    assert r.status_code == 401
    assert COOKIE_NAME not in r.cookies


def test_logout_clears_cookie(client):
    r = client.post("/api/auth/logout")
    assert r.status_code == 200
```

- [ ] **Step 2: 运行确认失败** — `pytest tests/unit/api/test_auth_routes.py -v`(ImportError: router)

- [ ] **Step 3: 实现** — `apps/api/auth.py` 追加(顶部加 `from fastapi import APIRouter, Body, HTTPException, Response`):

```python
router = APIRouter(prefix="/api/auth", tags=["auth"])


def _passcodes() -> set[str]:
    raw = os.getenv("APP_PASSCODES", "")
    if not raw:
        raise RuntimeError("APP_PASSCODES 未配置(公网鉴权必需)")
    return {c.strip() for c in raw.split(",") if c.strip()}


@router.post("/login")
async def login(response: Response, passcode: str = Body("", embed=True)):
    if passcode not in _passcodes():
        raise HTTPException(status_code=401, detail="invalid passcode")
    token = sign_token(int(time.time()))
    response.set_cookie(
        COOKIE_NAME, token, httponly=True, samesite="lax",
        max_age=TTL_S, path="/")
    return {"ok": True}


@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}
```

- [ ] **Step 4: 运行确认通过** — `pytest tests/unit/api/test_auth_routes.py -v`(4 PASS)

- [ ] **Step 5: 提交**

```bash
git add apps/api/auth.py tests/unit/api/test_auth_routes.py
git commit -m "feat: 鉴权 login/logout 路由 (口令校验 + 下发签名 cookie)"
```

---

## Task 3: AuthMiddleware + 接入 main

**Files:** Modify `apps/api/auth.py`(加 middleware)、`apps/api/main.py`;Test `tests/unit/api/test_auth_middleware.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/unit/api/test_auth_middleware.py
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
    async def health():  # 豁免
        return {"ok": True}

    @app.get("/api/symbols/x")
    async def guarded():  # 需鉴权
        return {"data": 1}

    return TestClient(app)


def test_auth_disabled_when_no_secret(monkeypatch):
    # 未配 APP_SECRET(本地 dev)→ 鉴权整体关闭, 守护路由也放行(不破坏 make dev)
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
    # 错口令也应进到路由(返回 401 是路由给的, 不是中间件挡的)
    assert client.post("/api/auth/login", json={"passcode": "x"}).status_code == 401


def test_guarded_without_cookie_401(client):
    assert client.get("/api/symbols/x").status_code == 401


def test_guarded_with_valid_cookie_200(client):
    tok = sign_token(int(time.time()))
    client.cookies.set(COOKIE_NAME, tok)
    assert client.get("/api/symbols/x").status_code == 200


def test_options_preflight_exempt(client):
    # OPTIONS 预检无 cookie 也放行
    assert client.options("/api/symbols/x").status_code != 401
```

- [ ] **Step 2: 运行确认失败** — `pytest tests/unit/api/test_auth_middleware.py -v`(ImportError: AuthMiddleware)

- [ ] **Step 3: 实现** — `apps/api/auth.py` 追加(顶部加 `from starlette.middleware.base import BaseHTTPMiddleware`、`from starlette.requests import Request`、`from starlette.responses import JSONResponse`):

```python
_EXEMPT = ("/api/auth/login", "/api/health")


class AuthMiddleware(BaseHTTPMiddleware):
    """校验 /api/* 的签名 cookie。豁免 login/health/OPTIONS。失败 401。

    只守 /api/*(前端页面与静态资源由 Next.js 自己服务, 401 由前端跳登录)。
    """

    async def dispatch(self, request: Request, call_next):
        # 未配 APP_SECRET → 鉴权关闭(本地 dev 单机无需登录, 不破坏 make dev)。
        # 公网部署必须设 APP_SECRET/APP_PASSCODES, 鉴权才生效。
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
```

`apps/api/main.py` 接入。**关键顺序**:Starlette 中后 `add_middleware` 的在**外层**;要让 CORS 在最外层(能处理预检 + 给 401 也带上 CORS 头,dev 跨域才读得到),**AuthMiddleware 必须加在现有 CORS 那行之前**:
```python
from apps.api.auth import AuthMiddleware, router as auth_router  # 顶部 import 区

# 在现有 `app.add_middleware(CORSMiddleware, ...)`(:55)那行【之前】插入:
app.add_middleware(AuthMiddleware)   # 先加 → 在内层; CORS 后加 → 最外层

# include_router 区(任意位置):
app.include_router(auth_router)
```
(实现时:把 `app.add_middleware(AuthMiddleware)` 放到现有 CORS `add_middleware` 调用上方一行。)

- [ ] **Step 4: 运行确认通过** — `pytest tests/unit/api/test_auth_middleware.py -v` + `python -c "import os; os.environ['APP_SECRET']='s'; os.environ['APP_PASSCODES']='pw'; from apps.api.main import app; print('main import ok')"`

- [ ] **Step 5: 提交**

```bash
git add apps/api/auth.py apps/api/main.py tests/unit/api/test_auth_middleware.py
git commit -m "feat: AuthMiddleware 守 /api/* + 接入 main (豁免 login/health/OPTIONS)"
```

---

## Task 4: refill 白名单 + 去重

**Files:** Modify `apps/api/routes/symbols.py`;Test `tests/unit/api/test_refill_guard.py`

`_publish_refill_request` 改为:发布前判 `symbol ∈ core_symbols(market) ∪ watchlist.dynamic_universe()`(白名单)+ `state:inflight:refill:{market}:{sym}:{iv}` SET NX EX 60(去重)。两道都过才 xadd。两处调用(bars / bars_history)传入 watchlist 服务。

- [ ] **Step 1: 写失败测试**

```python
# tests/unit/api/test_refill_guard.py
import pytest
from unittest.mock import AsyncMock, MagicMock
from apps.api.routes.symbols import _publish_refill_request


def _redis():
    r = MagicMock(); r._r = MagicMock()
    r._r.xadd = AsyncMock()
    r._r.set = AsyncMock(return_value=True)   # 默认: NX 成功(未在途)
    return r


@pytest.mark.asyncio
async def test_core_symbol_allowed():
    redis = _redis()
    wl = MagicMock(); wl.dynamic_universe = AsyncMock(return_value=[])
    await _publish_refill_request(redis, "AAPL", "5m", 365, watchlist=wl)  # AAPL ∈ CORE.us
    redis._r.xadd.assert_awaited_once()


@pytest.mark.asyncio
async def test_watchlist_symbol_allowed():
    redis = _redis()
    wl = MagicMock(); wl.dynamic_universe = AsyncMock(return_value=["ZZZZ.SH"])
    await _publish_refill_request(redis, "ZZZZ.SH", "5m", 365, watchlist=wl)
    redis._r.xadd.assert_awaited_once()


@pytest.mark.asyncio
async def test_cold_symbol_blocked():
    redis = _redis()
    wl = MagicMock(); wl.dynamic_universe = AsyncMock(return_value=[])
    await _publish_refill_request(redis, "GARBAGE123", "5m", 365, watchlist=wl)  # 不在白名单
    redis._r.xadd.assert_not_awaited()


@pytest.mark.asyncio
async def test_dedup_inflight_blocked():
    redis = _redis()
    redis._r.set = AsyncMock(return_value=None)   # NX 失败(已在途)
    wl = MagicMock(); wl.dynamic_universe = AsyncMock(return_value=[])
    await _publish_refill_request(redis, "AAPL", "5m", 365, watchlist=wl)
    redis._r.xadd.assert_not_awaited()
```

- [ ] **Step 2: 运行确认失败** — `pytest tests/unit/api/test_refill_guard.py -v`(TypeError: 不接受 watchlist / 或未拦截)

- [ ] **Step 3: 实现** — `apps/api/routes/symbols.py`:
顶部加 `from core.domain.core_symbols import core_symbols`。改 `_publish_refill_request` 签名加 `watchlist` 参 + 两道闸:

```python
async def _publish_refill_request(redis_cache, symbol: str, interval: str, days: int,
                                  *, watchlist=None) -> None:
    """发 bus:bars.refill_request。白名单(CORE∪watchlist)+ inflight 去重。"""
    import json
    from core.cache import keys as ck
    market = infer_market(symbol) or "unknown"
    # 白名单: 只允许核心 + watchlist 标的触发 refill(防公网刷爆/给 B-sina 减压)
    allowed = set(core_symbols(market))
    if watchlist is not None:
        try:
            allowed |= set(await watchlist.dynamic_universe())
        except Exception:  # noqa: BLE001
            pass
    if symbol not in allowed:
        return
    # 去重: 同 (market,symbol,interval) 60s 内只发一次
    try:
        ok = await redis_cache._r.set(  # noqa: SLF001
            ck.state_inflight(f"refill:{market}:{symbol}:{interval}"),
            b"1", nx=True, ex=60)
        if not ok:
            return
    except Exception:  # noqa: BLE001
        pass
    payload = {"market": market, "symbol": symbol, "interval": interval, "days": days}
    await redis_cache._r.xadd(  # noqa: SLF001
        ck.BUS_BARS_REFILL_REQUEST, {"data": json.dumps(payload)},
        maxlen=100, approximate=True)
```

两处调用处(bars `:333`、bars_history `:423` 附近)给路由加 `watchlist=Depends(get_watchlist_service)` 参数,并把调用改为 `await _publish_refill_request(redis_cache, symbol, interval, days, watchlist=watchlist)`。`get_watchlist_service` 从 `apps.api.deps` import(确认已有该工厂)。

- [ ] **Step 4: 运行确认通过** — `pytest tests/unit/api/test_refill_guard.py -v` + `python -c "import os; os.environ['APP_SECRET']='s'; os.environ['APP_PASSCODES']='pw'; from apps.api.main import app; print('ok')"`

- [ ] **Step 5: 提交**

```bash
git add apps/api/routes/symbols.py tests/unit/api/test_refill_guard.py
git commit -m "feat: refill 白名单(CORE∪watchlist)+ inflight 去重 (修 W2, 给 B-sina 减压)"
```

---

## Task 5: 前端登录页 + 401 跳转

**Files:** Create `apps/web/app/login/page.tsx`、`apps/web/lib/api_fetch.ts`;验证 `tsc --noEmit`

- [ ] **Step 1: api_fetch 包装(401 → 跳 /login)**

`apps/web/lib/api_fetch.ts`:
```typescript
// 统一 fetch: credentials 带 cookie; 401 → 跳登录页。
export async function apiFetch(input: string, init?: RequestInit): Promise<Response> {
  const res = await fetch(input, { ...init, credentials: 'include' })
  if (res.status === 401 && typeof window !== 'undefined'
      && !window.location.pathname.startsWith('/login')) {
    window.location.href = '/login'
  }
  return res
}
```

- [ ] **Step 2: 登录页**

`apps/web/app/login/page.tsx`:
```tsx
'use client'
import { useState } from 'react'

export default function LoginPage() {
  const [pw, setPw] = useState('')
  const [err, setErr] = useState('')
  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setErr('')
    const r = await fetch('/api/auth/login', {
      method: 'POST', credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ passcode: pw }),
    })
    if (r.ok) window.location.href = '/'
    else setErr('口令错误')
  }
  return (
    <main className="min-h-screen flex items-center justify-center bg-neutral-950">
      <form onSubmit={submit} className="space-y-3 p-6 rounded-lg border border-neutral-800 bg-neutral-900">
        <div className="text-neutral-200 text-sm">MarketPulse · 输入口令</div>
        <input type="password" value={pw} onChange={(e) => setPw(e.target.value)}
          className="w-64 px-3 py-2 rounded bg-neutral-800 text-neutral-100 text-sm" placeholder="口令" />
        {err && <div className="text-red-400 text-xs">{err}</div>}
        <button className="w-full px-3 py-2 rounded bg-neutral-700 text-white text-sm">进入</button>
      </form>
    </main>
  )
}
```

(注:现有页面的数据请求若已用 SWR 全局 fetcher,可把 fetcher 换成 `apiFetch` 让 401 统一跳转;本 task 先提供 `apiFetch` + 登录页,接入全局 fetcher 在收尾步做一次替换。)

- [ ] **Step 3: 验证** — `cd apps/web && npx tsc --noEmit && cd ../..`(无类型错误)

- [ ] **Step 4: 提交**

```bash
git add apps/web/app/login/page.tsx apps/web/lib/api_fetch.ts
git commit -m "feat: 前端登录页 + apiFetch(401→跳登录)"
```

---

## Task 6: nginx 样例 + 生产环境变量说明

**Files:** Create `deploy/nginx.conf`、`deploy/README.md`

- [ ] **Step 1: 写 nginx 样例**

`deploy/nginx.conf`(关键段,占位 `server_name`/证书路径由部署填):
```nginx
limit_req_zone $binary_remote_addr zone=api:10m rate=20r/s;

upstream mp_api { server 127.0.0.1:8787; keepalive 32; }   # 多 worker 时 uvicorn --workers 自己管

server {
  listen 443 ssl http2;                    # HTTP/2 解决浏览器 6 连接上限(W10)
  server_name your.domain;
  ssl_certificate     /etc/letsencrypt/.../fullchain.pem;
  ssl_certificate_key /etc/letsencrypt/.../privkey.pem;

  # 前端 (Next.js next start, 默认 3000)
  location / { proxy_pass http://127.0.0.1:3000; proxy_set_header Host $host; }

  # SSE: 关缓冲, 长超时, h1.1 到 upstream
  location /api/sse/ {
    proxy_pass http://mp_api;
    proxy_http_version 1.1;
    proxy_set_header Connection '';
    proxy_buffering off;
    proxy_read_timeout 1h;
  }

  # 其余 API: 限流
  location /api/ {
    limit_req zone=api burst=40 nodelay;
    proxy_pass http://mp_api;
    proxy_set_header Host $host;
  }
}
```

- [ ] **Step 2: 写 deploy/README.md**(必配 env + 启动命令)

```markdown
# 生产部署(VPS, 邀请制)
必配环境变量:
- APP_SECRET=<随机长串>        # cookie 签名密钥
- APP_PASSCODES=code1,code2    # 允许的邀请口令(逗号分隔)
启动:
- api(多 worker): uvicorn apps.api.main:app --host 127.0.0.1 --port 8787 --workers <核数-2>
- web(生产): cd apps/web && npm run build && npm run start
- collector ×3: 同 dev(单实例)
- nginx: 用 deploy/nginx.conf(填证书/域名)
```

- [ ] **Step 3: 提交**

```bash
git add deploy/nginx.conf deploy/README.md
git commit -m "docs: nginx 样例(TLS/HTTP2/限流/SSE)+ 生产部署 env 说明"
```

---

## 收尾验证

- [ ] `python -c "from apps.api.main import app; print('OK')"`(**不带 env 也应 OK** —— 鉴权未配=关闭,不破坏 dev)
- [ ] `pytest tests/unit/api/ -q`(auth + refill 全绿)
- [ ] `cd apps/web && npx tsc --noEmit`
- [ ] 全套 `pytest -m "not integration" -q`(除既有 index_minute 2 例外全绿)
- [ ] **dev 冒烟(不带 env,鉴权关闭)**:重启 api,无 cookie 访问 `/api/markets/ashare/dashboard` 应 **200**(dev 不挡)→ 证明不破坏 make dev。
- [ ] **鉴权生效冒烟(带 env)**:`APP_SECRET=s APP_PASSCODES=pw uvicorn ...` 起一个 → 无 cookie 访问受护路由应 **401**;`/api/health` 200;POST `/api/auth/login` 正确口令 200 + Set-Cookie;带该 cookie 再访问受护路由 200。
- [ ] **注意**:本地 dev 重启 api **不必**带 env(鉴权自动关闭);仅公网生产需设 `APP_SECRET`/`APP_PASSCODES`。

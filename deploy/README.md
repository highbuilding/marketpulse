# 生产部署(VPS · 邀请制 · ~100 并发)

> 本地 dev(`make dev`)**不需要**这些——鉴权未配 `APP_SECRET` 时自动关闭。仅公网生产需要。

## 必配环境变量

| 变量 | 说明 |
|---|---|
| `APP_SECRET` | cookie 签名密钥,随机长串(如 `openssl rand -hex 32`)。**不配则鉴权关闭**,公网必配。 |
| `APP_PASSCODES` | 允许的邀请口令,逗号分隔(如 `code1,code2`)。配了 `APP_SECRET` 就必须配它。 |

## 启动

```bash
# api(多 worker;api 无 V8/无 ak_call, 多 worker 安全)
APP_SECRET=<随机串> APP_PASSCODES=<口令逗号分隔> \
  uvicorn apps.api.main:app --host 127.0.0.1 --port 8787 --workers <核数-2>

# 前端(生产构建)
cd apps/web && npm run build && npm run start    # 默认 3000

# collector ×3(同 dev, 单实例)
python -m apps.collector.ashare.main
python -m apps.collector.us.main
python -m apps.collector.crypto.main

# redis: docker-compose(同 dev)
# nginx: 用 deploy/nginx.conf(填 server_name + 证书路径)
```

## 鉴权流程

1. 用户访问 → 前端任意 `/api/*` 请求返回 401 → `apiFetch` 跳 `/login`。
2. `/login` 页输口令 → `POST /api/auth/login` → 校验 `APP_PASSCODES` → 下发 HttpOnly 签名 cookie(30 天)。
3. 之后请求(含 SSE,EventSource 同源自动带 cookie)通过鉴权。

## 注意

- 本地 dev 不设 `APP_SECRET` → 鉴权关闭,`make dev` 行为不变。
- HTTP/2 必须 TLS(浏览器要求);h2 在 nginx 终结,nginx↔uvicorn 仍 h1.1。
- collector 仍单实例(leader 锁),不随用户数扩。

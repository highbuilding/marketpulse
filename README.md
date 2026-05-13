# MarketPulse

本地运行的四市场行情监控分析平台。详见 `docs/superpowers/specs/2026-05-13-marketpulse-design.md`。

## 启动

```bash
make install     # 安装 Python 依赖
make web-install # 安装前端依赖
cp .env.example .env  # 按需填入 API key
make dev         # 启动后端(8787)与前端(3000)
```

打开 http://localhost:3000/dashboard。

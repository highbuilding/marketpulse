# 日志持久化设计

**日期**: 2026-05-20
**版本**: 1.0

## 0. 背景

API 进程偶尔崩溃(雷区 1 mini_racer SIGABRT 等),`/tmp/api.log` 是 nohup `>` 覆盖模式,重启 wipe 历史 → 崩溃信息丢失,无法事后排查。

## 1. 目标

- 关键日志持久化到项目内 `data/logs/`,跨重启不丢
- WARNING 及以上单独存档,便于 grep 排查
- C 层崩溃(SIGABRT/SIGSEGV)的线程栈不丢失(stdout buffer 来不及 flush 时仍能落盘)
- 文件 rotation 防无限增长
- structlog 现有 KV 风格保持不变

## 2. 文件结构

```
data/logs/
├── api.log           # 全部日志, RotatingFileHandler(10MB × 10 backup)
├── api-errors.log    # WARNING+, RotatingFileHandler(5MB × 10 backup)
└── fault.log         # faulthandler 写入 SIGABRT/SIGSEGV 时的线程栈, append never rotate
```

`data/logs/` 在 `.gitignore`(已有 `logs/` 规则覆盖)。

## 3. 实现

### 3.1 新文件 `core/integrations/logging_setup.py`

- 配置 stdlib root logger + 两个 RotatingFileHandler + 一个 StreamHandler(stdout)
- structlog 透传到 stdlib(已是项目惯例)
- faulthandler.enable + register SIGABRT

### 3.2 改 `apps/api/main.py`

启动期最早(`load_dotenv()` 之后,其他 import 之前)调 `setup_logging()`。

### 3.3 nohup 命令

我后续重启 API 时用 `>>` append 模式而不是 `>` 覆盖,但项目内 `data/logs/api.log` 才是事实源。Makefile 的 `dev` 直接走前台 stdout(那是开发模式),不动。

## 4. 不做的事

- 不动 uvicorn access log 分流(优先级低)
- 不接 Loki / ELK(本地工具,过度设计)
- 不引入新依赖(stdlib + faulthandler 都是 builtin)

## 5. 验证

- 启动后 `data/logs/api.log` 立即出现 + 写入 startup 事件
- `kill -ABRT $PID` 模拟崩溃 → `data/logs/fault.log` 出现栈帧
- 重启 API → 历史 `api.log` 内容仍在(append 模式)

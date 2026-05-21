# 美股 Alpaca 前复权修复

**日期**: 2026-05-21
**版本**: 1.0
**作者**: zhonghuai + Claude
**状态**: 待实施

## 0. 背景

实测 NVDA 2024-06-10 10:1 split 在 K 线上出现价格跳水(1208 → 120),DB 数据是未复权。Alpaca SDK 默认 `adjustment='raw'`(未复权),需显式传 `adjustment='all'` 拿前复权。

## 1. 目标

- `_fetch_history_alpaca` + `_fetch_intraday_alpaca` 都加 `adjustment='all'`
- 清除 DuckDB 中所有美股未复权数据(1d + intraday)
- 不预热,首次访问按需触发拉取

## 2. 非目标

- 不改 yfinance fallback(它默认 `auto_adjust=False` 也是未复权,但本期 yfinance 几乎不会触发;如未来 Alpaca 不可用再升)
- 不引入 `adjustment` 配置开关(YAGNI;前复权是业界默认,用户没需求看 raw)
- 不预热,无 lifespan 改动

## 3. 实施面

- `core/adapters/us.py`:`StockBarsRequest(..., adjustment='all')` 两处
- 一次性 SQL:`DELETE FROM bars WHERE market='us'`
- 测试:新增 1 个 `adjustment='all'` 参数断言;现有 Alpaca 测试不变
- 文档:CLAUDE.md 雷区或活跃约束加一行"美股 1d/intraday 是前复权"

## 4. 验证

清后访问 `/api/symbols/NVDA/bars?interval=1d&days=2000` → 2024-06-10 前后 close 价格连续(无 10:1 跳变);分拆前历史价应是当前价 ÷ 10(约 ~$120 当时,而非 $1200)。

# UI 信息架构重构 + 功能占位 Demo 实施计划

> **For agentic workers:** 纯前端 UI demo,零后端改动,限定 `apps/web/`。本计划无单测(spec §6 明确:以 `tsc --noEmit` + 人工走查验证)。Steps 用 checkbox 跟踪。

**Goal:** 重构侧边栏导航为 8 项(方案 A 平铺),新增 CD 信号页 + 设置多 tab + 策略/AI助手/自动交易占位页,全部假数据可跳转。

**Architecture:** 现有真实页面(`/` `/market` `/watchlist` `/symbol/[code]`)原样保留,仅在 `layout.tsx` 重新归位导航。新页面用写死假数据,集中放 `lib/demo_fixtures.ts`。样式复用 `globals.css` 现有 class(`.panel/.panel-header/.data-table/.sig-badge/.int-tab`)+ 内联 style(沿用现有页面风格,注意无 `.chip` class)。

**Tech Stack:** Next.js 14 App Router(client components)、TypeScript、现有 globals.css。

---

## 文件结构

**新增:**
- `apps/web/lib/demo_fixtures.ts` — 所有假数据集中源(信号事件、策略、AI对话、持仓订单、收件人、周期配置),便于后续真做时删除
- `apps/web/app/signals/page.tsx` — CD 信号单列时间流(假数据)
- `apps/web/app/strategy/page.tsx` — 策略卡片网格
- `apps/web/app/strategy/[id]/page.tsx` — 回测报告(假曲线+指标)
- `apps/web/app/assistant/page.tsx` — 聊天式 AI 助手
- `apps/web/app/trading/page.tsx` — 持仓/订单/风控
- `apps/web/app/settings/page.tsx` — 重定向到 `/settings/notifications`
- `apps/web/app/settings/layout.tsx` — 设置页二级 tab 外壳
- `apps/web/app/settings/notifications/page.tsx` — 假收件人 + 按标的周期
- `apps/web/app/settings/preferences/page.tsx` — 占位偏好
- `apps/web/app/settings/about/page.tsx` — 占位关于

**修改:**
- `apps/web/app/layout.tsx` — `NAV_ITEMS` 加 4 项 + activeNav 规则 + 设置链接改指 `/settings`
- `apps/web/app/notifications/page.tsx` — 改为重定向到 `/signals`

---

## Task 1: 导航重构(layout.tsx)

**Files:** Modify `apps/web/app/layout.tsx`

- [ ] **Step 1:** 改 `NAV_ITEMS` 为 8 项,改 activeNav 规则,设置链接改指 `/settings`
- [ ] **Step 2:** `npx tsc --noEmit` 通过
- [ ] **Step 3:** commit

## Task 2: 假数据源(demo_fixtures.ts)

**Files:** Create `apps/web/lib/demo_fixtures.ts`

- [ ] **Step 1:** 写所有假数据 + 类型
- [ ] **Step 2:** `tsc --noEmit` 通过
- [ ] **Step 3:** commit

## Task 3: CD 信号页(/signals)

**Files:** Create `apps/web/app/signals/page.tsx`

- [ ] **Step 1:** 筛选条 + 单列时间流,从 fixtures 读假信号,前端过滤,行可点跳 `/symbol/[code]`
- [ ] **Step 2:** `tsc --noEmit` 通过 + 浏览器走查
- [ ] **Step 3:** commit

## Task 4: 设置页多 tab(/settings/*)

**Files:** Create `settings/page.tsx` `settings/layout.tsx` `settings/notifications/page.tsx` `settings/preferences/page.tsx` `settings/about/page.tsx`

- [ ] **Step 1:** 重定向 + tab 外壳 + 三个子页(通知页假收件人/周期,本地 state)
- [ ] **Step 2:** `tsc --noEmit` + 走查
- [ ] **Step 3:** commit

## Task 5: 占位页(策略/AI/交易)

**Files:** Create `strategy/page.tsx` `strategy/[id]/page.tsx` `assistant/page.tsx` `trading/page.tsx`

- [ ] **Step 1:** 三个占位页 + 回测详情,顶部统一"🧪 功能预览"提示条,假数据
- [ ] **Step 2:** `tsc --noEmit` + 走查
- [ ] **Step 3:** commit

## Task 6: 旧路由重定向 + 全量验证

**Files:** Modify `apps/web/app/notifications/page.tsx`

- [ ] **Step 1:** `/notifications` 改为 `redirect('/signals')`
- [ ] **Step 2:** `tsc --noEmit` 全过 + 8 导航项逐个点击无 404/无 console error + 现有真实页面不回归
- [ ] **Step 3:** commit

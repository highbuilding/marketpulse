import assert from 'node:assert/strict'
import { test } from 'node:test'

import {
  inferMarket, marketTz, tradingDateKey, todayKey, tzOffsetSeconds,
} from '../../../apps/web/lib/markets'

test('inferMarket ashare suffix', () => {
  assert.equal(inferMarket('600519.SH'), 'ashare')
  assert.equal(inferMarket('000001.SZ'), 'ashare')
  assert.equal(inferMarket('920001.BJ'), 'ashare')
})

test('inferMarket hk', () => {
  assert.equal(inferMarket('9988.HK'), 'hk')
})

test('inferMarket crypto', () => {
  assert.equal(inferMarket('BTC/USDT'), 'crypto')
})

test('inferMarket us default + class share', () => {
  assert.equal(inferMarket('AAPL'), 'us')
  assert.equal(inferMarket('BRK.B'), 'us')
  assert.equal(inferMarket('^GSPC'), 'us')
})

test('marketTz mapping', () => {
  assert.equal(marketTz('ashare'), 'Asia/Shanghai')
  assert.equal(marketTz('us'), 'America/New_York')
})

test('tradingDateKey US: ET 自然日切分', () => {
  // 2026-05-18 04:00 UTC = 2026-05-18 00:00 EDT
  assert.equal(tradingDateKey('2026-05-18T04:00:00Z', 'us'), '2026-05-18')
  // 2026-05-18 03:30 UTC = 2026-05-17 23:30 EDT, 仍是 5/17
  assert.equal(tradingDateKey('2026-05-18T03:30:00Z', 'us'), '2026-05-17')
})

test('tradingDateKey ashare: BJT 切分', () => {
  // 2026-05-17 16:00 UTC = BJT 2026-05-18 00:00
  assert.equal(tradingDateKey('2026-05-17T16:00:00Z', 'ashare'), '2026-05-18')
})

test('tzOffsetSeconds US 夏冬令时', () => {
  // 2026-06-15 是 EDT(UTC-4 = -14400 秒)
  const summer = tzOffsetSeconds('us', '2026-06-15T12:00:00Z')
  assert.equal(summer, -4 * 3600)
  // 2026-01-15 是 EST(UTC-5 = -18000 秒)
  const winter = tzOffsetSeconds('us', '2026-01-15T12:00:00Z')
  assert.equal(winter, -5 * 3600)
})

test('tzOffsetSeconds ashare 固定 +8h', () => {
  assert.equal(tzOffsetSeconds('ashare', '2026-05-18T00:00:00Z'), 8 * 3600)
})

test('todayKey works for all markets', () => {
  // 不验证具体值(取决于当前时刻), 只验证不抛错且返回 ISO date 格式
  for (const m of ['ashare', 'hk', 'us', 'crypto'] as const) {
    assert.match(todayKey(m), /^\d{4}-\d{2}-\d{2}$/)
  }
})

// 纯前端 UI demo 的集中假数据源。
// 后续任一功能真做时,删除对应导出 + 改页面接真实 API 即可。
import type { MarketId } from './market-context'

// ── CD 信号事件流(/signals)──────────────────────────────
export type SignalDirection = 'buy' | 'sell'
export interface DemoSignal {
  id: string
  market: MarketId
  symbol: string        // 代码,跳详情页用
  name: string          // 中文名
  interval: string      // 1d / 4h / 1h / 30m / 15m
  direction: SignalDirection
  type: string          // 底背离 / 顶背离
  ts: string            // 显示用固定时间戳
}

export const DEMO_SIGNALS: DemoSignal[] = [
  { id: 's1',  market: 'ashare', symbol: '600519', name: '贵州茅台', interval: '1d',  direction: 'buy',  type: '底背离', ts: '06-02 15:00' },
  { id: 's2',  market: 'us',     symbol: 'NVDA',   name: '英伟达',   interval: '4h',  direction: 'sell', type: '顶背离', ts: '06-02 03:00' },
  { id: 's3',  market: 'crypto', symbol: 'BTCUSDT',name: '比特币',   interval: '1h',  direction: 'buy',  type: '底背离', ts: '06-01 22:00' },
  { id: 's4',  market: 'ashare', symbol: '300750', name: '宁德时代', interval: '1d',  direction: 'buy',  type: '底背离', ts: '06-01 15:00' },
  { id: 's5',  market: 'us',     symbol: 'AAPL',   name: '苹果',     interval: '1d',  direction: 'sell', type: '顶背离', ts: '06-01 04:00' },
  { id: 's6',  market: 'crypto', symbol: 'ETHUSDT',name: '以太坊',   interval: '4h',  direction: 'buy',  type: '底背离', ts: '06-01 00:00' },
  { id: 's7',  market: 'ashare', symbol: '000858', name: '五粮液',   interval: '30m', direction: 'sell', type: '顶背离', ts: '05-30 14:00' },
  { id: 's8',  market: 'us',     symbol: 'TSLA',   name: '特斯拉',   interval: '1h',  direction: 'buy',  type: '底背离', ts: '05-30 22:30' },
  { id: 's9',  market: 'ashare', symbol: '601318', name: '中国平安', interval: '1d',  direction: 'buy',  type: '底背离', ts: '05-30 15:00' },
  { id: 's10', market: 'crypto', symbol: 'SOLUSDT',name: 'Solana',   interval: '15m', direction: 'sell', type: '顶背离', ts: '05-29 20:15' },
  { id: 's11', market: 'us',     symbol: 'MSFT',   name: '微软',     interval: '4h',  direction: 'buy',  type: '底背离', ts: '05-29 12:00' },
  { id: 's12', market: 'ashare', symbol: '002594', name: '比亚迪',   interval: '1d',  direction: 'sell', type: '顶背离', ts: '05-29 15:00' },
  { id: 's13', market: 'crypto', symbol: 'BTCUSDT',name: '比特币',   interval: '4h',  direction: 'sell', type: '顶背离', ts: '05-28 16:00' },
  { id: 's14', market: 'ashare', symbol: '600036', name: '招商银行', interval: '60m', direction: 'buy',  type: '底背离', ts: '05-28 11:00' },
  { id: 's15', market: 'us',     symbol: 'GOOGL',  name: '谷歌',     interval: '1d',  direction: 'buy',  type: '底背离', ts: '05-28 04:00' },
  { id: 's16', market: 'ashare', symbol: '300059', name: '东方财富', interval: '30m', direction: 'buy',  type: '底背离', ts: '05-27 13:30' },
  { id: 's17', market: 'crypto', symbol: 'ETHUSDT',name: '以太坊',   interval: '1h',  direction: 'sell', type: '顶背离', ts: '05-27 09:00' },
  { id: 's18', market: 'us',     symbol: 'AMD',    name: 'AMD',      interval: '1d',  direction: 'sell', type: '顶背离', ts: '05-27 04:00' },
]

// ── 策略回测(/strategy)──────────────────────────────────
export interface DemoStrategy {
  id: string
  name: string
  desc: string
  annualReturn: number   // 年化 %
  maxDrawdown: number    // 最大回撤 %
  status: 'running' | 'paused' | 'draft'
}

export const DEMO_STRATEGIES: DemoStrategy[] = [
  { id: 'cd-reversal', name: 'CD 背离反转',   desc: '底背离买入 / 顶背离卖出,持有至反向信号', annualReturn: 23.4, maxDrawdown: -12.1, status: 'running' },
  { id: 'ma-cross',    name: '双均线金叉',     desc: 'MA5 上穿 MA20 买入,下穿卖出',          annualReturn: 15.8, maxDrawdown: -18.6, status: 'running' },
  { id: 'rsi-mean',    name: 'RSI 均值回归',   desc: 'RSI<30 买入,RSI>70 卖出',              annualReturn: 9.2,  maxDrawdown: -8.4,  status: 'paused' },
  { id: 'vol-break',   name: '放量突破',       desc: '量能突破 20 日均量 2 倍且价创新高',     annualReturn: 31.7, maxDrawdown: -24.3, status: 'draft' },
  { id: 'grid-crypto', name: '网格(Crypto)',  desc: '区间内等比网格,适合震荡行情',          annualReturn: 18.5, maxDrawdown: -15.0, status: 'paused' },
  { id: 'north-flow',  name: '北向资金跟随',   desc: '北向净流入前 20 标的轮动',              annualReturn: 12.6, maxDrawdown: -10.9, status: 'draft' },
]

// 回测报告假资金曲线(归一化净值点,画死折线)
export const DEMO_EQUITY_CURVE: number[] = [
  100, 102, 101, 105, 108, 106, 112, 118, 115, 121, 119, 127,
  133, 130, 138, 142, 140, 148, 145, 152, 159, 156, 164, 171,
]

export interface DemoMetric { label: string; value: string }
export const DEMO_METRICS: DemoMetric[] = [
  { label: '总收益',   value: '+71.0%' },
  { label: '年化收益', value: '+23.4%' },
  { label: '夏普比率', value: '1.87' },
  { label: '最大回撤', value: '-12.1%' },
  { label: '胜率',     value: '58.3%' },
  { label: '交易次数', value: '142' },
]

export interface DemoTrade { date: string; dir: SignalDirection; symbol: string; price: string; pnl: string }
export const DEMO_TRADES: DemoTrade[] = [
  { date: '2026-05-28', dir: 'buy',  symbol: '600519', price: '1620.0', pnl: '—' },
  { date: '2026-05-22', dir: 'sell', symbol: '600519', price: '1701.5', pnl: '+5.0%' },
  { date: '2026-05-15', dir: 'buy',  symbol: '300750', price: '188.20', pnl: '—' },
  { date: '2026-05-08', dir: 'sell', symbol: '300750', price: '205.60', pnl: '+9.2%' },
  { date: '2026-04-30', dir: 'buy',  symbol: '601318', price: '48.30',  pnl: '—' },
]

// ── AI 助手(/assistant)─────────────────────────────────
export interface DemoChatMsg { role: 'ai' | 'user'; text: string; ts: string }
export const DEMO_CHAT: DemoChatMsg[] = [
  { role: 'ai',   text: '早上好。今日 A 股开盘放量上涨,北向净流入 48.2 亿,科技与新能源板块领涨。你的自选里宁德时代触发 1d 底背离,值得关注。', ts: '09:35' },
  { role: 'user', text: '茅台现在能买吗?',                                                                                              ts: '09:41' },
  { role: 'ai',   text: '贵州茅台昨日刚出 1d 底背离信号,当前价 1620 附近,处于近 3 个月支撑带。短线偏多,但量能尚未明显放大,建议分批而非追高。', ts: '09:41' },
  { role: 'user', text: '帮我盯一下 NVDA 的 4h 信号',                                                                                    ts: '09:43' },
  { role: 'ai',   text: '好的。NVDA 当前 4h 刚出顶背离,我已加入重点监控。一旦出现反向(底背离)或跌破 4h 关键均线,会第一时间播报给你。',           ts: '09:43' },
]

// ── 自动交易(/trading)──────────────────────────────────
export interface DemoPosition { symbol: string; name: string; qty: number; cost: string; last: string; pnl: string; up: boolean }
export const DEMO_POSITIONS: DemoPosition[] = [
  { symbol: '600519', name: '贵州茅台', qty: 100,  cost: '1620.0', last: '1701.5', pnl: '+5.0%',  up: true },
  { symbol: '300750', name: '宁德时代', qty: 500,  cost: '188.20', last: '182.40', pnl: '-3.1%',  up: false },
  { symbol: 'BTCUSDT',name: '比特币',   qty: 0.5,  cost: '61200',  last: '64800',  pnl: '+5.9%',  up: true },
]

export interface DemoOrder { ts: string; dir: SignalDirection; symbol: string; price: string; status: string }
export const DEMO_ORDERS: DemoOrder[] = [
  { ts: '06-02 09:35', dir: 'buy',  symbol: '600519', price: '1620.0', status: '已成交' },
  { ts: '06-02 10:12', dir: 'sell', symbol: '300750', price: '205.60', status: '已成交' },
  { ts: '06-02 14:01', dir: 'buy',  symbol: 'BTCUSDT',price: '61200',  status: '部分成交' },
  { ts: '06-02 14:48', dir: 'buy',  symbol: 'ETHUSDT',price: '3180',   status: '已撤单' },
]

export interface DemoRiskToggle { label: string; desc: string; on: boolean }
export const DEMO_RISK_TOGGLES: DemoRiskToggle[] = [
  { label: '启用自动交易',   desc: '总开关。关闭后所有策略仅播报不下单',     on: false },
  { label: '单笔金额上限',   desc: '每笔订单不超过账户净值的 5%',           on: true },
  { label: '最大持仓数',     desc: '同时持有标的不超过 10 个',              on: true },
  { label: '止损保护',       desc: '单标的浮亏超 8% 自动平仓',              on: true },
]

// ── 设置 · 信号通知(/settings/notifications)─────────────
export interface DemoRecipient { id: number; address: string; enabled: boolean }
export const DEMO_RECIPIENTS: DemoRecipient[] = [
  { id: 1, address: 'zhonghuai@example.com', enabled: true },
  { id: 2, address: 'alerts@example.com',    enabled: true },
  { id: 3, address: 'backup@example.com',    enabled: false },
]

export const SIGNAL_INTERVALS = ['15m', '30m', '60m', '4h', '1d'] as const
export type SignalInterval = typeof SIGNAL_INTERVALS[number]

export interface DemoSymbolConfig { symbol: string; name: string; intervals: SignalInterval[] }
export const DEMO_SYMBOL_CONFIGS: DemoSymbolConfig[] = [
  { symbol: '600519', name: '贵州茅台', intervals: ['1d'] },
  { symbol: '300750', name: '宁德时代', intervals: ['1d', '4h'] },
  { symbol: 'BTCUSDT',name: '比特币',   intervals: ['1d', '4h', '60m'] },
  { symbol: 'NVDA',   name: '英伟达',   intervals: ['1d', '4h'] },
]

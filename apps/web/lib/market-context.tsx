'use client'

import { createContext, useContext, useState, useEffect, type ReactNode } from 'react'

export type MarketId = 'ashare' | 'us' | 'hk' | 'crypto'

const MARKET_LABELS: Record<MarketId, { flag: string; name: string; sub: string }> = {
  ashare: { flag: '🇨🇳', name: 'A 股', sub: '沪深京' },
  us:     { flag: '🇺🇸', name: '美股', sub: 'NYSE/NASDAQ' },
  hk:     { flag: '🇭🇰', name: '港股', sub: '联交所' },
  crypto: { flag: '🪙', name: 'Crypto', sub: 'BTC 大盘' },
}

const VALID: MarketId[] = ['ashare', 'us', 'hk', 'crypto']
const STORAGE_KEY = 'mp:market'

const MarketContext = createContext<{
  market: MarketId
  setMarket: (m: MarketId) => void
  marketLabel: { flag: string; name: string; sub: string }
}>({
  market: 'ashare',
  setMarket: () => {},
  marketLabel: MARKET_LABELS.ashare,
})

export function MarketProvider({ children }: { children: ReactNode }) {
  const [market, setMarketState] = useState<MarketId>('ashare')

  // 首屏从 localStorage 恢复上次选的市场(SSR 安全: 初值固定, 挂载后再读)
  useEffect(() => {
    const saved = typeof window !== 'undefined' ? window.localStorage.getItem(STORAGE_KEY) : null
    if (saved && VALID.includes(saved as MarketId)) setMarketState(saved as MarketId)
  }, [])

  const setMarket = (m: MarketId) => {
    setMarketState(m)
    if (typeof window !== 'undefined') window.localStorage.setItem(STORAGE_KEY, m)
  }

  return (
    <MarketContext.Provider value={{ market, setMarket, marketLabel: MARKET_LABELS[market] }}>
      {children}
    </MarketContext.Provider>
  )
}

export function useMarket() {
  return useContext(MarketContext)
}

export { MARKET_LABELS }

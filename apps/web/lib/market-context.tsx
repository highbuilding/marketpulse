'use client'

import { createContext, useContext, useState, type ReactNode } from 'react'

export type MarketId = 'ashare' | 'us' | 'hk' | 'crypto'

const MARKET_LABELS: Record<MarketId, { flag: string; name: string; sub: string }> = {
  ashare: { flag: '🇨🇳', name: 'A 股', sub: '沪深京' },
  us:     { flag: '🇺🇸', name: '美股', sub: 'NYSE/NASDAQ' },
  hk:     { flag: '🇭🇰', name: '港股', sub: '联交所' },
  crypto: { flag: '🪙', name: 'Crypto', sub: 'BTC 大盘' },
}

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
  const [market, setMarket] = useState<MarketId>('ashare')
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

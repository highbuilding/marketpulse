'use client'

import { useRouter } from 'next/navigation'
import useSWR from 'swr'

import { fetchSectorConstituents } from '@/lib/sector_api'

export default function SectorPage({ params }: { params: { name: string } }) {
  const name = decodeURIComponent(params.name)
  const router = useRouter()
  const goBack = () => {
    if (typeof window !== 'undefined' && window.history.length > 1) router.back()
    else router.push('/market')
  }
  const { data, error, isLoading } = useSWR(
    `sector:${name}`, () => fetchSectorConstituents(name),
  )

  return (
    <main className="p-6 max-w-7xl mx-auto space-y-4">
      <header className="flex items-baseline justify-between">
        <h1 className="text-2xl font-bold">{name}</h1>
        <button onClick={goBack} className="text-xs text-neutral-400 hover:text-neutral-200">← 返回</button>
      </header>

      <section className="rounded-lg border border-neutral-800 bg-neutral-950 p-4">
        <h2 className="text-lg font-semibold mb-3">成分股</h2>
        {isLoading && <p className="text-sm text-neutral-500">加载中…</p>}
        {error && (
          <p className="text-sm text-yellow-400">板块数据尚未抓取。运行 scheduler 或调用 sector refresh。</p>
        )}
        {data && (
          <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-2">
            {data.symbols.map((s) => (
              <a key={s} href={`/symbol/${encodeURIComponent(s)}`}
                className="font-mono text-sm py-1 px-2 rounded bg-neutral-900 hover:bg-neutral-800 hover:text-blue-400">
                {s}
              </a>
            ))}
          </div>
        )}
      </section>
    </main>
  )
}

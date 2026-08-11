import { useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useVirtualizer } from '@tanstack/react-virtual'

import { api } from '../api/client'
import { Panel, Diamond } from '../components/Panel'
import { LANE_COLOR, LANE_LABEL, LANES, laneOf, type Lane } from '../lib/lanes'
import { useSelection } from '../stores/selection'
import { DAY_S } from '../clock/engine'

export function LeftRail({ runId, t }: { runId: string; t: number }) {
  const [tab, setTab] = useState<'feed' | 'people'>('feed')
  const [q, setQ] = useState('')
  const [off, setOff] = useState<Set<Lane>>(new Set())
  const select = useSelection((s) => s.select)

  const toggle = (l: Lane) =>
    setOff((s) => {
      const n = new Set(s)
      n.has(l) ? n.delete(l) : n.add(l)
      return n
    })

  return (
    <div className="absolute top-[76px] left-3 bottom-[130px] w-[268px] z-20 flex flex-col gap-3">
      <Panel className="p-2" plain>
        <input
          value={q}
          onChange={(e) => { setQ(e.target.value); if (e.target.value) setTab('people') }}
          placeholder="search people…"
          className="w-full bg-[var(--color-paper)] border border-[var(--color-line)]
                     px-2 py-1 text-[12px] outline-none
                     focus:border-[var(--color-haldi)]"
        />
        <div className="flex flex-wrap gap-1 mt-2">
          {(Object.keys(LANES) as Lane[]).map((l) => (
            <button
              key={l}
              onClick={() => toggle(l)}
              className={`flex items-center gap-1 text-[10px] px-1.5 py-[2px] border
                          transition-opacity cursor-pointer
                          ${off.has(l)
                            ? 'opacity-35 border-[var(--color-line)]'
                            : 'border-[var(--color-line-ink)]'}`}
            >
              <Diamond color={LANES[l]} size={6} />
              {LANE_LABEL[l]}
            </button>
          ))}
        </div>
      </Panel>

      <Panel className="flex-1 min-h-0 flex flex-col"
             title={
               <div className="flex gap-3">
                 {(['feed', 'people'] as const).map((k) => (
                   <button key={k} onClick={() => setTab(k)}
                           className={`survey text-[10px] cursor-pointer
                             ${tab === k ? 'text-[var(--color-ink)]' : 'opacity-45'}`}>
                     {k === 'feed' ? 'the day' : 'people'}
                   </button>
                 ))}
               </div>
             }>
        {tab === 'feed'
          ? <Feed runId={runId} t={t} off={off} />
          : <People runId={runId} q={q} onPick={(id) => select({ kind: 'person', id })} />}
      </Panel>
    </div>
  )
}

function Feed({ runId, t, off }: { runId: string; t: number; off: Set<Lane> }) {
  const day = Math.floor(t / DAY_S)
  const select = useSelection((s) => s.select)
  // Scoped to the day on screen. Asking for the run's last N events instead
  // hands a client on day 1 a page of day 29, which it then filters to nothing.
  const q = useQuery({
    queryKey: ['ticker', runId, day],
    queryFn: () => api.ticker(runId, 0, 2000, day),
  })
  const box = useRef<HTMLDivElement>(null)

  // The feed follows the playhead: what you are looking at on the map is what
  // is at the bottom of the list, so scrubbing scrolls the day rather than
  // leaving you reading yesterday.
  const items = (q.data?.items ?? [])
    .filter((e) => !off.has(laneOf(e.type)))
    .filter((e) => e.t <= t + 60)
  const shown = items.slice(-120)

  useEffect(() => {
    const el = box.current
    if (el) el.scrollTop = el.scrollHeight
  }, [shown.length])

  if (!q.data) return <div className="p-3 text-[12px]">reading the log…</div>

  // How many of the day's events are still ahead of the playhead. Saying "36
  // later today" instead of a bare "nothing yet" is the difference between a
  // panel that looks broken and one that is telling you where you are: most of
  // a day's notable events happen after 10am, and an empty morning is normal.
  const ahead = (q.data.items ?? []).filter((e) => !off.has(laneOf(e.type))).length - items.length

  return (
    <div ref={box} className="flex-1 min-h-0 overflow-y-auto px-2.5 py-2 space-y-[3px]">
      {shown.length === 0 && (
        <div className="text-[11px] italic text-[var(--color-ink-faint)]">
          nothing yet on day {day + 1}
          {ahead > 0 && <> — {ahead} later today</>}
        </div>
      )}
      {shown.map((e) => (
        <div key={e.seq} className="flex gap-1.5 items-start text-[11.5px] leading-snug">
          <span className="tnum text-[9px] text-[var(--color-ink-faint)] pt-[3px] w-[52px] shrink-0"
                style={{ fontFamily: 'var(--font-mono)' }}>{e.hm}</span>
          <Diamond color={LANE_COLOR(e.type)} size={6} />
          <span className="flex-1">
            {e.text}
            {e.refs.person_ids.slice(0, 1).map((pid) => (
              <button key={pid} onClick={() => select({ kind: 'person', id: pid })}
                      className="ml-1 text-[9px] opacity-45 hover:opacity-100 cursor-pointer">
                ↗
              </button>
            ))}
          </span>
        </div>
      ))}
    </div>
  )
}

function People({ runId, q, onPick }: {
  runId: string; q: string; onPick: (id: string) => void
}) {
  const res = useQuery({
    queryKey: ['people', runId, q],
    queryFn: () => api.people(runId, q, 0, 500),
  })
  const parent = useRef<HTMLDivElement>(null)
  const rows = res.data?.items ?? []

  // 49,578 people do not go in a list; a virtualiser draws the ~20 you can see.
  const v = useVirtualizer({
    count: rows.length,
    getScrollElement: () => parent.current,
    estimateSize: () => 34,
    overscan: 8,
  })

  return (
    <div ref={parent} className="flex-1 min-h-0 overflow-y-auto">
      <div className="px-3 py-1.5 text-[10px] text-[var(--color-ink-faint)]">
        {res.data ? `${res.data.total.toLocaleString()} people${q ? ' match' : ''}` : '…'}
      </div>
      <div style={{ height: v.getTotalSize(), position: 'relative' }}>
        {v.getVirtualItems().map((vi) => {
          const p = rows[vi.index]
          return (
            <button
              key={p.id}
              onClick={() => onPick(p.id)}
              className="absolute left-0 w-full text-left px-3 py-1
                         hover:bg-[var(--color-haldi)]/15 cursor-pointer"
              style={{ top: vi.start, height: vi.size }}
            >
              <div className="text-[12px] leading-tight">{p.name}</div>
              <div className="text-[10px] text-[var(--color-ink-faint)] leading-tight">
                {p.age} · {p.occupation}
              </div>
            </button>
          )
        })}
      </div>
    </div>
  )
}

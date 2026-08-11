import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'

import { api } from '../api/client'
import type { Rumour } from '../api/types'
import { Panel, StampButton } from '../components/Panel'
import { useSelection } from '../stores/selection'

/**
 * The telephone game, audited.
 *
 * Every number here folds from the log: who heard it, from whom, at what
 * credence, how many mouths it passed through, and how the words changed on the
 * way. The drift ladder is the part worth staring at — the same claim at hop 0
 * and hop 4 is often not the same claim.
 */
export function RumourBoard({ runId, onClose }: {
  runId: string
  onClose: () => void
}) {
  const q = useQuery({ queryKey: ['rumors', runId], queryFn: () => api.rumors(runId) })
  const [openKey, setOpenKey] = useState<string | null>(null)
  const select = useSelection((s) => s.select)

  const rumours = q.data ?? []
  const open = rumours.find((r) => r.key === openKey) ?? rumours[0]

  return (
    <div className="absolute inset-0 z-40 grid place-items-center bg-[var(--color-ink)]/20"
         onClick={onClose}>
      <Panel className="w-[min(1100px,94vw)] h-[min(760px,88vh)] flex flex-col"
             title="what the city is saying"
             onClick={(e: React.MouseEvent) => e.stopPropagation()}>
        {!q.data && <div className="p-4 text-[12px]">folding the log…</div>}
        {q.data && !rumours.length && (
          <div className="p-4 text-[12px] text-[var(--color-ink-dim)]">
            nobody has heard anything worth repeating in this run.
          </div>
        )}

        {!!rumours.length && (
          <div className="flex-1 min-h-0 flex">
            <div className="w-[320px] border-r border-[var(--color-line)]
                            overflow-y-auto">
              {rumours.map((r) => (
                <button key={r.key} onClick={() => setOpenKey(r.key)}
                        className={`w-full text-left px-3 py-2 border-b
                          border-[var(--color-line)] cursor-pointer
                          ${r.key === open?.key
                            ? 'bg-[var(--color-haldi)]/20'
                            : 'hover:bg-[var(--color-haldi)]/8'}`}>
                  <div className="text-[12px] leading-snug">
                    “{r.variants[0]?.text ?? r.key}”
                  </div>
                  <div className="flex gap-2 items-center mt-1 text-[10px]
                                  text-[var(--color-ink-faint)]">
                    <Veracity v={r.veracity} />
                    <span className="tnum">{r.reach} heard</span>
                    <span className="tnum">{r.believers} believed</span>
                    {r.actions.length > 0 && (
                      <span className="tnum text-[var(--color-rumor)]">
                        {r.actions.length} acted
                      </span>
                    )}
                  </div>
                </button>
              ))}
            </div>

            {open && <Detail r={open} onPerson={(id) => {
              select({ kind: 'person', id }); onClose()
            }} />}
          </div>
        )}

        <div className="px-3 py-2 border-t border-[var(--color-line)] flex justify-end">
          <StampButton onClick={onClose}>close</StampButton>
        </div>
      </Panel>
    </div>
  )
}

function Detail({ r, onPerson }: { r: Rumour; onPerson: (id: string) => void }) {
  const peak = Math.max(1, ...r.by_day.map((d) => d.n))
  return (
    <div className="flex-1 min-w-0 overflow-y-auto p-4 space-y-4">
      <div>
        <div className="survey text-[9px] text-[var(--color-ink-faint)] mb-1">
          about
        </div>
        <div className="text-[14px]">{r.subject}</div>
        <div className="flex gap-3 mt-1.5 text-[11px] text-[var(--color-ink-dim)]">
          <span>started {r.first_hm}</span>
          <span>as {r.origin_type?.replace(/^\w+\./, '') ?? 'something'}</span>
          <Veracity v={r.veracity} />
        </div>
      </div>

      <div>
        <div className="survey text-[9px] text-[var(--color-ink-faint)] mb-1.5">
          how the words changed
        </div>
        <div className="space-y-1.5">
          {r.variants.map((v, i) => (
            <div key={i} className="flex gap-2 items-start"
                 style={{ paddingLeft: `${Math.min(v.hop, 6) * 14}px` }}>
              <span className="tnum text-[9px] text-[var(--color-rumor)] pt-[3px] w-[28px]"
                    style={{ fontFamily: 'var(--font-mono)' }}>
                hop {v.hop}
              </span>
              <div className="flex-1">
                <div className={`text-[12px] leading-snug
                                 ${i === 0 ? '' : 'text-[var(--color-ink-dim)]'}`}>
                  “{v.text}”
                </div>
                {!!v.ops.length && (
                  <div className="text-[9px] text-[var(--color-ink-faint)] mt-[1px]">
                    {v.ops.join(' · ')}
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>
      </div>

      <div>
        <div className="survey text-[9px] text-[var(--color-ink-faint)] mb-1.5">
          how fast it went round
        </div>
        <div className="flex items-end gap-[3px] h-[44px]">
          {r.by_day.map((d) => (
            <div key={d.day} className="flex-1 bg-[var(--color-rumor)] opacity-75"
                 style={{ height: `${Math.max(3, (d.n / peak) * 44)}px` }}
                 title={`day ${d.day + 1}: ${d.n} hearings`} />
          ))}
        </div>
        <div className="flex justify-between text-[9px] text-[var(--color-ink-faint)] mt-0.5">
          <span>day {(r.by_day[0]?.day ?? 0) + 1}</span>
          <span>day {(r.by_day.at(-1)?.day ?? 0) + 1}</span>
        </div>
      </div>

      {!!r.actions.length && (
        <div>
          <div className="survey text-[9px] text-[var(--color-ink-faint)] mb-1.5">
            who acted on it — {r.actions.length}
          </div>
          <div className="space-y-1">
            {r.actions.slice(0, 12).map((a, i) => (
              <div key={i} className="text-[12px] leading-snug">
                <button onClick={() => onPerson(a.person_id)}
                        className="underline decoration-dotted cursor-pointer">
                  {a.person}
                </button>
                {' '}{a.action.replace(/_/g, ' ')}{a.place ? ` · ${a.place}` : ''}
                <span className="text-[10px] text-[var(--color-ink-faint)]"> {a.hm}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      <div>
        <div className="survey text-[9px] text-[var(--color-ink-faint)] mb-1.5">
          the chain{r.spread_truncated && ' — first 400'}
        </div>
        <div className="space-y-[3px]">
          {r.spread.slice(0, 25).map((s, i) => (
            <div key={i} className="flex gap-2 items-baseline text-[11px]">
              <span className="tnum text-[9px] text-[var(--color-ink-faint)] w-[52px]"
                    style={{ fontFamily: 'var(--font-mono)' }}>{s.hm}</span>
              <button onClick={() => onPerson(s.person_id)}
                      className="underline decoration-dotted cursor-pointer">
                {s.person}
              </button>
              <span className="text-[var(--color-ink-faint)]">
                {s.source && s.source !== 'origin' && s.source !== 'witness'
                  ? `from ${s.source}` : s.channel === 'witness' ? 'saw it' : s.channel}
                {' · '}{Math.round((s.credence ?? 0) * 100)}%
                {s.chain.length > 1 && ` · via ${s.chain.length} mouths`}
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

function Veracity({ v }: { v: string }) {
  const label = { true: 'true', false: 'false' }[v] ?? v
  const colour = v === 'true' ? 'var(--color-rumor)'
    : v === 'false' ? 'var(--color-danger)' : 'var(--color-ink-faint)'
  return (
    <span className="text-[9px] uppercase tracking-[0.08em] px-1 border"
          style={{ color: colour, borderColor: colour }}>
      {label}
    </span>
  )
}

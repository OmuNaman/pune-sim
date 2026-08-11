import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'

import { api } from '../api/client'
import type { RunSummary } from '../api/types'
import { Panel, StampButton } from '../components/Panel'

/**
 * The save-tree.
 *
 * Every run is a node on a time axis and every branch forks from its parent at
 * the day it was made. What makes this more than a save-game list is that the
 * shared past is genuinely shared — the sim is deterministic, so replaying days
 * 0..N gives byte-identical results and every difference after the fork was
 * caused by the change. Select a node and its parent to see how much.
 */

const ROW_H = 46
const PAD_L = 128

export function Timelines({ current, onOpen, onClose }: {
  current: string
  onOpen: (id: string) => void
  onClose: () => void
}) {
  const runs = useQuery({ queryKey: ['runs'], queryFn: api.runs })
  const [pair, setPair] = useState<[string, string] | null>(null)
  const [showAll, setShowAll] = useState(false)

  // Trees first. A machine with twenty ad-hoc CLI run directories draws twenty
  // parallel lines that fork from nothing and relate to nothing, and buries the
  // one thing this screen is for — which world came from which.
  const rows = useMemo(() => {
    const all = runs.data?.runs ?? []
    if (showAll) return layout(all)
    const inTree = new Set<string>()
    for (const r of all) {
      if (r.parent_id) { inTree.add(r.id); inTree.add(r.parent_id) }
    }
    const kept = all.filter((r) => inTree.has(r.id) || r.id === current)
    return layout(kept.length > 1 ? kept : all)
  }, [runs.data, showAll, current])
  const hidden = (runs.data?.runs.length ?? 0) - rows.length
  const maxDay = Math.max(4, ...rows.map((r) => r.run.days_planned || r.run.days_done))

  return (
    <div className="absolute inset-0 z-40 grid place-items-center bg-[var(--color-ink)]/20"
         onClick={onClose}>
      <Panel className="w-[min(1180px,94vw)] max-h-[86vh] overflow-auto"
             title="timelines"
             onClick={(e: React.MouseEvent) => e.stopPropagation()}>
        <div className="p-4">
          {!rows.length && (
            <div className="text-[12px] text-[var(--color-ink-dim)]">
              no runs yet — make one with <b>+ new</b>
            </div>
          )}

          <svg width="100%" height={rows.length * ROW_H + 26}
               viewBox={`0 0 1100 ${rows.length * ROW_H + 26}`}>
            {/* day gridlines, so the fork days line up with something */}
            {Array.from({ length: Math.min(maxDay, 31) }, (_, i) => {
              const x = PAD_L + (i / maxDay) * (1100 - PAD_L - 24)
              return (
                <g key={i}>
                  <line x1={x} y1={16} x2={x} y2={rows.length * ROW_H + 8}
                        stroke="var(--color-line)" strokeWidth={i % 5 ? 0.5 : 1} />
                  {i % 5 === 0 && (
                    <text x={x} y={11} fontSize={8} fill="var(--color-ink-faint)"
                          textAnchor="middle" style={{ letterSpacing: '0.1em' }}>
                      {i + 1}
                    </text>
                  )}
                </g>
              )
            })}

            {rows.map((r, i) => {
              const y = 26 + i * ROW_H
              const x0 = PAD_L + ((r.startDay) / maxDay) * (1100 - PAD_L - 24)
              const x1 = PAD_L + ((r.run.days_done) / maxDay) * (1100 - PAD_L - 24)
              const parentRow = rows.findIndex((q) => q.run.id === r.run.parent_id)
              const selected = pair?.includes(r.run.id)
              return (
                <g key={r.run.id}>
                  {/* the fork: down from the parent's line at the fork day */}
                  {parentRow >= 0 && (
                    <path
                      d={`M ${x0} ${26 + parentRow * ROW_H} V ${y - 9} q 0 9 9 9`}
                      fill="none" stroke="var(--color-haldi)" strokeWidth={1.5} />
                  )}
                  <line x1={x0} y1={y} x2={Math.max(x1, x0 + 3)} y2={y}
                        stroke={r.run.id === current ? 'var(--color-haldi)' : 'var(--color-ink)'}
                        strokeWidth={r.run.id === current ? 3.5 : 2}
                        strokeLinecap="round" opacity={r.run.id === current ? 1 : 0.55} />
                  {/* the run's own name, and what made it */}
                  <text x={PAD_L - 8} y={y + 3.5} fontSize={11} textAnchor="end"
                        fill="var(--color-ink)"
                        className="cursor-pointer"
                        onClick={() => onOpen(r.run.id)}>
                    {r.run.name.slice(0, 22)}
                  </text>
                  {r.run.what_if && (
                    <text x={x0 + 6} y={y - 6} fontSize={9}
                          fill="var(--color-ink-faint)" fontStyle="italic">
                      {r.run.what_if.slice(0, 46)}
                    </text>
                  )}
                  <circle cx={Math.max(x1, x0 + 3)} cy={y} r={selected ? 5 : 3.5}
                          fill={selected ? 'var(--color-haldi)' : 'var(--color-ink)'}
                          stroke="var(--color-paper)" strokeWidth={1.5}
                          className="cursor-pointer"
                          onClick={() => setPair(pick(pair, r.run.id))} />
                  <text x={1100 - 18} y={y + 3.5} fontSize={9} textAnchor="end"
                        fill="var(--color-ink-faint)"
                        style={{ fontFamily: 'var(--font-mono)' }}>
                    {r.run.households.toLocaleString()}hh · {r.run.days_done}d
                  </text>
                </g>
              )
            })}
          </svg>

          <div className="flex items-center gap-2 mt-3 pt-3
                          border-t border-[var(--color-line)]">
            <div className="text-[11px] text-[var(--color-ink-dim)] flex-1">
              {pair
                ? <>comparing <b>{name(rows, pair[0])}</b> with <b>{name(rows, pair[1])}</b></>
                : 'click two end-dots to compare two worlds'}
            </div>
            {hidden > 0 && (
              <StampButton on={showAll} onClick={() => setShowAll(!showAll)}>
                {showAll ? 'just the forks' : `+${hidden} unbranched`}
              </StampButton>
            )}
            <StampButton disabled={!pair} onClick={() => setPair(null)}>clear</StampButton>
            <StampButton onClick={onClose}>close</StampButton>
          </div>

          {pair && <DiffView a={pair[0]} b={pair[1]} />}
        </div>
      </Panel>
    </div>
  )
}

function pick(cur: [string, string] | null, id: string): [string, string] | null {
  if (!cur) return [id, id]
  if (cur[0] === cur[1]) return cur[0] === id ? null : [cur[0], id]
  return [cur[1], id]
}

function name(rows: Row[], id: string) {
  return rows.find((r) => r.run.id === id)?.run.name ?? id
}

interface Row { run: RunSummary; startDay: number; depth: number }

/** Parents above their children, each child starting at its fork day. */
function layout(runs: RunSummary[]): Row[] {
  const byParent = new Map<string | null, RunSummary[]>()
  for (const r of runs) {
    const k = r.parent_id ?? null
    byParent.set(k, [...(byParent.get(k) ?? []), r])
  }
  const out: Row[] = []
  const walk = (parent: string | null, depth: number) => {
    for (const r of (byParent.get(parent) ?? []).sort((a, b) => a.created_at - b.created_at)) {
      out.push({ run: r, startDay: r.parent_day ?? 0, depth })
      walk(r.id, depth + 1)
    }
  }
  walk(null, 0)
  return out
}

function DiffView({ a, b }: { a: string; b: string }) {
  const q = useQuery({
    queryKey: ['diff', a, b],
    queryFn: () => api.diff(a, b),
    enabled: a !== b,
    retry: false,
  })

  if (a === b) return null
  if (q.isError) {
    return (
      <div className="mt-3 text-[11px] text-[var(--color-danger)]">
        {(q.error as Error).message}
      </div>
    )
  }
  if (!q.data) return <div className="mt-3 text-[11px]">comparing…</div>
  const d = q.data
  const peak = Math.max(1, ...d.by_day_changed.map((x) => x.n))

  return (
    <div className="mt-3 space-y-2">
      {d.identical ? (
        <div className="text-[12px]">The two worlds are identical.</div>
      ) : (
        <>
          {d.headline.map((h, i) => (
            <div key={i} className="text-[12px]">{h}</div>
          ))}
          <div className="flex gap-6 text-[11px] text-[var(--color-ink-dim)]">
            <span><b className="text-[var(--color-ink)] tnum">
              {d.people_changed.toLocaleString()}</b> people had a different day</span>
            {d.reconverged_day != null && <span>re-converged on day {d.reconverged_day + 1}</span>}
          </div>
          {d.by_day_changed.length > 1 && (
            <div>
              <div className="survey text-[9px] text-[var(--color-ink-faint)] mb-1">
                how far apart the worlds drift
              </div>
              <div className="flex items-end gap-[2px] h-[42px]">
                {d.by_day_changed.map((x) => (
                  <div key={x.day} className="flex-1 bg-[var(--color-haldi)]"
                       style={{ height: `${Math.max(2, (x.n / peak) * 42)}px` }}
                       title={`day ${x.day + 1}: ${x.n} people`} />
                ))}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  )
}

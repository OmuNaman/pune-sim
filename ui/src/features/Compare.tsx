import { useQuery } from '@tanstack/react-query'

import { api } from '../api/client'
import type { CompareReport } from '../api/types'
import { Panel, StampButton } from '../components/Panel'
import { useSelection } from '../stores/selection'

/**
 * Two lives, side by side.
 *
 * The middle column is why this screen exists. Two people in a city of fifty
 * thousand mostly never meet; when they have both heard the same rumour they
 * have usually heard it at different hops, at different credences, and often in
 * different words. A dossier alone cannot show that — you have to put two of
 * them next to each other.
 */
export function Compare({ runId, a, b, day, onClose }: {
  runId: string
  a: string
  b: string
  day: number
  onClose: () => void
}) {
  const q = useQuery({
    queryKey: ['compare', runId, a, b, day],
    queryFn: () => api.compare(runId, a, b, day),
  })
  const select = useSelection((s) => s.select)

  return (
    <div className="absolute inset-0 z-40 grid place-items-center bg-[var(--color-ink)]/20"
         onClick={onClose}>
      <Panel className="w-[min(1080px,94vw)] max-h-[86vh] overflow-auto"
             title="two lives" onClick={(e: React.MouseEvent) => e.stopPropagation()}>
        {!q.data ? (
          <div className="p-4 text-[12px]">reading both lives…</div>
        ) : (
          <Body d={q.data} onPerson={(id) => { select({ kind: 'person', id }); onClose() }} />
        )}
        <div className="px-3 py-2 border-t border-[var(--color-line)] flex justify-end">
          <StampButton onClick={onClose}>close</StampButton>
        </div>
      </Panel>
    </div>
  )
}

function Body({ d, onPerson }: { d: CompareReport; onPerson: (id: string) => void }) {
  return (
    <div className="p-4">
      <div className="grid grid-cols-[1fr_auto_1fr] gap-4">
        <Card p={d.a} onPerson={onPerson} />
        <div className="w-px bg-[var(--color-line)]" />
        <Card p={d.b} onPerson={onPerson} right />
      </div>

      {d.same_household && (
        <div className="mt-3 text-[12px] text-center text-[var(--color-haldi-deep)]">
          they live in the same house
        </div>
      )}

      <Section label={`paths crossed on day ${d.day + 1} · ${d.crossings.length}`}>
        {d.crossings.length === 0 ? (
          <Empty>
            they were never in the same place for as long as five minutes —
            which is what most pairs of people in a city are
          </Empty>
        ) : (
          <div className="space-y-1">
            {d.crossings.map((c, i) => (
              <div key={i} className="flex gap-2 items-baseline text-[12px]">
                <span className="tnum text-[10px] text-[var(--color-ink-faint)] w-[40px]"
                      style={{ fontFamily: 'var(--font-mono)' }}>{c.hm}</span>
                <span className="flex-1">{c.place_name}</span>
                <span className="text-[10px] text-[var(--color-ink-faint)]">
                  {c.a_doing ?? '—'} / {c.b_doing ?? '—'}
                </span>
                <span className="tnum text-[11px] w-[42px] text-right">{c.minutes} min</span>
              </div>
            ))}
          </div>
        )}
      </Section>

      <Section label={`the same news, held differently · ${d.shared_claims.length}`}>
        {d.shared_claims.length === 0 ? (
          <Empty>no claim has reached both of them</Empty>
        ) : (
          <div className="space-y-2.5">
            {d.shared_claims.map((s) => (
              <div key={s.key}>
                {s.same_words ? (
                  <div className="text-[12px]">“{s.a.text}”</div>
                ) : (
                  <div className="space-y-[2px]">
                    <div className="text-[12px]">
                      <Side>A</Side> “{s.a.text}”
                    </div>
                    <div className="text-[12px]">
                      <Side>B</Side> “{s.b.text}”
                    </div>
                    <div className="text-[10px] text-[var(--color-haldi-deep)]">
                      the words changed on the way
                      {!!s.b.ops.length && ` — ${s.b.ops.join(', ')}`}
                    </div>
                  </div>
                )}
                <div className="flex gap-4 mt-[3px] text-[10px] text-[var(--color-ink-faint)]"
                     style={{ fontFamily: 'var(--font-mono)' }}>
                  <span>A · hop {s.a.hop} · {pct(s.a.credence)} · {s.a.hm}
                    {s.a.source && s.a.source !== 'origin' && s.a.source !== 'witness'
                      && ` · from ${s.a.source}`}</span>
                  <span>B · hop {s.b.hop} · {pct(s.b.credence)} · {s.b.hm}
                    {s.b.source && s.b.source !== 'origin' && s.b.source !== 'witness'
                      && ` · from ${s.b.source}`}</span>
                </div>
              </div>
            ))}
          </div>
        )}
        <div className="text-[10px] text-[var(--color-ink-faint)] mt-2">
          {d.only_a} claim{d.only_a === 1 ? '' : 's'} only {d.a.name.split(' ')[0]} has
          heard · {d.only_b} only {d.b.name.split(' ')[0]}
        </div>
      </Section>
    </div>
  )
}

const pct = (c: number | null) => `${Math.round((c ?? 0) * 100)}%`

function Side({ children }: { children: React.ReactNode }) {
  return (
    <span className="text-[9px] px-1 mr-1 border border-[var(--color-line-ink)]
                     text-[var(--color-ink-faint)]">{children}</span>
  )
}

function Card({ p, onPerson, right = false }: {
  p: CompareReport['a']; onPerson: (id: string) => void; right?: boolean
}) {
  return (
    <div className={right ? 'text-right' : ''}>
      <button onClick={() => onPerson(p.id)}
              style={{ fontFamily: 'var(--font-display)' }}
              className="text-[17px] leading-tight cursor-pointer hover:underline">
        {p.name}
      </button>
      <div className="text-[11px] text-[var(--color-ink-dim)] mt-0.5">
        {p.age} · {p.occupation} · {p.religion.replace('_', ' ')}
      </div>
      <div className="text-[11px] text-[var(--color-ink-dim)] mt-1 leading-relaxed">
        {p.home_name}
        {p.work_name && <> · {p.work_name}</>}
      </div>
    </div>
  )
}

function Section({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="mt-4 pt-3 border-t border-[var(--color-line)]">
      <div className="survey text-[9px] text-[var(--color-ink-faint)] mb-2">{label}</div>
      {children}
    </div>
  )
}

function Empty({ children }: { children: React.ReactNode }) {
  return <div className="text-[11px] italic text-[var(--color-ink-faint)]">{children}</div>
}

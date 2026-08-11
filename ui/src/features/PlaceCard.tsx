import { useQuery } from '@tanstack/react-query'
import { Panel, Diamond } from '../components/Panel'
import { api } from '../api/client'
import { LANE_COLOR } from '../lib/lanes'
import heritage from '../content/heritage.json'

const NOTES = (heritage as any).places as Record<
  string, { dev: string; est: string; note: string }
>

/** `place:way/123` -> `way/123`, which is how the curated notes are keyed. */
function osmId(placeId: string) {
  return placeId.replace(/^(place|home):/, '')
}

export function PlaceCard({ runId, placeId, t }: {
  runId: string; placeId: string; t: number
}) {
  // Quantised to the hour, and the QUERY reads the quantised value too. Using
  // the raw `t` in queryFn while keying on the hour makes every re-render a
  // different fetch for the same key, so the panel restarts for ever and never
  // leaves its loading state — which is exactly what it did.
  const hour = Math.floor(t / 3600)
  const q = useQuery({
    queryKey: ['place', runId, placeId, hour],
    queryFn: () => api.place(runId, placeId, hour * 3600),
    placeholderData: (prev) => prev,   // keep the last card while the next loads
  })
  const note = NOTES[osmId(placeId)]

  if (!q.data) return <Panel className="p-3 text-[12px]" title="place">loading…</Panel>
  const p = q.data

  return (
    <Panel className="overflow-y-auto max-h-full" title="place">
      <div className="p-3">
        {note && (
          <div style={{ fontFamily: 'var(--font-display)' }} className="text-[20px] leading-tight">
            {note.dev}
          </div>
        )}
        <div className="text-[15px] font-semibold leading-tight">{p.name || p.kind}</div>
        <div className="flex gap-1.5 mt-1.5 flex-wrap">
          <Chip>{p.kind}</Chip>
          {note && <Chip>est. {note.est}</Chip>}
          <Chip>{p.here_n} here now</Chip>
        </div>

        {note && (
          <div className="mt-2.5 border-l-2 border-[var(--color-haldi)] pl-2.5
                          text-[12px] leading-[1.5] text-[var(--color-ink-dim)]">
            {note.note}
          </div>
        )}
      </div>

      <Section label={`who is here · ${p.here_n}`}>
        {p.here.length === 0
          ? <Empty>nobody, at this hour</Empty>
          : (
            <div className="flex flex-wrap gap-1 px-3 pb-2.5">
              {p.here.slice(0, 60).map((h) => (
                <span key={h.id}
                      className="text-[11px] px-1.5 py-[1px] border border-[var(--color-line)]
                                 bg-[var(--color-paper)]"
                      title={h.activity}>
                  {h.name}
                </span>
              ))}
              {p.here_n > 60 && (
                <span className="text-[11px] text-[var(--color-ink-faint)] px-1">
                  +{p.here_n - 60} more
                </span>
              )}
            </div>
          )}
      </Section>

      <Section label="today, here">
        {p.today.length === 0
          ? <Empty>an ordinary day — nothing notable happened here</Empty>
          : (
            <div className="px-3 pb-3 space-y-1">
              {p.today.map((e) => (
                <div key={e.seq} className="flex gap-2 items-start text-[12px] leading-snug">
                  <span className="tnum text-[10px] text-[var(--color-ink-faint)] pt-[3px] w-[34px]"
                        style={{ fontFamily: 'var(--font-mono)' }}>{e.hm}</span>
                  <Diamond color={LANE_COLOR(e.type)} />
                  <span className="flex-1">{e.text}</span>
                </div>
              ))}
            </div>
          )}
      </Section>
    </Panel>
  )
}

export function Chip({ children }: { children: React.ReactNode }) {
  return (
    <span className="text-[10px] px-1.5 py-[1px] border border-[var(--color-line-ink)]
                     text-[var(--color-ink-dim)] uppercase tracking-[0.06em]">
      {children}
    </span>
  )
}

export function Section({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="border-t border-[var(--color-line)]">
      <div className="survey text-[9px] text-[var(--color-ink-faint)] px-3 pt-2 pb-1.5">
        {label}
      </div>
      {children}
    </div>
  )
}

export function Empty({ children }: { children: React.ReactNode }) {
  return (
    <div className="px-3 pb-2.5 text-[11px] italic text-[var(--color-ink-faint)]">
      {children}
    </div>
  )
}

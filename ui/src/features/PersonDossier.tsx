import { useQuery } from '@tanstack/react-query'
import { Panel, Diamond, StampButton } from '../components/Panel'
import { Chip, Section, Empty } from './PlaceCard'
import { api } from '../api/client'
import { LANE_COLOR } from '../lib/lanes'
import { useSelection } from '../stores/selection'
import { DAY_S } from '../clock/engine'

export function PersonDossier({ runId, personId, t }: {
  runId: string; personId: string; t: number
}) {
  const day = Math.floor(t / DAY_S)
  const select = useSelection((s) => s.select)
  const pinned = useSelection((s) => s.pinned)
  const pin = useSelection((s) => s.pin)
  const q = useQuery({
    queryKey: ['person', runId, personId, day],
    queryFn: () => api.person(runId, personId, day),
    placeholderData: (prev) => prev,   // scrubbing must not blank the dossier
  })

  if (!q.data) return <Panel className="p-3 text-[12px]" title="person">loading…</Panel>
  const p = q.data
  const today = p.timeline.filter((e) => e.day === day)

  return (
    <Panel className="overflow-y-auto max-h-full" title="person">
      <div className="p-3">
        <div style={{ fontFamily: 'var(--font-display)' }} className="text-[19px] leading-tight">
          {p.name}
        </div>
        <div className="flex gap-1.5 mt-1.5 flex-wrap">
          <Chip>{p.age}</Chip>
          <Chip>{p.occupation}</Chip>
          <Chip>{p.religion.replace('_', ' ')}</Chip>
        </div>
        <div className="mt-2 text-[11px] text-[var(--color-ink-dim)] leading-relaxed">
          lives at <b className="text-[var(--color-ink)]">{p.home_name}</b>
          {p.work_name && <> · {p.occupation === 'student' ? 'studies' : 'works'} at{' '}
            <b className="text-[var(--color-ink)]">{p.work_name}</b></>}
        </div>
      </div>

      <Section label={`household · ${p.household}`}>
        <div className="flex flex-wrap gap-1 px-3 pb-2.5">
          {p.members.map((m) => (
            <button
              key={m.id}
              onClick={() => select({ kind: 'person', id: m.id })}
              className={`text-[11px] px-1.5 py-[1px] border cursor-pointer
                ${m.id === p.id
                  ? 'border-[var(--color-haldi)] bg-[var(--color-haldi)]/25'
                  : 'border-[var(--color-line)] bg-[var(--color-paper)] hover:border-[var(--color-ink)]'}`}
              title={`${m.age} · ${m.occupation}`}
            >
              {m.name.split(' ')[0]} <span className="text-[var(--color-ink-faint)]">{m.age}</span>
            </button>
          ))}
        </div>
      </Section>

      <Section label={`their day · day ${day + 1}`}>
        {today.length === 0
          ? <Empty>nothing notable today — they went about their business</Empty>
          : (
            <div className="px-3 pb-3 space-y-1">
              {today.map((e) => (
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

      {p.heard.length > 0 && (
        <Section label={`what they have heard · ${p.heard.length}`}>
          <div className="px-3 pb-3 space-y-2">
            {p.heard.slice(-8).reverse().map((h, i) => (
              <div key={i}>
                <div className="text-[12px] leading-snug">“{h.text}”</div>
                <div className="flex items-center gap-2 mt-[3px]">
                  <div className="h-[3px] w-16 bg-[var(--color-line)]">
                    <div className="h-full bg-[var(--color-rumor)]"
                         style={{ width: `${Math.round((h.credence ?? 0) * 100)}%` }} />
                  </div>
                  <span className="tnum text-[9px] text-[var(--color-ink-faint)]"
                        style={{ fontFamily: 'var(--font-mono)' }}>
                    {Math.round((h.credence ?? 0) * 100)}% · hop {h.hop} · {h.hm}
                    {h.source && h.source !== 'origin' && h.source !== 'witness'
                      && ` · from ${h.source}`}
                  </span>
                </div>
              </div>
            ))}
          </div>
        </Section>
      )}

      {p.memories.length > 0 && (
        <Section label="what they remember">
          <div className="px-3 pb-3 space-y-1.5">
            {p.memories.slice(-6).reverse().map((m, i) => (
              <div key={i} className="text-[12px] leading-snug border-l-2 pl-2
                                      border-[var(--color-memory)]/40 italic">
                {m.summary}
              </div>
            ))}
          </div>
        </Section>
      )}

      {p.interviews.length > 0 && (
        <Section label="interviews">
          <div className="px-3 pb-3 space-y-2">
            {p.interviews.map((iv, i) => (
              <div key={i}>
                <div className="text-[11px] text-[var(--color-ink-dim)]">— {iv.question}</div>
                <div className="text-[12px] leading-snug mt-[2px]">{iv.answer}</div>
              </div>
            ))}
          </div>
        </Section>
      )}

      <Section label="">
        <div className="px-3 pb-3 flex gap-1.5 items-center">
          <StampButton onClick={() => select({ kind: 'none' })}>close</StampButton>
          {pinned === p.id ? (
            <StampButton on onClick={() => pin(null)}>pinned</StampButton>
          ) : (
            <StampButton onClick={() => pin(p.id)}
                         title="pin this person, then open another to compare">
              {pinned ? '+ compare' : 'pin to compare'}
            </StampButton>
          )}
          {pinned && pinned !== p.id && (
            <span className="text-[10px] text-[var(--color-ink-faint)]">
              vs the pinned one
            </span>
          )}
        </div>
      </Section>
    </Panel>
  )
}

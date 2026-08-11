import { useState } from 'react'
import { api } from '../api/client'
import type { RunMeta } from '../api/types'
import { Panel, StampButton } from '../components/Panel'
import { duration } from '../lib/time'

/**
 * Fork the world at a day and change one thing.
 *
 * The cost has to be on the button, because it is not obvious: a branch is not
 * a copy of the database, it is the same world re-run with one more event in
 * it. Branching at day 12 of a 30-day V3 run re-simulates twelve days. That is
 * also exactly WHY the diff means anything — the shared past is byte-identical,
 * so everything after the fork was caused by the change.
 */

const KINDS = [
  { type: 'hazard.water.supply_cut', label: 'water cut' },
  { type: 'hazard.power.outage', label: 'power cut' },
  { type: 'hazard.road.collision', label: 'road accident' },
  { type: 'hazard.fire.small', label: 'fire' },
]

export function BranchHere({ meta, day, secondsPerDay, onClose, onBranched }: {
  meta: RunMeta
  /** The day the playhead is on — where the fork defaults to. */
  day: number
  secondsPerDay: number
  onClose: () => void
  onBranched: (id: string) => void
}) {
  const [forkDay, setForkDay] = useState(Math.min(day + 1, meta.days_done))
  const [kind, setKind] = useState(KINDS[0].type)
  const [place, setPlace] = useState('')
  const [time, setTime] = useState('08:00')
  const [addDays, setAddDays] = useState(0)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  const replay = forkDay * secondsPerDay
  const total = (forkDay + addDays + (meta.days_done - forkDay)) * secondsPerDay

  const go = () => {
    setBusy(true); setErr('')
    api.branch(meta.id, {
      what_if: KINDS.find((k) => k.type === kind)?.label ?? kind,
      from_day: forkDay,
      add_days: addDays,
      injections: [{ day: forkDay, time, type: kind,
                     place: place || null, severity: 0.7 }],
    })
      .then((r) => { onBranched(r.id); onClose() })
      .catch((e: Error) => { setErr(e.message); setBusy(false) })
  }

  return (
    <div className="absolute inset-0 z-40 grid place-items-center bg-[var(--color-ink)]/15"
         onClick={onClose}>
      <Panel className="w-[480px]" title="⑂ what if…"
             onClick={(e: React.MouseEvent) => e.stopPropagation()}>
        <div className="p-4 space-y-3">
          <div className="text-[12px] text-[var(--color-ink-dim)]">
            A second copy of <b className="text-[var(--color-ink)]">{meta.name}</b> that
            is the same world until day {forkDay + 1}, and then is not.
          </div>

          <Row label="fork at">
            <input type="range" min={0} max={Math.max(0, meta.days_done)}
                   value={forkDay} onChange={(e) => setForkDay(+e.target.value)}
                   className="flex-1 accent-[var(--color-haldi)]" />
            <span className="tnum text-[12px] w-[54px] text-right">day {forkDay + 1}</span>
          </Row>

          <Row label="what happens">
            <select value={kind} onChange={(e) => setKind(e.target.value)}
                    className="flex-1 bg-[var(--color-paper)] border border-[var(--color-line)]
                               px-1.5 py-0.5 text-[12px] outline-none">
              {KINDS.map((k) => <option key={k.type} value={k.type}>{k.label}</option>)}
            </select>
            <input value={time} onChange={(e) => setTime(e.target.value)}
                   className="w-[52px] bg-[var(--color-paper)] border border-[var(--color-line)]
                              px-1 py-0.5 text-[12px] tnum outline-none" />
          </Row>

          <Row label="where">
            <input value={place} onChange={(e) => setPlace(e.target.value)}
                   placeholder="a place id, or leave blank to let the sim choose"
                   className="flex-1 bg-[var(--color-paper)] border border-[var(--color-line)]
                              px-1.5 py-0.5 text-[11px] outline-none" />
          </Row>

          <Row label="extra days">
            <input type="range" min={0} max={30} value={addDays}
                   onChange={(e) => setAddDays(+e.target.value)}
                   className="flex-1 accent-[var(--color-haldi)]" />
            <span className="tnum text-[12px] w-[54px] text-right">+{addDays}</span>
          </Row>

          <div className="border-t border-[var(--color-line)] pt-2.5 text-[11px]
                          text-[var(--color-ink-dim)] leading-relaxed">
            {forkDay > 0 ? (
              <>Days 1–{forkDay} are re-simulated <b>identically</b> — about{' '}
                <b className="text-[var(--color-ink)]">{duration(replay)}</b> of
                computing before the two worlds can even differ. That replay is
                what makes the comparison mean something: every difference after
                the fork was caused by this change and nothing else.
              </>
            ) : (
              <>Branching from the very beginning — the two worlds differ from
                day one.</>
            )}
            <div className="mt-1">
              the whole branch: about <b className="text-[var(--color-ink)]">
              {duration(total)}</b>
            </div>
          </div>

          {err && <div className="text-[11px] text-[var(--color-danger)]">{err}</div>}

          <div className="flex gap-2 justify-end">
            <StampButton onClick={onClose}>cancel</StampButton>
            <StampButton primary disabled={busy} onClick={go}>
              {busy ? 'forking…' : '⑂ branch'}
            </StampButton>
          </div>
        </div>
      </Panel>
    </div>
  )
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-2">
      <span className="survey text-[9px] text-[var(--color-ink-dim)] w-[76px]">{label}</span>
      {children}
    </div>
  )
}

import { useEffect, useState } from 'react'
import { Wordmark } from '../components/Logo'
import { Odometer } from '../components/Odometer'
import { StampButton } from '../components/Panel'
import { clock, DAY_S } from '../clock/engine'
import { clockHM, compact, longDate } from '../lib/time'
import type { RunMeta, RunSummary } from '../api/types'

const SPEEDS = [
  { label: '1×', v: 60 },      // one sim-minute a second
  { label: '10×', v: 600 },
  { label: '60×', v: 3600 },   // an hour a second — a day in 24s
  { label: '⚡', v: 21600 },    // a day in four seconds
]

export function TopBar({
  meta, people, runs, onPickRun, onNewRun,
}: {
  meta: RunMeta
  /** From the roster once it exists; meta.people is 0 until then. */
  people: number
  runs: RunSummary[]
  onPickRun: (id: string) => void
  onNewRun: () => void
}) {
  const [t, setT] = useState(clock.t)
  const [playing, setPlaying] = useState(clock.playing)
  const [speed, setSpeed] = useState(clock.speed)

  // The clock ticks at 60fps; the header only needs to say what minute it is,
  // so this subscribes and throttles rather than re-rendering per frame.
  useEffect(() => {
    let last = 0
    return clock.subscribe((now) => {
      if (Math.abs(now - last) < 20 && clock.playing) return
      last = now
      setT(now)
      setPlaying(clock.playing)
    })
  }, [])

  const maxT = meta.max_t
  const day = Math.floor(t / DAY_S)
  const lastDay = meta.days_done - 1

  const step = (d: number) => {
    const target = Math.max(0, Math.min((day + d) * DAY_S + 8 * 3600, maxT))
    clock.pause()
    clock.seek(target, maxT)
    setPlaying(false)
  }

  return (
    <div className="panel grain absolute top-3 left-3 right-3 z-20 flex items-center
                    gap-4 px-3 py-2">
      <Wordmark />

      <div className="h-8 w-px bg-[var(--color-line)]" />

      <select
        className="bg-transparent text-[13px] font-medium outline-none cursor-pointer
                   max-w-[220px] truncate"
        value={meta.id}
        onChange={(e) => onPickRun(e.target.value)}
      >
        {runs.map((r) => (
          <option key={r.id} value={r.id}>
            {r.name} · {r.households.toLocaleString()}hh · {r.days_done}d
          </option>
        ))}
      </select>
      <StampButton onClick={onNewRun} title="make a new world">+ new</StampButton>

      <div className="h-8 w-px bg-[var(--color-line)]" />

      <div className="flex items-baseline gap-3">
        <div className="text-[11px] survey text-[var(--color-ink-dim)]">
          Day <span className="tnum text-[var(--color-ink)] text-[13px]">{day + 1}</span>
          <span className="text-[var(--color-ink-faint)]">/{meta.days_done}</span>
        </div>
        <Odometer value={clockHM(t)} className="text-[22px]" />
        <div className="text-[11px] text-[var(--color-ink-dim)]">{longDate(t)}</div>
      </div>

      <div className="flex items-center gap-1.5 ml-1">
        <StampButton onClick={() => step(-1)} disabled={day <= 0} title="previous day">◀</StampButton>
        <StampButton
          primary
          onClick={() => { clock.toggle(maxT); setPlaying(clock.playing) }}
          title={playing ? 'pause' : 'play'}
          className="min-w-[42px]"
        >
          {playing ? '⏸' : '▶'}
        </StampButton>
        <StampButton onClick={() => step(1)} disabled={day >= lastDay} title="next day">▶</StampButton>
      </div>

      <div className="flex items-center gap-1">
        {SPEEDS.map((s) => (
          <StampButton
            key={s.label}
            on={speed === s.v}
            onClick={() => { clock.speed = s.v; setSpeed(s.v) }}
            className="!px-2 !py-0.5 !text-[11px]"
          >
            {s.label}
          </StampButton>
        ))}
      </div>

      <div className="ml-auto flex items-center gap-4 text-[11px] text-[var(--color-ink-dim)]">
        <Stat label="people" value={people.toLocaleString()} />
        <Stat label="events" value={compact(meta.events)} />
        <Stat label="block" value={meta.block} />
        <Stat label="seed" value={String(meta.seed)} />
      </div>
    </div>
  )
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="leading-tight">
      <div className="survey text-[8px] text-[var(--color-ink-faint)]">{label}</div>
      <div className="tnum text-[12px] text-[var(--color-ink)]"
           style={{ fontFamily: 'var(--font-mono)' }}>{value}</div>
    </div>
  )
}

import { useEffect, useRef, useState } from 'react'
import { clock, DAY_S } from '../clock/engine'
import type { DaySummary, RunMeta } from '../api/types'
import { clockHM } from '../lib/time'

/**
 * The day ribbon: one cell per computed day, its height the day's notable
 * event count, with a draggable playhead across the lot.
 *
 * Days are the sim's own unit — it computes one whole day at a time and can
 * never do half — so the strip is cells rather than a continuous axis. When a
 * run is computing, the pending day gets its own cell with a progress ring
 * (Phase 4); the shape is already here so it does not need re-laying-out.
 */
export function TimelineStrip({ meta, days }: { meta: RunMeta; days: DaySummary[] }) {
  const [t, setT] = useState(clock.t)
  const [dragging, setDragging] = useState(false)
  const barRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    let last = 0
    return clock.subscribe((now) => {
      if (Math.abs(now - last) < 60 && clock.playing) return
      last = now
      setT(now)
    })
  }, [])

  const total = Math.max(1, meta.days_done * DAY_S)
  const peak = Math.max(1, ...days.map((d) => d.notable))

  const seekTo = (clientX: number) => {
    const el = barRef.current
    if (!el) return
    const r = el.getBoundingClientRect()
    const frac = Math.min(1, Math.max(0, (clientX - r.left) / r.width))
    clock.pause()
    clock.seek(frac * total, meta.max_t)
  }

  useEffect(() => {
    if (!dragging) return
    const move = (e: PointerEvent) => seekTo(e.clientX)
    const up = () => setDragging(false)
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
    return () => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
    }
  }, [dragging])

  const playFrac = t / total

  return (
    <div className="panel grain absolute bottom-3 left-3 right-3 z-20 px-3 py-2">
      <div className="flex items-center justify-between mb-1.5">
        <div className="survey text-[9px] text-[var(--color-ink-dim)]">
          {meta.days_done} days computed
        </div>
        <div className="text-[10px] tnum text-[var(--color-ink-faint)]"
             style={{ fontFamily: 'var(--font-mono)' }}>
          day {Math.floor(t / DAY_S) + 1} · {clockHM(t)}
        </div>
      </div>

      <div
        ref={barRef}
        className="relative h-[46px] cursor-pointer select-none"
        onPointerDown={(e) => { setDragging(true); seekTo(e.clientX) }}
      >
        <div className="absolute inset-0 flex gap-[2px] items-end">
          {days.map((d) => (
            <div
              key={d.day}
              className="flex-1 min-w-0 relative group"
              title={`day ${d.day + 1} — ${d.notable.toLocaleString()} notable of ${d.total.toLocaleString()}`}
            >
              <div
                className="w-full bg-[var(--color-haldi)] opacity-70
                           group-hover:opacity-100 transition-opacity"
                style={{ height: `${Math.max(3, (d.notable / peak) * 40)}px` }}
              />
              <div className="absolute bottom-0 left-0 right-0 h-[2px]
                              bg-[var(--color-line-ink)] opacity-40" />
            </div>
          ))}
        </div>

        {/* the playhead */}
        <div
          className="absolute top-0 bottom-0 w-[2px] bg-[var(--color-ink)] pointer-events-none"
          style={{ left: `${playFrac * 100}%` }}
        >
          <div className="absolute -top-1 -left-[3px] w-2 h-2 bg-[var(--color-ink)]"
               style={{ transform: 'rotate(45deg)' }} />
        </div>
      </div>

      <div className="flex justify-between mt-1 text-[8px] survey text-[var(--color-ink-faint)]">
        <span>day 1</span>
        <span>day {meta.days_done}</span>
      </div>
    </div>
  )
}

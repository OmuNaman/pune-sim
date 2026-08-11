import { useState } from 'react'
import { api } from '../api/client'
import { Panel, StampButton } from '../components/Panel'
import { duration } from '../lib/time'

/**
 * Making a world.
 *
 * The numbers under the sliders are the point. A run is a real cost in time and
 * (with scenes on) in money, and both scale hard with the household count —
 * 0.04 seconds a day at 80 households, 62-86 at 12,000. A dialog that hides
 * that lets you ask for two and a half hours of compute by dragging a slider.
 */

// Measured, from docs/perf/scale-probe.md and the V3 soak: seconds per sim-day.
// Interpolated between rungs; kasba's tiny world is faster per person than the
// routed oldcity one, which is why they are separate.
const RUNGS: Record<string, [number, number][]> = {
  kasba: [[306, 0.042], [1266, 0.314], [5000, 3.23], [11240, 14.58]],
  oldcity: [[12438, 22.2], [24716, 35.7], [49578, 86.1]],
}

function secondsPerDay(block: string, people: number): number {
  const rungs = RUNGS[block] ?? RUNGS.kasba
  if (people <= rungs[0][0]) return rungs[0][1] * (people / rungs[0][0])
  for (let i = 1; i < rungs.length; i++) {
    const [p0, s0] = rungs[i - 1], [p1, s1] = rungs[i]
    if (people <= p1) return s0 + ((people - p0) / (p1 - p0)) * (s1 - s0)
  }
  const [pl, sl] = rungs[rungs.length - 1]
  return sl * (people / pl) ** 1.2   // beyond the measured range, and says so
}

export function NewRun({ onClose, onCreated }: {
  onClose: () => void
  onCreated: (id: string) => void
}) {
  const [block, setBlock] = useState<'kasba' | 'oldcity'>('oldcity')
  const [households, setHouseholds] = useState(2000)
  const [days, setDays] = useState(7)
  const [seed, setSeed] = useState(108)
  const [hazards, setHazards] = useState(true)
  const [scenes, setScenes] = useState(false)
  const [k, setK] = useState(5)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  // ~4.13 people per household, from the fitted demography.
  const people = Math.round(households * 4.13)
  const perDay = secondsPerDay(block, people)
  const beyond = people > (RUNGS[block].at(-1)?.[0] ?? 0)
  // $0.0031/sim-day measured at 12,000 households with the spotlight gate at
  // k=5 — spend follows attention, not population, which is why it barely moves.
  const spend = scenes ? days * 0.0031 * (k / 5) : 0

  const go = () => {
    setBusy(true); setErr('')
    api.create({ block, households, days, seed, hazards, scenes, k, autostart: true })
      .then((r) => { onCreated(r.id); onClose() })
      .catch((e: Error) => { setErr(e.message); setBusy(false) })
  }

  return (
    <div className="absolute inset-0 z-40 grid place-items-center
                    bg-[var(--color-ink)]/15" onClick={onClose}>
      <Panel className="w-[520px]" title="a new world"
             onClick={(e: React.MouseEvent) => e.stopPropagation()}>
        <div className="p-4 space-y-4">
          <Field label="block" hint={block === 'kasba'
            ? 'the V0–V2 pin: 124 named places, straight-line walking'
            : "V3's four peths: 438 places, 2,057 streets, routed"}>
            <div className="flex gap-1.5">
              {(['kasba', 'oldcity'] as const).map((b) => (
                <StampButton key={b} on={block === b} onClick={() => setBlock(b)}>
                  {b}
                </StampButton>
              ))}
            </div>
          </Field>

          <Field label="households" hint={`${people.toLocaleString()} people`}>
            <input type="range" min={20} max={block === 'kasba' ? 2880 : 12000}
                   step={20} value={Math.min(households, block === 'kasba' ? 2880 : 12000)}
                   onChange={(e) => setHouseholds(+e.target.value)}
                   className="w-full accent-[var(--color-haldi)]" />
          </Field>

          <Field label="days" hint={`to ${new Date(Date.UTC(2026, 0, 1 + days))
            .toLocaleDateString('en-GB', { day: '2-digit', month: 'short' })}`}>
            <input type="range" min={1} max={90} value={days}
                   onChange={(e) => setDays(+e.target.value)}
                   className="w-full accent-[var(--color-haldi)]" />
          </Field>

          <div className="flex gap-4">
            <Field label="seed" hint="the same seed is the same world, always">
              <div className="flex gap-1.5 items-center">
                <input type="number" value={seed} onChange={(e) => setSeed(+e.target.value)}
                       className="w-20 bg-[var(--color-paper)] border border-[var(--color-line)]
                                  px-1.5 py-0.5 text-[12px] tnum outline-none" />
                <StampButton onClick={() => setSeed(Math.floor(Math.random() * 100000))}>
                  ⚄
                </StampButton>
              </div>
            </Field>
            <Field label="lanes" hint={scenes ? `${k} households on camera a day` : ''}>
              <div className="flex gap-1.5 items-center">
                <StampButton on={hazards} onClick={() => setHazards(!hazards)}>
                  hazards
                </StampButton>
                <StampButton on={scenes} onClick={() => setScenes(!scenes)}>
                  scenes (LLM)
                </StampButton>
                {scenes && (
                  <input type="range" min={1} max={20} value={k}
                         onChange={(e) => setK(+e.target.value)}
                         className="w-16 accent-[var(--color-haldi)]"
                         title="households the camera follows each day" />
                )}
              </div>
            </Field>
          </div>

          {scenes && (
            <div className="text-[10px] text-[var(--color-ink-faint)] leading-snug">
              Scenes are written by a language model and cost real money on your own
              key. Spend follows attention rather than population — the gate renders
              k households a day whether the city holds 300 people or 50,000.
            </div>
          )}

          <div className="border-t border-[var(--color-line)] pt-3 text-[12px] space-y-1">
            <Cost label="one day takes"
                  value={perDay < 1 ? `${(perDay * 1000).toFixed(0)} ms` : `${perDay.toFixed(1)} s`} />
            <Cost label={`all ${days} days take`} value={duration(perDay * days)} />
            {scenes && <Cost label="model spend" value={`about $${spend.toFixed(3)}`} />}
            {beyond && (
              <div className="text-[10px] italic text-[var(--color-ink-faint)]">
                past the largest size actually measured — treat the estimate as a floor
              </div>
            )}
          </div>

          {err && <div className="text-[11px] text-[var(--color-danger)]">{err}</div>}

          <div className="flex gap-2 justify-end">
            <StampButton onClick={onClose}>cancel</StampButton>
            <StampButton primary disabled={busy} onClick={go}>
              {busy ? 'building…' : 'प्रारंभ · begin'}
            </StampButton>
          </div>
        </div>
      </Panel>
    </div>
  )
}

function Field({ label, hint, children }: {
  label: string; hint?: string; children: React.ReactNode
}) {
  return (
    <div className="flex-1">
      <div className="flex justify-between items-baseline mb-1">
        <span className="survey text-[9px] text-[var(--color-ink-dim)]">{label}</span>
        {hint && <span className="text-[10px] text-[var(--color-ink-faint)]">{hint}</span>}
      </div>
      {children}
    </div>
  )
}

function Cost({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between">
      <span className="text-[var(--color-ink-dim)]">{label}</span>
      <span className="tnum" style={{ fontFamily: 'var(--font-mono)' }}>{value}</span>
    </div>
  )
}

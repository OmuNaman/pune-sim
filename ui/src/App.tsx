import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'

import { api } from './api/client'
import type { RunMeta } from './api/types'
import { clock, DAY_S } from './clock/engine'
import { setEpoch } from './lib/time'
import { runSecondsPerDay } from './lib/cost'
import { MapRoot } from './map/MapRoot'
import { TopBar } from './panels/TopBar'
import { TimelineStrip } from './panels/TimelineStrip'
import { LeftRail } from './panels/LeftRail'
import { Inspector } from './panels/Inspector'
import { RunControl } from './features/RunControl'
import { NewRun } from './features/NewRun'
import { BranchHere } from './features/BranchHere'
import { Timelines } from './features/Timelines'
import { RumourBoard } from './features/RumourBoard'
import { Compare } from './features/Compare'
import { useSelection } from './stores/selection'
import { Panel } from './components/Panel'
import { Logo } from './components/Logo'

export default function App() {
  const [runId, setRunId] = useState<string | null>(null)
  const [t, setT] = useState(0)
  const [newRun, setNewRun] = useState(false)
  const [branching, setBranching] = useState(false)
  const [timelines, setTimelines] = useState(false)
  const [rumours, setRumours] = useState(false)
  const sel = useSelection((s) => s.sel)
  const pinned = useSelection((s) => s.pinned)

  const runs = useQuery({ queryKey: ['runs'], queryFn: api.runs })

  // Which run to open first. `?run=` wins so a link is shareable; otherwise
  // the biggest one with days in it, because on a machine with twenty ad-hoc
  // run directories the interesting one is the one with a city in it.
  useEffect(() => {
    if (runId || !runs.data) return
    const asked = new URLSearchParams(location.search).get('run')
    const usable = runs.data.runs.filter((r) => r.days_done > 0)
    if (!usable.length) return
    const hit = asked && usable.find((r) => r.id === asked || r.name === asked)
    if (hit) return setRunId(hit.id)
    usable.sort((a, b) => b.households * b.days_done - a.households * a.days_done)
    setRunId(usable[0].id)
  }, [runs.data, runId])

  const meta = useQuery({
    queryKey: ['meta', runId],
    queryFn: () => api.meta(runId!),
    enabled: !!runId,
  })

  const days = useQuery({
    queryKey: ['days', runId],
    queryFn: () => api.days(runId!),
    enabled: !!runId,
  })

  // The roster is the expensive part — 13s at V3 scale — and the map does not
  // wait for it. Asking for it here is what triggers the build; the map is
  // already drawing streets by the time this resolves.
  const roster = useQuery({
    queryKey: ['roster', runId],
    queryFn: () => api.roster(runId!),
    enabled: !!runId,
  })

  useEffect(() => {
    const m = meta.data
    if (!m) return
    setEpoch(m.epoch)
  }, [meta.data?.epoch])

  // Once the people exist, point the clock at them and pull the first frame.
  useEffect(() => {
    const m = meta.data
    const r = roster.data
    if (!m || !r) return
    // Open at 10:30, not at midnight or at dawn: the city is at its busiest
    // mid-morning, and a first frame where everybody is asleep at home looks
    // identical to a first frame that failed to load.
    void clock.open(m.id, r.order.length, Math.min(10.5 * 3600, m.max_t))
  }, [meta.data?.id, roster.data?.order.length])

  // The panels need the time, but not at 60fps — they show minutes, not
  // frames. Throttling here keeps React out of the animation loop entirely.
  //
  // Seeded with the clock's CURRENT value, not 0: this effect runs once on
  // mount and `clock.open()` happens later, so a panel that only ever learns
  // the time from the subscription sits at t=0 until the user touches
  // something. That is how the feed spent its life asking for day 1 of a run
  // whose playhead was on day 10.
  useEffect(() => {
    let last = -1e9
    setT(clock.t)
    return clock.subscribe((now) => {
      if (Math.abs(now - last) < 60) return
      last = now
      setT(now)
    })
  }, [roster.data?.order.length])

  // Space plays, arrows step a day. A map app that needs the mouse for the
  // clock is a map app you drive with one hand tied.
  useEffect(() => {
    const m = meta.data
    if (!m) return
    const onKey = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement)?.tagName === 'INPUT') return
      if (e.code === 'Space') { e.preventDefault(); clock.toggle(m.max_t) }
      if (e.code === 'ArrowRight') clock.seek(clock.t + DAY_S, m.max_t)
      if (e.code === 'ArrowLeft') clock.seek(clock.t - DAY_S, m.max_t)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [meta.data?.id, meta.data?.max_t])

  if (runs.isError) return <Splash msg="The API is not answering. Is `punesim ui` running?" />
  if (!runs.data) return <Splash msg="Reading the runs…" />
  if (!runId) return <Splash msg="No run on disk has any days in it yet." />
  if (!meta.data) return <Splash msg="Opening the world…" />

  const m: RunMeta = meta.data

  const people = roster.data?.order.length ?? m.households * 4

  return (
    <div className="relative w-full h-full">
      <MapRoot meta={m} />
      <TopBar meta={m} people={people} runs={runs.data.runs.filter((r) => r.days_done > 0)}
              onPickRun={setRunId} onNewRun={() => setNewRun(true)}
              onBranch={() => setBranching(true)}
              onTimelines={() => setTimelines(true)}
              onRumours={() => setRumours(true)} />
      <LeftRail runId={m.id} t={t} />
      <Inspector runId={m.id} t={t} order={roster.data?.order} />
      <RunControl meta={m} />
      {days.data && <TimelineStrip meta={m} days={days.data} />}
      {!roster.data && <Waking households={m.households} />}
      {newRun && (
        <NewRun onClose={() => setNewRun(false)}
                onCreated={(id) => { void runs.refetch(); setRunId(id) }} />
      )}
      {branching && (
        <BranchHere meta={m} day={Math.floor(t / DAY_S)}
                    secondsPerDay={runSecondsPerDay(m)}
                    onClose={() => setBranching(false)}
                    onBranched={(id) => { void runs.refetch(); setRunId(id) }} />
      )}
      {timelines && (
        <Timelines current={m.id} onClose={() => setTimelines(false)}
                   onOpen={(id) => { setRunId(id); setTimelines(false) }} />
      )}
      {rumours && <RumourBoard runId={m.id} onClose={() => setRumours(false)} />}
      {/* Two people are pinned against each other: show the comparison. */}
      {pinned && sel.kind === 'person' && sel.id !== pinned && (
        <Compare runId={m.id} a={pinned} b={sel.id} day={Math.floor(t / DAY_S)}
                 onClose={() => useSelection.getState().pin(null)} />
      )}
    </div>
  )
}

/** The map is up and the streets are drawn; the people are still being made. */
function Waking({ households }: { households: number }) {
  return (
    <div className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 z-30">
      <Panel className="px-6 py-4 text-center">
        <div className="survey text-[10px] text-[var(--color-ink-dim)] mb-1">
          waking the city
        </div>
        <div className="text-[12px] text-[var(--color-ink)]">
          synthesising {households.toLocaleString()} households…
        </div>
        <div className="mt-2 h-[3px] w-40 mx-auto bg-[var(--color-line)] overflow-hidden">
          <div className="h-full w-1/3 bg-[var(--color-haldi)] animate-[slide_1.1s_ease-in-out_infinite]" />
        </div>
      </Panel>
    </div>
  )
}

function Splash({ msg }: { msg: string }) {
  return (
    <div className="w-full h-full grid place-items-center">
      <Panel className="px-8 py-7 text-center">
        <div className="flex justify-center mb-3"><Logo size={52} /></div>
        <div style={{ fontFamily: 'var(--font-display)' }} className="text-[22px] mb-1">
          पुणे SIM
        </div>
        <div className="text-[12px] text-[var(--color-ink-dim)]">{msg}</div>
      </Panel>
    </div>
  )
}

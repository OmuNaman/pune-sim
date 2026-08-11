import { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'

import { api } from '../api/client'
import type { LiveStatus, RunMeta } from '../api/types'
import { Panel, StampButton } from '../components/Panel'

/**
 * The other clock.
 *
 * World time — scrubbing days that already exist — is instant and lives in the
 * top bar. This is the one that costs something: computing a day the world has
 * never had. At 80 households that is 0.04 seconds and feels continuous; at
 * 12,000 it is 62-86 seconds and feels like ending a turn. Both are honest, and
 * the difference has to be visible rather than hidden behind a spinner that
 * means two completely different things.
 */
export function RunControl({ meta }: { meta: RunMeta }) {
  const qc = useQueryClient()
  const [err, setErr] = useState('')
  const [since, setSince] = useState(0)

  const live = useQuery({
    queryKey: ['worker', meta.id],
    queryFn: () => api.workerEvents(meta.id, since),
    // Poll only while something is actually happening. A finished run answers
    // this once and then never again.
    refetchInterval: (q) => {
      const s = q.state.data?.status?.status
      return s && ['running', 'starting', 'building', 'pausing', 'stopping',
                   'resuming'].includes(s) ? 700 : false
    },
  })

  const st: LiveStatus = live.data?.status ?? {}
  const running = st.status === 'running'
  const busy = ['starting', 'building', 'resuming', 'pausing', 'stopping'].includes(
    st.status ?? '')
  const computing = running || busy
  const dayDone = st.day ?? meta.days_done - 1
  const nextDay = dayDone + 2   // 1-based, for people
  const atEnd = st.status === 'finished' || dayDone + 1 >= meta.days_planned

  // A finished day means new events and a longer world: everything cached about
  // this run is now short by a day.
  useEffect(() => {
    const msgs = live.data?.messages ?? []
    if (!msgs.length) return
    setSince(Math.max(...msgs.map((m) => m._seq)))
    if (msgs.some((m) => m.kind === 'day' || m.kind === 'finished')) {
      qc.invalidateQueries({ queryKey: ['meta', meta.id] })
      qc.invalidateQueries({ queryKey: ['days', meta.id] })
      qc.invalidateQueries({ queryKey: ['ticker', meta.id] })
    }
  }, [live.data?.messages, meta.id, qc])

  const act = (fn: () => Promise<unknown>) => {
    setErr('')
    fn().then(() => live.refetch()).catch((e: Error) => setErr(e.message))
  }

  if (!meta.managed) {
    return (
      <Panel className="absolute top-[76px] left-1/2 -translate-x-1/2 z-20 px-3 py-1.5"
             plain>
        <div className="text-[11px] text-[var(--color-ink-dim)]">
          a run from the command line — readable, not drivable
        </div>
      </Panel>
    )
  }

  return (
    <Panel className="absolute top-[76px] left-1/2 -translate-x-1/2 z-20 px-3 py-2 min-w-[420px]"
           plain>
      <div className="flex items-center gap-3">
        <div className="survey text-[9px] text-[var(--color-ink-faint)] w-[52px]">
          world
        </div>
        <div className="flex-1 text-[12px]">
          {computing ? (
            <span className="flex items-center gap-2">
              <Spinner />
              <b>computing day {nextDay}</b>
              {st.last_day_wall != null && (
                <span className="text-[var(--color-ink-faint)] tnum">
                  last day took {st.last_day_wall}s
                </span>
              )}
              {busy && st.detail && (
                <span className="text-[var(--color-ink-faint)]">{st.detail}</span>
              )}
            </span>
          ) : atEnd ? (
            <span className="text-[var(--color-ink-dim)]">
              all {meta.days_planned} days computed — branch to go further
            </span>
          ) : (
            <span className="text-[var(--color-ink-dim)]">
              {meta.days_done} of {meta.days_planned} days computed
            </span>
          )}
        </div>

        <div className="flex gap-1.5">
          {running ? (
            <StampButton onClick={() => act(() => api.pause(meta.id))}>
              ⏸ pause
            </StampButton>
          ) : (
            <StampButton primary disabled={atEnd || busy}
                         onClick={() => act(() => api.play(meta.id))}>
              ▶ compute
            </StampButton>
          )}
          <StampButton disabled={atEnd || computing}
                       onClick={() => act(() => api.step(meta.id))}>
            ⏭ one day
          </StampButton>
        </div>
      </div>

      {st.status === 'pausing' && (
        <Note>
          finishing day {nextDay} first — a day is the only moment the world is
          whole, so pause waits for it
        </Note>
      )}
      {st.status === 'error' && <Note bad>{st.error}</Note>}
      {err && <Note bad>{err}</Note>}
    </Panel>
  )
}

function Note({ children, bad = false }: { children: React.ReactNode; bad?: boolean }) {
  return (
    <div className={`text-[11px] mt-1.5 pt-1.5 border-t border-[var(--color-line)]
                     ${bad ? 'text-[var(--color-danger)]' : 'text-[var(--color-ink-dim)]'}`}>
      {children}
    </div>
  )
}

function Spinner() {
  return (
    <span className="inline-block w-[9px] h-[9px] border-[1.5px] rounded-full
                     border-[var(--color-haldi)] border-t-transparent
                     animate-spin" />
  )
}

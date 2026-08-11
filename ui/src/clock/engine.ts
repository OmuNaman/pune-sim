/**
 * The sim clock: fetch keyframes, interpolate between them, tick at 60fps.
 *
 * Deliberately not React. Position updates happen every animation frame and
 * React's job is panels — putting 49,578 people through a reconciler sixty
 * times a second is how the old viewer ended up creating a DOM node per agent.
 * This owns a mutable buffer, writes into it in place, and tells exactly one
 * subscriber (the map) that it changed.
 *
 * Keyframes are on the sim's own 5-minute tick grid (288/day), which is also
 * the grid `presence_intervals` is built on, so a keyframe boundary is always
 * a moment the log can actually answer about.
 */

import { api } from '../api/client'
import type { Frame } from '../api/types'

export const TICK_S = 300
export const DAY_S = 86_400
const CACHE_MAX = 64        // ~28 MB of frames at 49.5k people
const PREFETCH_AHEAD = 6    // ticks pulled in the direction of travel

export type ClockListener = (t: number) => void

function snapDown(t: number) { return Math.floor(t / TICK_S) * TICK_S }

export class ClockEngine {
  runId = ''
  n = 0
  /** interleaved lon,lat — the live buffer the map reads every frame */
  coords = new Float32Array(0)
  codes = new Uint8Array(0)

  t = 0                 // current sim time, seconds
  speed = 60            // sim-seconds per wall-second
  playing = false
  ready = false

  private frames = new Map<number, Frame>()
  private pending = new Map<number, Promise<Frame | null>>()
  private raf = 0
  private lastWall = 0
  private listeners = new Set<ClockListener>()

  /** Returns an unsubscribe with a void return, so it can be a useEffect cleanup. */
  subscribe(fn: ClockListener): () => void {
    this.listeners.add(fn)
    return () => { this.listeners.delete(fn) }
  }

  private emit() { for (const fn of this.listeners) fn(this.t) }

  async open(runId: string, n: number, t: number) {
    this.pause()
    this.runId = runId
    this.n = n
    this.coords = new Float32Array(n * 2)
    this.codes = new Uint8Array(n)
    this.frames.clear()
    this.pending.clear()
    this.ready = false
    this.t = t
    await this.frame(snapDown(t))
    this.apply()
    this.ready = true
    this.emit()
  }

  private async frame(tick: number): Promise<Frame | null> {
    const hit = this.frames.get(tick)
    if (hit) return hit
    const inflight = this.pending.get(tick)
    if (inflight) return inflight
    const runId = this.runId
    const p = api.positions(runId, tick)
      .then((f) => {
        // A frame that arrives after the user switched runs is not this world's.
        if (this.runId !== runId) return null
        this.frames.set(tick, f)
        if (this.frames.size > CACHE_MAX) {
          // Evict furthest from the playhead, not oldest: scrubbing back and
          // forth over the same hour should not keep throwing away the hour.
          let worst = -1, worstD = -1
          for (const k of this.frames.keys()) {
            const d = Math.abs(k - this.t)
            if (d > worstD) { worstD = d; worst = k }
          }
          if (worst >= 0) this.frames.delete(worst)
        }
        return f
      })
      .catch(() => null)
      .finally(() => this.pending.delete(tick))
    this.pending.set(tick, p)
    return p
  }

  /** Write the interpolated positions for `this.t` into the live buffer. */
  private apply() {
    const a = this.frames.get(snapDown(this.t))
    // Not cached yet. The buffer keeps whatever it last held, which is correct
    // (better a slightly old frame than a blank city) — but the arrival of the
    // real frame has to repaint, or the map shows the moment you seeked FROM
    // for ever. That was a whole day of the sim rendered as one frozen 08:00:
    // 22,703 children still at school at ten at night.
    if (!a) {
      void this.frame(snapDown(this.t)).then((f) => {
        if (f && snapDown(this.t) === f.t) { this.apply(); this.emit() }
      })
      return
    }
    const b = this.frames.get(snapDown(this.t) + TICK_S)
    const frac = b ? (this.t - a.t) / TICK_S : 0
    const { coords, codes } = this
    if (!b || frac <= 0) {
      coords.set(a.coords)
      codes.set(a.codes)
      return
    }
    const ac = a.coords, bc = b.coords
    for (let i = 0; i < coords.length; i++) {
      const x = ac[i], y = bc[i]
      // A person who was unplaceable in one frame should not be lerped toward
      // NaN and vanish from both; hold the frame that knows where they are.
      coords[i] = Number.isNaN(x) ? y : Number.isNaN(y) ? x : x + (y - x) * frac
    }
    // Activity is a step function, not a ramp: you are at work or you are not.
    codes.set(a.codes)
  }

  private prefetch(dir: number) {
    const base = snapDown(this.t)
    for (let i = 0; i <= PREFETCH_AHEAD; i++) {
      const tick = base + (dir >= 0 ? i : -i) * TICK_S
      if (tick >= 0 && !this.frames.has(tick) && !this.pending.has(tick)) {
        void this.frame(tick)
      }
    }
    if (!this.frames.has(base + TICK_S)) void this.frame(base + TICK_S)
  }

  seek(t: number, maxT: number) {
    const dir = t >= this.t ? 1 : -1
    this.t = Math.max(0, Math.min(t, maxT))
    this.apply()
    this.prefetch(dir)
    this.emit()
  }

  play(maxT: number) {
    if (this.playing) return
    this.playing = true
    this.lastWall = performance.now()
    const step = (now: number) => {
      if (!this.playing) return
      const dt = (now - this.lastWall) / 1000
      this.lastWall = now
      const next = this.t + dt * this.speed
      if (next >= maxT) {
        this.t = maxT
        this.apply()
        this.emit()
        this.pause()
        return
      }
      this.t = next
      this.apply()
      this.prefetch(1)
      this.emit()
      this.raf = requestAnimationFrame(step)
    }
    this.raf = requestAnimationFrame(step)
  }

  pause() {
    this.playing = false
    if (this.raf) cancelAnimationFrame(this.raf)
    this.raf = 0
  }

  toggle(maxT: number) { this.playing ? this.pause() : this.play(maxT) }
}

export const clock = new ClockEngine()

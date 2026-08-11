/**
 * Sim time, in the sim's own terms.
 *
 * Canonical time is seconds since 2026-01-01 00:00 IST (kernel/timebase.py).
 * The old UI hardcoded a weekday array to work out what day of the week day 0
 * was, which is a fact about the epoch and drifts the moment the epoch moves.
 * The API sends the epoch; everything here derives from it.
 */

export const DAY_S = 86_400

let epochMs = Date.UTC(2026, 0, 1) - 5.5 * 3600 * 1000  // IST midnight, until told

export function setEpoch(iso: string) {
  const parsed = Date.parse(iso)
  if (!Number.isNaN(parsed)) epochMs = parsed
}

export function simDate(t: number): Date {
  return new Date(epochMs + t * 1000)
}

const IST = 'Asia/Kolkata'

export function clockHM(t: number): string {
  return simDate(t).toLocaleTimeString('en-GB', {
    hour: '2-digit', minute: '2-digit', timeZone: IST, hour12: false,
  })
}

export function longDate(t: number): string {
  return simDate(t).toLocaleDateString('en-GB', {
    weekday: 'short', day: '2-digit', month: 'short', year: 'numeric', timeZone: IST,
  })
}

export function dayOf(t: number): number {
  return Math.floor(t / DAY_S)
}

/** "2h 14m" — for how long a run has been computing, and how long a stay was. */
export function duration(seconds: number): string {
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  return h ? `${h}h ${m}m` : `${m}m`
}

export function compact(n: number): string {
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M`
  if (n >= 1e3) return `${(n / 1e3).toFixed(n >= 1e4 ? 0 : 1)}k`
  return String(n)
}

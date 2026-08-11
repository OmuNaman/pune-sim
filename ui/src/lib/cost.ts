/**
 * What a day of simulation actually costs, in seconds.
 *
 * Measured, not guessed: these are the rungs from docs/perf/scale-probe.md and
 * the V3 soak, zero-LLM, one process per rung. They exist so the UI can price a
 * request BEFORE the user makes it — a dialog that hides this lets you ask for
 * two and a half hours of compute by dragging a slider.
 *
 * kasba and oldcity are separate curves on purpose: kasba's 124-place world is
 * a deliberate worst case for crowding and does not route, oldcity has 438
 * places and walks the road graph.
 */

const RUNGS: Record<string, [people: number, seconds: number][]> = {
  kasba: [[306, 0.042], [1266, 0.314], [5000, 3.23], [11240, 14.58]],
  oldcity: [[12438, 22.2], [24716, 35.7], [49578, 86.1]],
}

/** People per household, from the fitted demography (49,578 / 12,000). */
export const PEOPLE_PER_HOUSEHOLD = 4.13

export function secondsPerDay(block: string, people: number): number {
  const rungs = RUNGS[block] ?? RUNGS.kasba
  if (people <= rungs[0][0]) return rungs[0][1] * (people / rungs[0][0])
  for (let i = 1; i < rungs.length; i++) {
    const [p0, s0] = rungs[i - 1]
    const [p1, s1] = rungs[i]
    if (people <= p1) return s0 + ((people - p0) / (p1 - p0)) * (s1 - s0)
  }
  // Past the largest size actually measured. The day pipeline was n^1.86 before
  // the co-presence fix and is near-linear after, so this is a floor rather
  // than a forecast — and callers say so.
  const [pl, sl] = rungs[rungs.length - 1]
  return sl * (people / pl) ** 1.2
}

export function beyondMeasured(block: string, people: number): boolean {
  const rungs = RUNGS[block] ?? RUNGS.kasba
  return people > rungs[rungs.length - 1][0]
}

/** Seconds per sim-day for an existing run, from its own size. */
export function runSecondsPerDay(meta: { block: string; households: number }): number {
  return secondsPerDay(meta.block, Math.round(meta.households * PEOPLE_PER_HOUSEHOLD))
}

/**
 * $0.0031 per sim-day measured at 12,000 households with the spotlight gate at
 * k=5. Spend follows ATTENTION rather than population — V1 measured $0.0029 at
 * 80 households, so a hundred and fifty times the people cost seven per cent
 * more — which is why households do not appear in this at all.
 */
export function modelSpend(days: number, k: number): number {
  return days * 0.0031 * (k / 5)
}

/**
 * Which lane an event belongs to, and therefore what colour it is.
 *
 * The sim has always had this vocabulary — the old viewer's CSS carried the
 * same semantic palette, commented "haldi" and "whisper green". What it did
 * NOT have was one place that decides; the mapping was duplicated between the
 * server's prose and the client's colour table, and drifted.
 */

export const LANES = {
  trip: 'var(--color-trip)',
  scene: 'var(--color-scene)',
  rumor: 'var(--color-rumor)',
  danger: 'var(--color-danger)',
  money: 'var(--color-mood)',
  phone: 'var(--color-phone)',
  memory: 'var(--color-memory)',
  other: 'var(--color-ink-faint)',
} as const

export type Lane = keyof typeof LANES

export function laneOf(type: string): Lane {
  if (type.startsWith('trip.') || type.startsWith('activity.')) return 'trip'
  if (type.startsWith('scene.') || type === 'conversation.held'
      || type === 'plan.revised') return 'scene'
  if (type.startsWith('info.') || type === 'belief.action'
      || type === 'plan.avoided') return 'rumor'
  if (type.startsWith('hazard.') || type.startsWith('hospital.')
      || type.startsWith('unrest.') || type.startsWith('police.')
      || type.startsWith('crowd.') || type.startsWith('curfew.')
      || type === 'ambulance.dispatched' || type === 'condition.set'
      || type === 'fir.update') return 'danger'
  if (type.startsWith('money.') || type.startsWith('loan.')) return 'money'
  if (type === 'message.sent') return 'phone'
  if (type === 'memory.formed' || type === 'mood.delta'
      || type === 'pressure.crossed') return 'memory'
  return 'other'
}

export const LANE_COLOR = (type: string) => LANES[laneOf(type)]

/** Human labels, for the filter chips. */
export const LANE_LABEL: Record<Lane, string> = {
  trip: 'trips',
  scene: 'scenes',
  rumor: 'rumours',
  danger: 'trouble',
  money: 'money',
  phone: 'messages',
  memory: 'inner life',
  other: 'other',
}

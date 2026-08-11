export type RunStatus =
  | 'created' | 'running' | 'paused' | 'stopped' | 'finished' | 'error'

export interface RunSummary {
  id: string
  name: string
  managed: boolean
  status: RunStatus
  computing_day: number | null
  seed: number
  block: string
  households: number
  days_planned: number
  days_done: number
  events: number
  last_seq: number
  created_at: number
  parent_id: string | null
  parent_day: number | null
  what_if: string
  size_bytes: number
}

export interface RunMeta extends RunSummary {
  /** 0 until the population has actually been synthesized — see world_ready. */
  people: number
  /** False while the roster is still being built; the map draws regardless. */
  world_ready: boolean
  world_building: boolean
  max_t: number
  routed: boolean
  bounds: [[number, number], [number, number]] | null
  epoch: string
}

export interface PersonRow {
  id: string
  ord: number
  name: string
  age: number
  sex: string
  occupation: string
  religion: string
  household: string
  home: string
  work: string | null
  work_name: string
}

export interface EventRefs {
  person_ids: string[]
  place_ids: string[]
}

export interface EventLine {
  seq: number
  t: number
  day: number
  hm: string
  type: string
  caused_by: number | null
  provenance?: string
  place?: string | null
  text: string
  refs: EventRefs
}

export interface PlaceRow {
  id: string
  name: string
  kind: string
  lat: number
  lon: number
}

export interface DaySummary {
  day: number
  total: number
  notable: number
  by_type: Record<string, number>
}

/** One decoded positions frame: parallel arrays indexed by person ordinal. */
export interface Frame {
  t: number
  n: number
  /** lon, lat interleaved */
  coords: Float32Array
  /** activity code, see api/positions.py ACTIVITY_CODES */
  codes: Uint8Array
}

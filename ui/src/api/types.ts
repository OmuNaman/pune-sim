export type RunStatus =
  | 'created' | 'building' | 'starting' | 'running' | 'pausing' | 'paused'
  | 'stopping' | 'stopped' | 'finished' | 'error' | 'resuming' | 'resumed'

/** What the worker process is doing right now. Empty for a run nobody is computing. */
export interface LiveStatus {
  run_id?: string
  status?: RunStatus
  /** Last COMPLETED day, or null before the first one lands. */
  day?: number | null
  days?: number
  detail?: string
  error?: string
  events?: number
  last_seq?: number
  /** Wall seconds the last day took — the honest cost of one more. */
  last_day_wall?: number | null
  alive?: boolean
  seq?: number
}

export interface WorkerMessage {
  kind: 'status' | 'day' | 'inject' | 'finished' | 'stopped' | 'error'
  _seq: number
  [k: string]: unknown
}

export interface NewRunBody {
  name?: string
  block: string
  households: number
  days: number
  seed?: number | null
  hazards: boolean
  scenes: boolean
  k?: number
  follow?: string[]
  autostart?: boolean
}

export interface BranchBody {
  name?: string
  what_if?: string
  from_day?: number | null
  add_days?: number
  injections?: InjectionBody[]
}

export interface BranchResult {
  id: string
  fork_day: number
  /** How many days the branch must re-simulate before it can differ. */
  replays_days: number
  days: number
  inherited: number
  added: number
  live: LiveStatus
  note: string
}

export interface DiffReport {
  identical: boolean
  a: { id: string; name: string; events: number }
  b: { id: string; name: string; events: number }
  branch_point: { day: number; hm: string; what: string } | null
  first_divergence: { day: number; hm: string; a: string; b: string } | null
  people_changed: number
  /** The decoherence curve: how far apart the worlds drift, by day. */
  by_day_changed: { day: number; n: number }[]
  reconverged_day: number | null
  type_deltas: Record<string, number>
  rumor_deltas: { key: string; reach_a: number; reach_b: number }[]
  only_in_a: unknown[]
  only_in_b: unknown[]
  headline: string[]
}

export interface InjectionBody {
  day: number
  time: string
  type: string
  place?: string | null
  participants?: string[]
  severity?: number | null
  payload?: Record<string, unknown>
}

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

export interface PlaceDetail extends PlaceRow {
  here: { id: string; name: string; activity: string }[]
  here_n: number
  today: EventLine[]
}

export interface HeardClaim {
  t: number
  hm: string
  text: string
  key: string
  credence: number | null
  channel: string
  source_id: string | null
  source: string | null
  hop: number
  ops: string[]
}

export interface PersonDossier {
  id: string
  ord: number
  name: string
  age: number
  sex: string
  occupation: string
  religion: string
  household: string
  members: { id: string; name: string; age: number; occupation: string }[]
  home: string
  home_name: string
  work: string | null
  work_name: string
  memories: { t: number; summary: string; salience: number }[]
  moods: { t: number; dim: string; delta: number }[]
  timeline: EventLine[]
  interviews: { t: number; hm: string; question: string; answer: string }[]
  heard: HeardClaim[]
  trips: {
    t0: number; t1: number; kind: string
    a: string; a_name: string; b: string | null; b_name: string
    activity: string | null
  }[]
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

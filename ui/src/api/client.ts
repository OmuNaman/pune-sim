import type {
  BranchBody, BranchResult, DaySummary, DiffReport, EventLine, Frame,
  InjectionBody, LiveStatus, NewRunBody, PersonDossier, PersonRow, PlaceDetail,
  PlaceRow, RunMeta, RunSummary, WorkerMessage,
} from './types'

async function json<T>(url: string): Promise<T> {
  const r = await fetch(url)
  if (!r.ok) throw new Error(`${r.status} ${r.statusText} — ${url}`)
  return r.json() as Promise<T>
}

async function post<T>(url: string, body?: unknown): Promise<T> {
  const r = await fetch(url, {
    method: 'POST',
    headers: body ? { 'content-type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  })
  const data = await r.json().catch(() => ({}))
  // The API says WHY in `detail` — "day 3 has already been computed", "branch
  // it to get one that can". Losing that and showing a status code instead is
  // how a UI turns a good explanation into a shrug.
  if (!r.ok) throw new Error((data as any)?.detail ?? `${r.status} ${r.statusText}`)
  return data as T
}

export const api = {
  runs: () => json<{ runs: RunSummary[] }>('/api/runs'),
  meta: (id: string) => json<RunMeta>(`/api/runs/${id}/meta`),

  create: (body: NewRunBody) => post<{ id: string; live: LiveStatus }>('/api/runs', body),
  play: (id: string) => post<LiveStatus>(`/api/runs/${id}/play`),
  pause: (id: string) => post<LiveStatus>(`/api/runs/${id}/pause`),
  step: (id: string, days = 1) => post<LiveStatus>(`/api/runs/${id}/step?days=${days}`),
  stopRun: (id: string, force = false) =>
    post<LiveStatus>(`/api/runs/${id}/stop?force=${force}`),
  inject: (id: string, body: InjectionBody) =>
    post<{ queued: boolean; live: boolean; day: number }>(`/api/runs/${id}/inject`, body),

  workerEvents: (id: string, since = 0) =>
    json<{ status: LiveStatus; messages: WorkerMessage[] }>(
      `/api/runs/${id}/events?since=${since}`),

  branch: (id: string, body: BranchBody) =>
    post<BranchResult>(`/api/runs/${id}/branch`, body),

  diff: (a: string, b: string) => post<DiffReport>('/api/diff', { a, b }),

  roster: (id: string) =>
    json<{ order: string[]; names: string[] }>(`/api/runs/${id}/roster`),

  people: (id: string, q = '', offset = 0, limit = 200) =>
    json<{ total: number; offset: number; items: PersonRow[] }>(
      `/api/runs/${id}/people?q=${encodeURIComponent(q)}&offset=${offset}&limit=${limit}`),

  places: (id: string) => json<PlaceRow[]>(`/api/runs/${id}/places`),

  // Ids carry slashes — `place:way/264276391` — and the route is declared as
  // `{place_id:path}` so the server wants them raw. Person ids are safe but go
  // through the same shape for consistency.
  place: (id: string, placeId: string, t: number) =>
    json<PlaceDetail>(`/api/runs/${id}/place/${placeId}?t=${Math.floor(t)}`),

  person: (id: string, pid: string, day: number) =>
    json<PersonDossier>(`/api/runs/${id}/person/${encodeURIComponent(pid)}?day=${day}`),

  geo: (id: string, layer: 'buildings' | 'roads') =>
    json<GeoJSON.FeatureCollection>(`/api/runs/${id}/geo/${layer}`),

  days: (id: string) => json<DaySummary[]>(`/api/runs/${id}/days`),

  ticker: (id: string, sinceSeq = 0, limit = 500, day?: number) =>
    json<{ items: EventLine[]; last_seq: number }>(
      `/api/runs/${id}/ticker?since_seq=${sinceSeq}&limit=${limit}` +
      (day === undefined ? '' : `&day=${day}`)),

  /** The binary frame. See api/positions.py for the layout. */
  async positions(id: string, t: number, signal?: AbortSignal): Promise<Frame> {
    const r = await fetch(`/api/runs/${id}/positions?t=${t}`, { signal })
    if (!r.ok) throw new Error(`${r.status} positions t=${t}`)
    return decodeFrame(await r.arrayBuffer())
  },
}

const MAGIC = 0x4f505350 // 'PSPO' little-endian

/** Zero-copy: the typed arrays are views onto the response buffer itself. */
export function decodeFrame(buf: ArrayBuffer): Frame {
  const head = new DataView(buf)
  if (head.getUint32(0, true) !== MAGIC) throw new Error('not a positions frame')
  const version = head.getUint16(4, true)
  if (version !== 1) throw new Error(`positions version ${version}, reader speaks 1`)
  const n = head.getUint32(8, true)
  const t = head.getUint32(12, true)
  return {
    t, n,
    coords: new Float32Array(buf, 16, n * 2),
    codes: new Uint8Array(buf, 16 + n * 8, n),
  }
}

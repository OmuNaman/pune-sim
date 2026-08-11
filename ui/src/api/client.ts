import type {
  DaySummary, EventLine, Frame, PersonRow, PlaceRow, RunMeta, RunSummary,
} from './types'

async function json<T>(url: string): Promise<T> {
  const r = await fetch(url)
  if (!r.ok) throw new Error(`${r.status} ${r.statusText} — ${url}`)
  return r.json() as Promise<T>
}

export const api = {
  runs: () => json<{ runs: RunSummary[] }>('/api/runs'),
  meta: (id: string) => json<RunMeta>(`/api/runs/${id}/meta`),

  roster: (id: string) =>
    json<{ order: string[]; names: string[] }>(`/api/runs/${id}/roster`),

  people: (id: string, q = '', offset = 0, limit = 200) =>
    json<{ total: number; offset: number; items: PersonRow[] }>(
      `/api/runs/${id}/people?q=${encodeURIComponent(q)}&offset=${offset}&limit=${limit}`),

  places: (id: string) => json<PlaceRow[]>(`/api/runs/${id}/places`),

  geo: (id: string, layer: 'buildings' | 'roads') =>
    json<GeoJSON.FeatureCollection>(`/api/runs/${id}/geo/${layer}`),

  days: (id: string) => json<DaySummary[]>(`/api/runs/${id}/days`),

  ticker: (id: string, sinceSeq = 0, limit = 500) =>
    json<{ items: EventLine[]; last_seq: number }>(
      `/api/runs/${id}/ticker?since_seq=${sinceSeq}&limit=${limit}`),

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

/**
 * The map. Mounted once, never re-rendered by React.
 *
 * The agent layer is fed straight from the clock engine's mutable buffer as a
 * deck.gl binary attribute — no array of objects is built per frame, no React
 * state changes, and the 49,578 positions go to the GPU as the same Float32Array
 * the decoder wrote. `updateTriggers` on a frame counter is what tells deck the
 * buffer's contents moved.
 */

import { useEffect, useRef } from 'react'
import maplibregl from 'maplibre-gl'
import { MapboxOverlay } from '@deck.gl/mapbox'
import { ScatterplotLayer, TextLayer } from '@deck.gl/layers'
import { ScreenGridLayer } from '@deck.gl/aggregation-layers'
import 'maplibre-gl/dist/maplibre-gl.css'

import { api } from '../api/client'
import type { PlaceRow, RunMeta } from '../api/types'
import { clock } from '../clock/engine'
import { geoLayers, paperStyle } from './paperStyle'
import { useSelection } from '../stores/selection'

// Activity code -> dot colour. Codes are api/positions.py ACTIVITY_CODES.
//
// The point of the colours is to make the CITY's rhythm legible at a glance:
// at 08:00 most people are still at home and that has to read as a calm mass
// rather than as noise, while the few already moving must pop. So home is a
// soft warm sepia close to the paper, and everything that is out in the world
// gets a saturated lane colour.
const CODE_RGB: [number, number, number][] = [
  [176, 154, 116],  // 0 home — warm sepia, near the paper
  [37, 99, 201],    // 1 transit — trip blue
  [43, 36, 64],     // 2 work — ink
  [124, 77, 196],   // 3 school — scene violet
  [232, 143, 41],   // 4 market/errand — haldi
  [30, 143, 90],    // 5 worship — whisper green
  [192, 48, 40],    // 6 hospital — danger
  [194, 68, 126],   // 7 social — memory pink
  [150, 143, 128],  // 8 other
]
// Being at home is the default state of most people most of the time; drawing
// it at full strength buries the eleven interesting things happening in the
// city under forty thousand identical dots.
const CODE_ALPHA = [110, 255, 235, 235, 245, 245, 255, 245, 150]

const DOT_ZOOM = 14.2   // below this, density; above it, individual people

export function MapRoot({ meta }: { meta: RunMeta }) {
  const host = useRef<HTMLDivElement>(null)
  const mapRef = useRef<maplibregl.Map | null>(null)
  const deckRef = useRef<MapboxOverlay | null>(null)
  const placesRef = useRef<PlaceRow[]>([])
  const zoomRef = useRef(14)
  const versionRef = useRef(0)
  const colourRef = useRef(new Uint8Array(0))
  const select = useSelection((s) => s.select)

  /** Packed RGBA, rebuilt in place from the clock's activity codes. */
  const colours = () => {
    const n = clock.n
    if (colourRef.current.length !== n * 4) colourRef.current = new Uint8Array(n * 4)
    const out = colourRef.current, codes = clock.codes
    for (let i = 0; i < n; i++) {
      const k = codes[i]
      const c = CODE_RGB[k] ?? CODE_RGB[8]
      out[i * 4] = c[0]; out[i * 4 + 1] = c[1]; out[i * 4 + 2] = c[2]
      out[i * 4 + 3] = CODE_ALPHA[k] ?? 200
    }
    return out
  }

  // -- build the layer set from current buffers. Called on every clock tick,
  //    so it must allocate as little as possible.
  const buildLayers = () => {
    const n = clock.n
    const zoomed = zoomRef.current >= DOT_ZOOM
    const layers: any[] = []

    if (n > 0 && zoomed) {
      layers.push(new ScatterplotLayer({
        id: 'agents',
        // Both position AND colour are binary. Mixing a binary getPosition with
        // an accessor-function getFillColor does not work: deck builds the
        // colour attribute by iterating `data`, and `data` here is a length,
        // not an array — so the layer had 49,578 positions and no colours, and
        // drew nothing at all. colours() keeps a packed Uint8 buffer in step.
        data: {
          length: n,
          attributes: {
            getPosition: { value: clock.coords, size: 2 },
            getFillColor: { value: colours(), size: 4, normalized: true },
          },
        },
        radiusUnits: 'pixels',
        getRadius: 2.2,
        radiusMinPixels: 1.3,
        radiusMaxPixels: 4.5,
        opacity: 1,          // per-dot alpha is in the colour buffer
        stroked: false,
        pickable: true,
        onClick: (info: any) => {
          if (info.index >= 0) select({ kind: 'person-ord', ord: info.index })
        },
      }))
    } else if (n > 0) {
      layers.push(new ScreenGridLayer({
        id: 'density',
        data: { length: n, attributes: { getPosition: { value: clock.coords, size: 2 } } },
        cellSizePixels: 18,
        colorRange: [
          [242, 227, 191, 90], [238, 206, 140, 140], [232, 163, 61, 180],
          [214, 121, 60, 210], [198, 93, 59, 235], [160, 60, 45, 250],
        ],
        gpuAggregation: true,
        opacity: 0.7,
      }))
    }

    if (placesRef.current.length && zoomRef.current >= 15.4) {
      layers.push(new TextLayer({
        id: 'place-labels',
        data: placesRef.current,
        getPosition: (p: PlaceRow) => [p.lon, p.lat],
        getText: (p: PlaceRow) => p.name,
        getSize: 10,
        sizeUnits: 'pixels',
        getColor: [43, 36, 64, 205],
        outlineWidth: 3,
        outlineColor: [246, 240, 225, 230],
        fontSettings: { sdf: true },
        fontFamily: 'Mukta, system-ui, sans-serif',
        getTextAnchor: 'start',
        getAlignmentBaseline: 'center',
        getPixelOffset: [7, 0],
        pickable: true,
        onClick: (info: any) => info.object && select({ kind: 'place', id: info.object.id }),
      }))
      layers.push(new ScatterplotLayer({
        id: 'place-dots',
        data: placesRef.current,
        getPosition: (p: PlaceRow) => [p.lon, p.lat],
        getFillColor: [43, 36, 64, 220],
        getRadius: 2.4,
        radiusUnits: 'pixels',
        pickable: true,
        onClick: (info: any) => info.object && select({ kind: 'place', id: info.object.id }),
      }))
    }
    return layers
  }

  const redraw = () => {
    versionRef.current++
    const layers = buildLayers()
    ;(window as any).__layers = layers.map((l: any) => ({
      id: l.id, n: l.props?.data?.length ?? null, zoom: zoomRef.current,
    }))
    deckRef.current?.setProps({ layers })
  }

  useEffect(() => {
    if (!host.current || mapRef.current) return
    const b = meta.bounds
    const map = new maplibregl.Map({
      container: host.current,
      style: paperStyle(),
      center: b ? [(b[0][1] + b[1][1]) / 2, (b[0][0] + b[1][0]) / 2] : [73.856, 18.519],
      zoom: 14.6,
      minZoom: 12,
      maxZoom: 19,
      attributionControl: { customAttribution: '© OpenStreetMap contributors (ODbL)' },
    })
    mapRef.current = map
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'bottom-right')

    const overlay = new MapboxOverlay({ interleaved: true, layers: [] })
    deckRef.current = overlay
    // Handles for scripts/layers.mjs. A map bug is invisible from the outside —
    // a screenshot of the wrong thing looks exactly like a screenshot.
    ;(window as any).__map = map
    ;(window as any).__deck = overlay
    ;(window as any).__clock = clock

    map.on('load', async () => {
      map.addControl(overlay as unknown as maplibregl.IControl)
      const [buildings, roads, places] = await Promise.all([
        api.geo(meta.id, 'buildings'),
        api.geo(meta.id, 'roads'),
        api.places(meta.id),
      ])
      map.addSource('buildings', { type: 'geojson', data: buildings as never })
      map.addSource('roads', { type: 'geojson', data: roads as never })
      for (const layer of geoLayers()) map.addLayer(layer)
      placesRef.current = places
      if (b) {
        map.fitBounds([[b[0][1], b[0][0]], [b[1][1], b[1][0]]], { padding: 60, duration: 0 })
      }
      // Clicking a building opens the place it is, which is the join the OSM
      // ids give us for free (api/geo.py).
      map.on('click', 'buildings-fill', (e) => {
        const f = e.features?.[0]
        const simId = f?.properties?.sim_id
        if (typeof simId === 'string' && simId.startsWith('place:')) {
          select({ kind: 'place', id: simId })
        }
      })
      map.on('mouseenter', 'buildings-fill', () => { map.getCanvas().style.cursor = 'pointer' })
      map.on('mouseleave', 'buildings-fill', () => { map.getCanvas().style.cursor = '' })
      redraw()
    })

    map.on('zoom', () => { zoomRef.current = map.getZoom(); redraw() })
    const unsub = clock.subscribe(() => redraw())

    return () => {
      unsub()
      map.remove()
      mapRef.current = null
      deckRef.current = null
    }
    // meta.id identifies the world; changing runs remounts the map entirely.
  }, [meta.id])

  // Inline, not a utility class. MapLibre's own stylesheet sets
  // `.maplibregl-map { position: relative }` unlayered, which outranks every
  // Tailwind utility — the container collapsed to 1600x0 and the map rendered
  // into a 300px sliver behind the panels. A style attribute beats both.
  return <div ref={host} style={{ position: 'absolute', inset: 0 }} />
}

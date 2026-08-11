import type { StyleSpecification } from 'maplibre-gl'

/**
 * The basemap, written by hand over our own vendored GeoJSON.
 *
 * No tile server, no CDN, no network at all — the old viewer pulled CARTO
 * raster tiles from the internet, which meant the map of a city whose geometry
 * is checksummed into this repo could not be drawn offline, and could not be
 * styled at all. Raster tiles cannot tint a peth or fade a footway.
 *
 * Sources are attached at runtime from /api/runs/{id}/geo/{layer}.
 */

const PETH_TINT: [string, string][] = [
  ['kasba', '#ebd8c6'],
  ['shaniwar', '#dcdcec'],
  ['budhwar', '#dde7d4'],
  ['raviwar', '#f2e3bf'],
]

// Buildings are painted by peth, so the districts read as districts. `role`
// then separates the ones the sim actually knows about from the fabric that is
// only there so a street has walls.
function pethColour(): any {
  return ['match', ['get', 'peth'], ...PETH_TINT.flat(), '#e9e2d2']
}

export function paperStyle(): StyleSpecification {
  return {
    version: 8,
    // No `glyphs` key at all: MapLibre validates it as a string when present,
    // and every label here is a deck.gl TextLayer, so the PBF glyph pipeline
    // (and the CDN it would normally point at) is never needed.
    sources: {},
    layers: [
      {
        id: 'ground',
        type: 'background',
        paint: { 'background-color': '#f6f0e1' },
      },
    ],
  }
}

/** Layers added once the geo sources exist. Order is bottom-up. */
export function geoLayers(): any[] {
  return [
    {
      id: 'buildings-fill',
      type: 'fill',
      source: 'buildings',
      paint: {
        'fill-color': pethColour(),
        // fabric recedes; a place the sim knows is worth looking at
        'fill-opacity': [
          'match', ['get', 'role'],
          'place', 0.95,
          'home', 0.75,
          0.55,
        ],
      },
    },
    {
      id: 'buildings-line',
      type: 'line',
      source: 'buildings',
      minzoom: 15.5,
      paint: {
        'line-color': '#c65d3b',
        'line-opacity': ['interpolate', ['linear'], ['zoom'], 15.5, 0, 17, 0.45],
        'line-width': 0.6,
      },
    },
    {
      id: 'roads-casing',
      type: 'line',
      source: 'roads',
      filter: ['<=', ['get', 'rank'], 4],
      paint: {
        'line-color': '#f6f0e1',
        'line-width': ['interpolate', ['exponential', 1.4], ['zoom'],
          13, 2, 18, ['*', 3, ['-', 7, ['get', 'rank']]]],
      },
    },
    {
      id: 'roads',
      type: 'line',
      source: 'roads',
      paint: {
        // Ink at low alpha rather than grey: the lanes should look drawn, and a
        // service road should be fainter than a main road, not just thinner.
        'line-color': '#2b2440',
        'line-opacity': ['interpolate', ['linear'], ['get', 'rank'],
          0, 0.38, 4, 0.28, 7, 0.16],
        'line-width': ['interpolate', ['exponential', 1.4], ['zoom'],
          13, ['max', 0.4, ['-', 2.2, ['*', 0.25, ['get', 'rank']]]],
          18, ['max', 1, ['*', 2.4, ['-', 8, ['get', 'rank']]]]],
        'line-dasharray': ['case', ['>=', ['get', 'rank'], 7], ['literal', [2, 1.5]],
          ['literal', [1, 0]]],
      },
    },
  ]
}

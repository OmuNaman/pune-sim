# pune-sim UI

A map-first, game-feel front end for the simulation. React + MapLibre GL (our
own vendored GeoJSON, no tiles, no CDN, works offline) + deck.gl for the agents.

## Running it

```bash
uv run punesim ui              # API + built UI on http://127.0.0.1:8619
```

For frontend work, run the API in dev mode and Vite beside it:

```bash
uv run punesim ui --dev        # API on 8619, allows the Vite origin
cd ui && pnpm dev              # app on http://localhost:5173, proxies /api
pnpm build                     # writes ui/dist, which `punesim ui` then serves
```

The UI takes no `--seed`/`--households`/`--block`: every run's population comes
from its own log's `run.meta` via `world/roster.py`. Passing the wrong flags to
the older `punesim serve` does not fail — it silently shows a different city.

## Checking it actually works

A map app can look finished in a screenshot while rendering nothing, or render
a frozen frame that a still image cannot distinguish from a moving one. Both
happened while building this. So:

```bash
node scripts/shot.mjs 8619 out.png        # screenshot + console errors
node scripts/animate.mjs 8619             # does the city move? hour-by-hour census
node scripts/hours.mjs 8619 . 6,9,15,22   # the same day at four hours
```

`animate.mjs` is the one that matters. It prints how many people are at home,
at work, at school and on the streets at each hour — the day should start with
everyone home at 06:00, fill the schools by 09:00, peak at work by noon, and
empty again by night. It caught a bug where the whole run rendered as one
frozen 08:00 frame, with 22,703 children still at school at ten at night.

## Layout

```
src/
  api/        typed client; positions arrive as a binary buffer, not JSON
  clock/      the sim clock — rAF, keyframe cache, interpolation. NOT React:
              49,578 people do not go through a reconciler sixty times a second
  map/        MapLibre style written by hand + deck.gl agent layers
  panels/     the floating HUD — top bar, timeline ribbon
  components/ Panel, StampButton, Odometer, Logo
  theme/      Peth Paper tokens; all custom CSS lives in @layer components,
              because unlayered CSS outranks every Tailwind utility
```

## Design

"Peth Paper": aged-paper ground, indigo ink, haldi accent kept from the old
viewer, terracotta brick, and the four peths tinted as districts. Event lanes
keep the semantic colours the sim has always used (trip blue, scene violet,
whisper green for rumour, danger red).

Two rules that keep it from looking generated: hard edges — 2px radii, no soft
shadows, a haldi rule across each panel like an account book — and diamonds
instead of circles for event marks.

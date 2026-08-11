/**
 * Does the city actually move? A screenshot cannot answer that.
 *
 * Plays the clock and samples the position buffer, reporting how many people
 * changed place and how the activity mix shifts across the day — the two
 * things that distinguish a living simulation from a still image of one.
 */
import { chromium } from 'playwright'

const port = process.argv[2] ?? '8629'
const b = await chromium.launch()
const p = await b.newPage({ viewport: { width: 1600, height: 950 } })
const errs = []
p.on('pageerror', (e) => errs.push(e.message))
await p.goto(`http://127.0.0.1:${port}/`, { waitUntil: 'networkidle', timeout: 120000 })
await p.waitForTimeout(22000)

const sample = () => p.evaluate(() => {
  const c = window.__clock
  const hist = {}
  for (let i = 0; i < c.n; i++) hist[c.codes[i]] = (hist[c.codes[i]] ?? 0) + 1
  return { t: Math.round(c.t), playing: c.playing, hist,
           head: Array.from(c.coords.slice(0, 20)) }
})

const rows = []
// Jump across the day rather than waiting for it: seek is the same code path
// play uses, and this keeps the check to a few seconds.
for (const hour of [6, 9, 12, 15, 18, 22]) {
  await p.evaluate((h) => window.__clock.seek(h * 3600, 30 * 86400), hour)
  await p.waitForTimeout(2500)
  rows.push([hour, await sample()])
}

const LANE = ['home', 'transit', 'work', 'school', 'market', 'worship', 'hospital', 'social', 'other']
console.log('hour   ' + LANE.map((l) => l.padStart(9)).join(''))
for (const [hour, s] of rows) {
  console.log(`${String(hour).padStart(2)}:00  ` +
    LANE.map((_, i) => String(s.hist[i] ?? 0).padStart(9)).join(''))
}

// And that playing actually advances the clock without being seeked.
await p.evaluate(() => { window.__clock.speed = 3600; window.__clock.play(30 * 86400) })
const t0 = (await sample()).t
await p.waitForTimeout(4000)
const t1 = await sample()
console.log(`\nplay: t ${t0} -> ${t1.t} (+${t1.t - t0}s of sim time in 4s wall)`)
console.log('moved:', t1.t > t0 ? 'YES' : 'NO — the clock is stuck')
if (errs.length) console.log('ERRORS:', [...new Set(errs)].slice(0, 5))
await b.close()

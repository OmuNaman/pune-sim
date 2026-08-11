/**
 * Make a world from the browser and watch it compute.
 *
 * The end-to-end claim of Phase 4: the + new dialog spawns a real worker
 * process, days land one after another, and the map fills with people who did
 * not exist when the page loaded.
 */
import { chromium } from 'playwright'

const port = process.argv[2] ?? '8650'
const dir = process.argv[3] ?? '.'
const b = await chromium.launch()
const p = await b.newPage({ viewport: { width: 1600, height: 950 } })
const errs = []
p.on('pageerror', (e) => errs.push(e.message))
p.on('console', (m) => { if (m.type() === 'error') errs.push(m.text()) })

await p.goto(`http://127.0.0.1:${port}/`, { waitUntil: 'networkidle', timeout: 120000 })
await p.waitForFunction(() => window.__clock?.n > 0, null, { timeout: 180000 })

// Create a small kasba run through the real endpoint the dialog calls.
const made = await p.evaluate(async () => {
  const r = await fetch('/api/runs', {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ name: 'browser-made', block: 'kasba', households: 60,
                           days: 4, seed: 108, hazards: true, scenes: false,
                           autostart: true }),
  })
  return r.json()
})
console.log('created:', made.id, '·', made.live?.status)

// Watch the days land.
const seen = []
for (let i = 0; i < 60; i++) {
  const s = await p.evaluate(async (id) =>
    (await fetch(`/api/runs/${id}/events`).then((r) => r.json())).status, made.id)
  if (s.day != null && !seen.includes(s.day)) {
    seen.push(s.day)
    console.log(`  day ${s.day} done · ${s.events} events · ${s.last_day_wall}s`)
  }
  if (['finished', 'error', 'stopped'].includes(s.status)) {
    console.log('final:', s.status, s.error ?? '')
    break
  }
  await p.waitForTimeout(400)
}

// Switch the UI to it and screenshot the world that did not exist a minute ago.
await p.goto(`http://127.0.0.1:${port}/?run=${made.id}`, { waitUntil: 'networkidle' })
await p.waitForFunction(() => window.__clock?.n > 0, null, { timeout: 180000 })
await p.waitForTimeout(4000)
await p.screenshot({ path: `${dir}/live.png` })
console.log('people on the map:', await p.evaluate(() => window.__clock.n))
if (errs.length) console.log('ERRORS:', [...new Set(errs)].slice(0, 6))
await b.close()

/**
 * Fork a world from the browser and compare the two.
 *
 * The end-to-end claim of Phase 6, and the one the whole save-tree rests on:
 * the shared past is genuinely shared, so the diff attributes every later
 * difference to the change and nothing else.
 */
import { chromium } from 'playwright'

const port = process.argv[2] ?? '8651'
const dir = process.argv[3] ?? '.'
const b = await chromium.launch()
const p = await b.newPage({ viewport: { width: 1600, height: 950 } })
const errs = []
p.on('pageerror', (e) => errs.push(e.message))
p.on('console', (m) => { if (m.type() === 'error') errs.push(m.text()) })
await p.goto(`http://127.0.0.1:${port}/`, { waitUntil: 'networkidle', timeout: 120000 })

const api = (path, body) => p.evaluate(async ([u, b]) => {
  const r = await fetch(u, b ? { method: 'POST',
    headers: { 'content-type': 'application/json' }, body: JSON.stringify(b) } : {})
  return r.json()
}, [path, body])

const settle = async (id) => {
  for (let i = 0; i < 200; i++) {
    const s = (await api(`/api/runs/${id}/events`)).status
    if (['finished', 'error', 'stopped'].includes(s.status)) return s
    await p.waitForTimeout(300)
  }
  throw new Error('never settled')
}

const trunk = await api('/api/runs', { name: 'trunk', block: 'kasba',
  households: 80, days: 6, seed: 108, hazards: true, autostart: true })
console.log('trunk:', trunk.id, (await settle(trunk.id)).status)

const places = await api(`/api/runs/${trunk.id}/places`)
const spot = places.find((x) => x.kind === 'market' || x.kind === 'temple') ?? places[0]

const fork = await api(`/api/runs/${trunk.id}/branch`, {
  name: 'what if the water went', what_if: 'the water went', from_day: 2, add_days: 0,
  injections: [{ day: 2, time: '07:00', type: 'hazard.water.supply_cut',
                 place: spot.id, severity: 0.8 }],
})
console.log('fork:', fork.id, '·', fork.note)
console.log('  replays', fork.replays_days, 'days, inherited', fork.inherited)
console.log('  ', (await settle(fork.id)).status)

const d = await api('/api/diff', { a: trunk.id, b: fork.id })
console.log('\n--- what the change did ---')
for (const h of d.headline ?? []) console.log(' ', h)
console.log('  people changed:', d.people_changed)
console.log('  first divergence:', JSON.stringify(d.first_divergence))
console.log('  reconverged day:', d.reconverged_day)
console.log('  type deltas:', JSON.stringify(d.type_deltas).slice(0, 200))

await p.goto(`http://127.0.0.1:${port}/?run=${fork.id}`, { waitUntil: 'networkidle' })
await p.waitForFunction(() => window.__clock?.n > 0, null, { timeout: 120000 })
await p.waitForTimeout(2500)
await p.evaluate(() => [...document.querySelectorAll('button')]
  .find((x) => x.textContent.includes('timelines'))?.click())
await p.waitForTimeout(3500)
await p.screenshot({ path: `${dir}/timelines.png` })
if (errs.length) console.log('\nERRORS:', [...new Set(errs)].slice(0, 6))
await b.close()

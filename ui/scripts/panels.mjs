/**
 * Do the panels fill with the run's own data?
 *
 * Selects a known heritage place and a real person by id rather than clicking
 * pixels on a WebGL canvas. Waits for the panel to actually settle: an earlier
 * version of this script read the DOM before React had rendered and reported
 * "loading…" three times for a panel that was fine.
 */
import { chromium } from 'playwright'

const port = process.argv[2] ?? '8637'
const dir = process.argv[3] ?? '.'
const b = await chromium.launch()
const p = await b.newPage({ viewport: { width: 1600, height: 950 } })
const errs = []
p.on('pageerror', (e) => errs.push(e.message))
p.on('console', (m) => { if (m.type() === 'error') errs.push(m.text()) })

await p.goto(`http://127.0.0.1:${port}/?run=${process.argv[4] ?? 'soak3'}`, { waitUntil: 'networkidle', timeout: 120000 })
// The roster is the slow part; wait for the clock to actually have people.
await p.waitForFunction(() => window.__clock?.n > 0, null, { timeout: 120000 })
await p.waitForTimeout(2000)

await p.evaluate(() => window.__clock.seek(9 * 86400 + 10.5 * 3600, 30 * 86400))
await p.waitForTimeout(4000)
await p.screenshot({ path: `${dir}/panel-feed.png` })

const panelText = (needle) => p.evaluate((n) => {
  const el = [...document.querySelectorAll('.panel')].find((e) => e.innerText.includes(n))
  return el?.innerText.replace(/\n/g, ' | ').slice(0, 220) ?? `(no ${n} panel)`
}, needle)

console.log('FEED:', await panelText('THE DAY'))

await p.evaluate(() => window.__select({ kind: 'place', id: window.__anyPlace || 'place:way/264276391' }))
await p.waitForTimeout(3500)
await p.screenshot({ path: `${dir}/panel-place.png` })
console.log('\nPLACE:', await panelText('PLACE'))

const pid = await p.evaluate(async () => {
  const id = window.__clock.runId
  if (!id) throw new Error('the clock never opened a run')
  const r = await fetch(`/api/runs/${encodeURIComponent(id)}/roster`).then((x) => x.json())
  return r.order[Math.floor(r.order.length / 3)]
})
await p.evaluate((id) => window.__select({ kind: 'person', id }), pid)
await p.waitForTimeout(3500)
await p.screenshot({ path: `${dir}/panel-person.png` })
console.log('\nPERSON:', await panelText('PERSON'))

if (errs.length) console.log('\nERRORS:', [...new Set(errs)].slice(0, 6))
await b.close()

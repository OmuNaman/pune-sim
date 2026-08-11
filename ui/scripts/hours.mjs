/** Screenshot the same city at several hours, to see the day breathe. */
import { chromium } from 'playwright'

const port = process.argv[2] ?? '8630'
const dir = process.argv[3] ?? '.'
const b = await chromium.launch()
const p = await b.newPage({ viewport: { width: 1600, height: 950 } })
await p.goto(`http://127.0.0.1:${port}/`, { waitUntil: 'networkidle', timeout: 120000 })
await p.waitForTimeout(22000)
for (const h of (process.argv[4] ?? '7,10,14,20').split(',').map(Number)) {
  await p.evaluate((hh) => window.__clock.seek(hh * 3600, 30 * 86400), h)
  await p.waitForTimeout(3000)
  await p.screenshot({ path: `${dir}/h${String(h).padStart(2, '0')}.png` })
  console.log(`h${h}`)
}
await b.close()

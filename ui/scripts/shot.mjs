/**
 * Screenshot the running app, and fail loudly on anything the console reports.
 *
 * A map app can look finished in a screenshot while WebGL is quietly throwing
 * every frame, so this collects console errors and page exceptions and prints
 * them beside the shot. Run against `punesim ui` on the port given.
 *
 *   node scripts/shot.mjs 8622 out.png [waitMs]
 */
import { chromium } from 'playwright'

const port = process.argv[2] ?? '8622'
const out = process.argv[3] ?? 'shot.png'
const wait = Number(process.argv[4] ?? 15000)

const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 1600, height: 950 } })

const problems = []
page.on('console', (m) => {
  if (m.type() === 'error' || m.type() === 'warning') problems.push(`[${m.type()}] ${m.text()}`)
})
page.on('pageerror', (e) => problems.push(`[pageerror] ${e.message}`))
page.on('requestfailed', (r) => problems.push(`[net] ${r.url()} ${r.failure()?.errorText}`))

await page.goto(`http://127.0.0.1:${port}/`, { waitUntil: 'networkidle', timeout: 120000 })
await page.waitForTimeout(wait)
await page.screenshot({ path: out })

// What did the page actually manage to render?
const state = await page.evaluate(() => ({
  canvases: document.querySelectorAll('canvas').length,
  panels: document.querySelectorAll('.panel').length,
  text: document.body.innerText.slice(0, 400),
}))
console.log(JSON.stringify(state, null, 2))
if (problems.length) {
  console.log('\n--- console ---')
  for (const p of [...new Set(problems)].slice(0, 25)) console.log(p)
}
await browser.close()

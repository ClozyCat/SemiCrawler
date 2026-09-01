import { chromium } from 'playwright'

const EXE = `${process.env.LOCALAPPDATA}\\ms-playwright\\chromium-1219\\chrome-win64\\chrome.exe`
const browser = await chromium.launch({ executablePath: EXE })
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } })

const errors = []
page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text().slice(0, 300)) })
page.on('pageerror', (e) => errors.push('PAGEERROR: ' + String(e).slice(0, 300)))

const report = (name, ok, extra = '') =>
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${extra ? '  ·  ' + extra : ''}`)

await page.goto('http://127.0.0.1:5173/', { waitUntil: 'networkidle' })
await page.waitForTimeout(1500)

async function css(sel, prop) {
  return page.$eval(sel, (el, p) => getComputedStyle(el)[p], prop)
}

// ---- Dashboard ----
report('console errors (dashboard)', errors.length === 0, errors.join(' | '))
report('no page horizontal overflow', await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth))
report('.run-body exists', await page.locator('.run-body').count() > 0)
report('.toggle-card count >= 2', await page.locator('.toggle-card').count() >= 2)
report('.run-btn rendered at width', (await page.$eval('.run-btn', (el) => el.getBoundingClientRect().width)) > 300)
report('.metric cards >= 4', await page.locator('.metric').count() >= 4)
report('sidebar dark bg', /gradient/.test(await css('.app-shell aside', 'backgroundImage')))

// ---- History + detail modal ----
await page.getByRole('button', { name: '数据归档', exact: true }).click()
await page.waitForTimeout(1000)
report('history results-head present', await page.locator('.results-head').count() > 0)
report('history region-chip present', await page.locator('.region-chip').count() > 0)
const db = page.getByRole('button', { name: /审核 \/ 编辑/ }).first()
if (await db.count()) {
  await db.click()
  await page.waitForTimeout(1200)
  report('.detail-layout grid', (await css('.detail-layout', 'display')) === 'grid')
  const gtc = (await css('.detail-layout', 'gridTemplateColumns')).split(' ').filter(Boolean)
  report('.detail-layout 2 cols', gtc.length === 2)
  report('.detail-main min-width 0', (await css('.detail-main', 'minWidth')) === '0px')
  report('.detail-aside present', await page.locator('.detail-aside').count() > 0)
  report('.detail-chips present', await page.locator('.detail-chips').count() > 0)
  report('.detail-summary items >= 4', await page.locator('.detail-summary-item').count() >= 4)
  await page.keyboard.press('Escape'); await page.waitForTimeout(400)
}

// ---- Analytics ----
await page.getByRole('button', { name: '关联洞察', exact: true }).click()
await page.waitForTimeout(1500)
report('.type-row rows', await page.locator('.analytics-types .type-row').count() > 0, `count=${await page.locator('.analytics-types .type-row').count()}`)
if (await page.locator('.analytics-types .type-row-bar i').count() > 0) {
  const widths = await page.$$eval('.analytics-types .type-row-bar i', (els) => els.map((e) => e.style.width))
  report('.type-row bars have inline width', widths.every(Boolean), widths.join(', '))
}
report('.analytics-summary cards', await page.locator('.analytics-summary > div').count() >= 3)

// ---- Sources ----
await page.getByRole('button', { name: '信息源管理', exact: true }).click()
await page.waitForTimeout(1000)
report('.management-row grid 5 cols', (await css('.management-row', 'gridTemplateColumns')).split(' ').filter(Boolean).length === 5)
report('.source-toggle switch', await page.locator('.management-row .source-toggle').count() > 0)
report('.list-head present', await page.locator('.list-head').count() > 0)

// ---- Overflow sweep across all views (scroll/clipping regressions) ----
for (const v of ['采集工作台', '数据归档', '关联洞察', '关键词配置', '信息源管理', 'API配置']) {
  await page.getByRole('button', { name: v, exact: true }).click()
  await page.waitForTimeout(1100)
  const overflow = await page.evaluate(() => {
    const bad = []
    document.querySelectorAll('main *').forEach((el) => {
      if (!(el instanceof HTMLElement)) return
      if (el.scrollWidth > el.clientWidth + 2) bad.push(`${el.tagName.toLowerCase()}.${String(el.className).split(' ').slice(0, 2).join('.')} ${el.scrollWidth}->${el.clientWidth}`)
    })
    return bad.slice(0, 12)
  })
  report(`overflow sweep: ${v}`, overflow.length === 0, overflow.join(' | '))
}

console.log('\n' + (errors.length ? 'ERRORS:\n' + errors.join('\n') : 'no console errors'))

await browser.close()

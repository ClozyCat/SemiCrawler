import { chromium } from 'playwright'

const BASE = 'http://127.0.0.1:5173/'
const tag = process.argv[2] || 'cur'
const only = process.argv[3] || null

const VIEWS = [
  { id: 'dashboard', name: '采集工作台' },
  { id: 'history', name: '数据归档' },
  { id: 'analytics', name: '关联洞察' },
  { id: 'keywords', name: '关键词配置' },
  { id: 'sources', name: '信息源管理' },
  { id: 'settings', name: 'API配置' },
]

const EXE = `${process.env.LOCALAPPDATA}\\ms-playwright\\chromium-1219\\chrome-win64\\chrome.exe`
const browser = await chromium.launch({ executablePath: EXE })
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 }, deviceScaleFactor: 1 })
page.on('console', (m) => { if (m.type() === 'error') console.log('  [console error]', m.text().slice(0, 200)) })

await page.goto(BASE, { waitUntil: 'networkidle' })
await page.waitForTimeout(1500)

for (const v of VIEWS) {
  if (only && only !== v.id) continue
  await page.getByRole('button', { name: v.name, exact: true }).click()
  await page.waitForTimeout(1800)
  await page.screenshot({ path: `shots/${tag}_${v.id}.png`, fullPage: true })
  console.log('shot', v.id)
}

// modals
if (!only) {
  await page.getByRole('button', { name: '数据归档', exact: true }).click()
  await page.waitForTimeout(1200)
  const detail = page.getByRole('button', { name: /审核 \/ 编辑/ }).first()
  if (await detail.count()) {
    await detail.click(); await page.waitForTimeout(1500)
    await page.screenshot({ path: `shots/${tag}_modal_detail.png`, fullPage: false })
    console.log('shot modal_detail')
    await page.keyboard.press('Escape'); await page.waitForTimeout(500)
  }
  await page.getByRole('button', { name: '原始数据' }).click(); await page.waitForTimeout(1200)
  const raw = page.locator('.article-title-cell button').first()
  if (await raw.count()) {
    await raw.click(); await page.waitForTimeout(1200)
    await page.screenshot({ path: `shots/${tag}_modal_raw.png`, fullPage: false })
    console.log('shot modal_raw')
    await page.keyboard.press('Escape'); await page.waitForTimeout(500)
  }
  await page.getByRole('button', { name: '采集任务' }).click(); await page.waitForTimeout(1000)
  await page.screenshot({ path: `shots/${tag}_tasks.png`, fullPage: true })
  console.log('shot tasks')
  const log = page.getByRole('button', { name: '运行日志' }).first()
  if (await log.count()) {
    await log.click(); await page.waitForTimeout(1500)
    await page.screenshot({ path: `shots/${tag}_modal_logs.png`, fullPage: false })
    console.log('shot modal_logs')
    await page.keyboard.press('Escape'); await page.waitForTimeout(500)
  }
  await page.getByRole('button', { name: '信息源管理', exact: true }).click(); await page.waitForTimeout(1000)
  await page.getByRole('button', { name: '添加信息源' }).first().click(); await page.waitForTimeout(1000)
  await page.screenshot({ path: `shots/${tag}_modal_source.png`, fullPage: false })
  console.log('shot modal_source')
}

await browser.close()
console.log('done')

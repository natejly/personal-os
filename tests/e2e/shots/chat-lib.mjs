import { appendFileSync } from 'node:fs'
import { homedir } from 'node:os'
import { join } from 'node:path'
import { shot, sleep } from './lib.mjs'
export const INV = join(homedir(), '.claude/jobs/66435b7a/tmp/inventory-chat.md')
export const msgBox = (page) => page.getByRole('textbox', { name: 'Message', exact: true })
export const newChat = (page) => page.getByRole('button', { name: /New chat/ }).first().click()
export async function say(page, text) { const b = msgBox(page); await b.fill(text); await b.press('Enter') }
export async function settle(page, ms = 30000) {
  const t = Date.now()
  while (Date.now() - t < ms) {
    if (!(await page.getByRole('button', { name: /^Stop/ }).count())) { await sleep(400); if (!(await page.getByRole('button', { name: /^Stop/ }).count())) return }
    await sleep(300)
  }
}
export async function inventory(page, name, extra = '') {
  const d = await page.evaluate(() => {
    const vis = (e) => e.offsetParent !== null || getComputedStyle(e).position === 'fixed'
    const btns = [...document.querySelectorAll('button, [role=tab], [role=menuitem], a')].filter(vis)
      .map((e) => (e.getAttribute('aria-label') || e.innerText || e.title || '').trim().replace(/\s+/g, ' ')).filter(Boolean)
    const texts = []
    for (const e of document.querySelectorAll('p, div, span, li, label, h1, h2, h3, h4, small')) {
      if (!vis(e) || [...e.children].some((c) => (c.innerText || '').trim().length > 40)) continue
      const t = (e.innerText || '').trim().replace(/\s+/g, ' ')
      if (t.length > 40) texts.push(t.slice(0, 300))
    }
    return { btns, texts: [...new Set(texts)] }
  })
  appendFileSync(INV, `\n## ${name}\n${extra ? extra + '\n' : ''}Buttons/tabs/links: ${d.btns.join(' | ')}\nText blocks >40 chars:\n${d.texts.map((t) => '- ' + t).join('\n')}\n`)
}
export async function snap(g, name, page = g.page, extra = '') {
  await shot(g, name, page)
  await inventory(page, name, extra)
}
export async function attempt(label, fn) {
  try { await fn() } catch (e) { console.log('FAILED', label, String(e).split('\n')[0]); appendFileSync(INV, `\n## ${label}\nNOT CAPTURED: ${String(e).split('\n')[0]}\n`) }
}

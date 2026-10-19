// Visual QA sweep: one isolated Grain, seeded with realistic content, every main surface shot per look x width.
//   node tests/e2e/shots/visual-qa.mjs                       default matrix
//   VQA_OUT=/tmp/vqa VQA_LOOKS=dark,light,paper,terminal VQA_WIDTHS=1440,1024,720 VQA_ONLY=chat,settings node ...
// Each shot also logs layout suspects (horizontal overflow, text clipped by its box) to <out>/report.txt.
import { appendFileSync, mkdirSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { session, sleep, nav } from './lib.mjs'
import { dayStr } from '../helpers/todos.mjs'
const menuById = async (g, id) => { await g.app.evaluate(({ BrowserWindow }) => { const b = BrowserWindow.getAllWindows().find((w) => w.webContents.getURL().includes('index.html')); if (b) { b.show(); b.focus() } }); await sleep(300); return menuById0(g, id) }
const menuById0 = (g, id) => g.app.evaluate(({ Menu }, id) => { const it = Menu.getApplicationMenu().getMenuItemById(id); if (!it) return false; it.click(); return true }, id)
import { menuClick } from '../helpers/spaces.mjs'

const OUT = process.env.VQA_OUT || '/tmp/vqa'
const LOOKS = {
  dark: { theme: 'dark', accent: 'sage', uiFont: 'system', codeFont: 'default' },
  light: { theme: 'light', accent: 'sage', uiFont: 'system', codeFont: 'default' },
  paper: { theme: 'light', accent: 'clay', uiFont: 'serif', codeFont: 'default' },
  terminal: { theme: 'dark', accent: 'mint', uiFont: 'mono', codeFont: 'menlo' },
  custom: { theme: 'dark', accent: 'custom', accentCustom: '#f0c43c', uiFont: 'system', codeFont: 'default' },
  customlight: { theme: 'light', accent: 'custom', accentCustom: '#f0c43c', uiFont: 'system', codeFont: 'default' }
}
const looks = (process.env.VQA_LOOKS || 'dark,light').split(',')
const widths = (process.env.VQA_WIDTHS || '1440,1024,820').split(',').map(Number)
const H = Number(process.env.VQA_H || 860)
const only = (process.env.VQA_ONLY || '').split(',').filter(Boolean)
const want = (k) => !only.length || only.includes(k)
mkdirSync(OUT, { recursive: true })
const REPORT = join(OUT, 'report.txt')
if (!process.env.VQA_APPEND) writeFileSync(REPORT, '')

let tag = ''
const attempt = async (label, fn) => {
  try { await fn() } catch (e) { const m = `FAILED ${tag} ${label}: ${String(e).split('\n')[0]}`; console.log(m); appendFileSync(REPORT, m + '\n') }
}

async function shot(g, name) {
  await sleep(450)
  // The app runs at a zoom factor, so page.screenshot crops the right edge; capture the whole window instead.
  const b64 = await g.app.evaluate(async ({ BrowserWindow }) => (await BrowserWindow.getAllWindows().find((w) => w.webContents.getURL().includes('index.html')).webContents.capturePage()).toPNG().toString('base64'))
  writeFileSync(join(OUT, `${tag}__${name}.png`), Buffer.from(b64, 'base64'))
  const sus = await g.page.evaluate(() => {
    const out = []
    const vw = innerWidth
    if (document.documentElement.scrollWidth > vw + 1) out.push(`page scrolls horizontally (${document.documentElement.scrollWidth}>${vw})`)
    const seen = new Set()
    for (const e of document.querySelectorAll('body *')) {
      const r = e.getBoundingClientRect()
      if (r.width <= 0 || r.height <= 0) continue
      const cs = getComputedStyle(e)
      if (cs.visibility === 'hidden' || cs.display === 'none') continue
      const id = typeof e.className === 'string' && e.className.trim() ? '.' + e.className.trim().split(/\s+/).slice(0, 2).join('.') : e.tagName.toLowerCase()
      if (r.right > vw + 2 && !e.closest('pre,.plane,.canvas-plane,[class*=scroll]')) {
        const k = 'right:' + id
        if (!seen.has(k)) { seen.add(k); out.push(`past right edge ${id} "${(e.innerText || '').trim().slice(0, 30)}" right=${Math.round(r.right)}`) }
      }
      if (e.children.length === 0 && (e.innerText || '').trim() && cs.overflow !== 'visible' && cs.textOverflow !== 'ellipsis' && e.scrollWidth > e.clientWidth + 2 && cs.whiteSpace !== 'pre' && e.tagName !== 'INPUT' && e.tagName !== 'TEXTAREA') {
        const k = 'clip:' + id
        if (!seen.has(k)) { seen.add(k); out.push(`clipped text ${id} "${(e.innerText || '').trim().slice(0, 30)}" ${e.scrollWidth}>${e.clientWidth}`) }
      }
    }
    return out.slice(0, 12)
  }).catch(() => [])
  if (sus.length) appendFileSync(REPORT, `## ${tag} ${name}\n${sus.map((s) => '  - ' + s).join('\n')}\n`)
  console.log('shot', tag, name)
}

const setSize = (g, w, h) => g.app.evaluate(({ BrowserWindow }, [w, h]) => { const b = BrowserWindow.getAllWindows()[0]; b.setMinimumSize(1, 1); b.setSize(w, h) }, [w, h])
const LONG = 'The launch plan has three moving parts, and each one depends on the others in ways that are easy to miss when you read the list top to bottom. First, the email sequence needs the new pricing page live, which in turn waits on legal sign-off for the revised terms. Second, the support team wants a macro library before the first send, so tickets land with consistent answers. Third, analytics must be tagged before any traffic arrives, otherwise the first two days of data are unusable. '
const CODE = '```python\nimport asyncio\n\nasync def fetch_all(urls: list[str], limit: int = 8) -> list[bytes]:\n    sem = asyncio.Semaphore(limit)\n    async def one(u):\n        async with sem:\n            return await get(u)  # a very long line that keeps going and going to test horizontal scrolling inside code blocks ........................\n    return await asyncio.gather(*(one(u) for u in urls))\n```\n'
const TABLE = '| Step | Owner | Due | Status |\n|---|---|---|---|\n| Pricing page | Maya | Oct 14 | in review |\n| Legal sign-off | Ken | Oct 15 | blocked |\n| Macro library | Priya | Oct 16 | drafting |\n'

async function seed(g) {
  const { api } = g
  const chat = async (title, content, project_id = null) => {
    const c = await api('/conversations', { method: 'POST', body: { title, project_id } })
    await api(`/conversations/${c.id}/chat`, { method: 'POST', body: { content } })
    await sleep(2500)
    return c
  }
  const pr = await api('/projects', { method: 'POST', body: { name: 'Website relaunch', description: 'Ship the new marketing site and pricing.', system_prompt: 'Be concise. Prefer bullet lists.', color: '#6aa0d8' } })
  await api('/projects', { method: 'POST', body: { name: 'Tax 2026', color: '#d98a57' } })
  await api('/projects', { method: 'POST', body: { name: 'A project with a really quite long name that must truncate', color: '#a07bd8' } })
  g.chats = {}
  g.chats.long = await chat('Plan the launch email sequence and the analytics tagging for the new pricing page', `!!reply ## Launch plan\n\n${LONG}\n\n${LONG}\n\n- Item one with a fairly long description that wraps onto a second line when the pane is narrow enough to need it\n- Item two\n  - nested item\n\n${CODE}\n${TABLE}\n> A quoted note that is long enough to wrap in a narrow window, just to see how blockquotes behave.\n\nInline \`code_in_a_sentence()\` and a very_long_unbroken_identifier_that_cannot_wrap_naturally_in_a_narrow_message_column_at_all_whatever.`, pr.id)
  g.chats.tool = await chat('Tool use and files', '!!tool web_search {"query":"pricing page conversion benchmarks"}', pr.id)
  g.chats.todo = await chat('Progress checklist', '!!tool todo_write {"steps":[{"text":"Read the brief","status":"completed"},{"text":"Draft the email sequence with a long step title that wraps in the narrow side panel","status":"in_progress"},{"text":"Tag analytics","status":"pending"}]}')
  g.chats.short = await chat('Trip to Lisbon', '!!reply Two days in Alfama, one in Sintra. Book the tram early.')
  await chat('Meeting notes: Acme', '!!reply Notes saved.')
  await chat('Fix login redirect bug', '!!reply Found it: the redirect dropped the query string.')
  for (const [title, folder] of [['Launch brief', ''], ['Pricing notes', 'Work'], ['Meeting 2026-10-10', 'Work/Meetings'], ['A document whose title is long enough to need truncating in lists and tabs', ''], ['Reading list', 'Personal']])
    await api('/docs', { method: 'POST', body: { title, content: `# ${title}\n\n${LONG}\n\n- one\n- two\n\n${CODE}`, folder, project_id: null } }).catch(() => {})
  const todos = [['Send invoice to Acme', dayStr(0), 1], ['Renew passport', dayStr(-3), 2], ['Book dentist appointment for the autumn checkup, preferably Thursday afternoon', dayStr(4), 0], ['Read the Q3 report', null, 0], ['Water the plants', dayStr(1), 0]]
  for (const [title, due, priority] of todos) await api('/todos', { method: 'POST', body: { title, due, priority } }).catch(() => {})
  await api('/jobs', { method: 'POST', body: { name: 'Morning briefing', prompt: 'Summarise my calendar and unread mail', kind: 'cron', cron: '0 9 * * *', enabled: true } }).catch(() => {})
  await api('/jobs', { method: 'POST', body: { name: 'Weekly review with a long name that has to truncate somewhere sensible', prompt: 'Review the week', kind: 'cron', cron: '0 17 * * 5', enabled: false } }).catch(() => {})
  const s = (await api('/canvases'))[0]
  await api('/canvases', { method: 'POST', body: { name: 'Research desk' } }).catch(() => {})
  await api(`/canvases/${s.id}/windows`, { method: 'POST', body: { kind: 'chat', ref_id: g.chats.short.id, x: 60, y: 50, w: 480, h: 480 } }).catch(() => {})
  await api(`/canvases/${s.id}/windows`, { method: 'POST', body: { kind: 'face', x: 600, y: 80, w: 220, h: 220 } }).catch(() => {})
  await api(`/canvases/${s.id}/windows`, { method: 'POST', body: { kind: 'todos', x: 600, y: 330, w: 340, h: 300 } }).catch(() => {})
}

const reloadHome = async (g) => { await g.page.reload(); await g.page.waitForSelector('.sidebar'); await sleep(900) }
const openChat = async (g, text) => { await g.page.locator('.sidebar .convo-item', { hasText: text }).first().click(); await sleep(900) }

async function surfaces(g) {
  const { page } = g
  if (want('shell')) {
    await attempt('new chat', async () => { await page.getByRole('button', { name: /New chat/ }).first().click(); await sleep(700); await shot(g, '01-chat-empty') })
    await attempt('today', async () => { await page.locator('.sidebar .brand').click(); await sleep(900); await shot(g, '02-today') })
  }
  if (want('chat')) {
    await attempt('chat long', async () => {
      await openChat(g, 'Plan the launch email'); await shot(g, '03-chat-long')
      await page.locator('.msgs, .messages, .chat-scroll').first().evaluate((e) => (e.scrollTop = 1e6)).catch(() => {}); await shot(g, '04-chat-long-bottom')
    })
    await attempt('chat tool', async () => { await openChat(g, 'Tool use'); await shot(g, '05-chat-tool') })
    await attempt('chat todo + side panel', async () => {
      await openChat(g, 'Progress checklist'); await shot(g, '06-chat-todo')
      await page.getByRole('button', { name: 'Workspace panel' }).click(); await sleep(800); await shot(g, '07-sidepanel')
      for (const n of ['Context', 'Files', 'Changes', 'Checklist', 'Workers', 'Review']) {
        const t = page.locator('.desk-panel, [aria-label="Workspace panel"]').getByText(n, { exact: true }).first()
        if (await t.count()) { if ((await t.locator('xpath=ancestor-or-self::button').first().getAttribute('aria-selected').catch(() => null)) !== 'true') await t.click({ timeout: 2000 }).catch(() => {}); await sleep(500); await shot(g, '08-sidepanel-' + n.toLowerCase()) }
      }
      await page.keyboard.press('Escape')
    })
    await attempt('composer ab', async () => {
      await g.api('/settings', { method: 'PUT', body: { devTools: true } }); await reloadHome(g)
      await openChat(g, 'Trip to Lisbon')
      await page.getByRole('button', { name: 'A/B' }).click(); await sleep(600); await shot(g, '09-composer-ab')
      await page.getByRole('button', { name: 'A/B' }).click()
      await g.api('/settings', { method: 'PUT', body: { devTools: false } }); await reloadHome(g)
    })
    await attempt('quick chat', async () => {
      await nav(page, 'Lists').click(); await sleep(600)
      await page.getByRole('button', { name: 'Quick chat' }).click(); await page.getByRole('complementary', { name: 'Page agent' }).waitFor(); await sleep(600); await shot(g, '11-quick-chat')
      await page.getByRole('complementary', { name: 'Page agent' }).getByRole('button', { name: /^Close/ }).click()
    })
    await attempt('palette', async () => { await menuClick(g.app, 'Command Palette…'); await sleep(600); await shot(g, '12-palette'); await page.keyboard.press('Escape') })
  }
  if (want('views')) {
    for (const [label, key] of [['Lists', '20-lists'], ['Calendar', '21-calendar'], ['Mail', '22-mail'], ['Health', '23-health'], ['Jobs', '25-jobs']])
      await attempt(label, async () => { await nav(page, label).click(); await sleep(1400); await shot(g, key) })
    await attempt('library tabs', async () => {
      await nav(page, 'Library').click(); await sleep(600)
      for (const t of ['Skills', 'Agents', 'Automations', 'Connectors']) { await page.getByRole('tab', { name: t, exact: true }).click(); await sleep(900); await shot(g, '24-library-' + t.toLowerCase()) }
      await page.getByRole('tab', { name: 'Browse', exact: true }).click().catch(() => {}); await sleep(1500); await shot(g, '24-library-catalog')
    })
    await attempt('files', async () => {
      await nav(page, 'Files').click(); await sleep(1200); await shot(g, '26-files')
      await page.getByText('Launch brief').first().click().catch(() => {}); await sleep(1000); await shot(g, '27-files-doc')
    })
    
  }
  if (want('projects')) {
    await attempt('project page', async () => { await page.locator('.sidebar').getByText('Website relaunch').first().click(); await sleep(1200); await shot(g, '30-project') })
  }
  if (want('spaces')) {
    await attempt('spaces', async () => {
      await page.locator('.sidebar').getByText('Canvas 1').first().click(); await sleep(2500); await shot(g, '31-spaces')
      await page.locator('.sidebar').getByText('Research desk').first().click().catch(() => {}); await sleep(1200); await shot(g, '32-spaces-empty')
    })
  }
  if (want('settings')) {
    await attempt('settings', async () => {
      await page.getByRole('button', { name: /^Settings/ }).first().click()
      const tl = page.getByRole('tablist', { name: 'Settings sections' })
      await tl.waitFor(); await sleep(700)
      const tabs = await tl.getByRole('tab').allInnerTexts()
      let i = 0
      for (const t of tabs) {
        const n = t.replace(/\s+/g, ' ').trim()
        await tl.getByRole('tab', { name: n, exact: true }).click(); await sleep(700)
        await shot(g, `40-settings-${String(i++).padStart(2, '0')}-${n.toLowerCase().replace(/\W+/g, '-')}`)
      }
      await page.keyboard.press('Escape'); await sleep(400)
    })
  }
}

for (const look of looks) {
  await session(async (g) => {
    await seed(g)
    for (const w of widths) {
      tag = `${look}-${w}x${H}`
      await setSize(g, w, H); await sleep(900)
      await reloadHome(g)
      await surfaces(g)
    }
    if (want('tall') && look === looks[0]) {
      for (const h of [560, 1200]) {
        tag = `${look}-1024x${h}`; await setSize(g, 1024, h); await sleep(900); await reloadHome(g)
        await attempt('tall chat', async () => { await openChat(g, 'Plan the launch'); await shot(g, '03-chat-long') })
        await attempt('tall settings', async () => { await g.page.getByRole('button', { name: /^Settings/ }).first().click(); await sleep(800); await shot(g, '40-settings'); await g.page.keyboard.press('Escape') })
      }
    }
  }, { settings: { ...LOOKS[look], sidebarHidden: [], hiddenViews: [] } })
}

if (want('onboarding')) {
  for (const [look, w] of [['dark', 1024], ['light', 720]]) {
    await session(async (g) => {
      tag = `onboarding-${look}-${w}`
      await setSize(g, w, H); await sleep(900)
      const ob = g.page.locator('.onboarding'); await ob.waitFor(); let i = 1
      await shot(g, `ob-${i++}`)
      const cont = (n = 'Continue') => g.page.getByRole('button', { name: new RegExp('^' + n) })
      await attempt('ob steps', async () => {
        await cont('Get started').click(); await sleep(500); await shot(g, `ob-${i++}`)
        await g.page.getByRole('radio', { name: /Custom/ }).click(); await cont().click(); await sleep(500); await shot(g, `ob-${i++}`)
        await ob.getByRole('textbox', { name: /API key/ }).fill('mock-key').catch(() => {}); await cont().click(); await sleep(2500); await shot(g, `ob-${i++}`)
        for (let k = 0; k < 4; k++) { await (k ? cont('Skip for now') : cont()).click({ timeout: 4000 }).catch(() => {}); await sleep(500); await shot(g, `ob-${i++}`) }
      })
    }, { settings: { ...LOOKS[look], onboardedAt: null, apiKey: null } })
  }
}

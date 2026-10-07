import { writeFileSync, mkdtempSync, realpathSync, rmSync } from 'node:fs'
import { homedir } from 'node:os'
import { join } from 'node:path'
import { session, sleep } from './lib.mjs'
import { ROOT } from '../harness.mjs'
import { scriptLLM } from '../helpers/scriptllm.mjs'
import { deskChat, openChat, WRITE, DELIVER, DONE } from '../helpers/cowork.mjs'
import { INV, msgBox, newChat, say, settle, snap, attempt } from './chat-lib.mjs'

const only = process.env.ONLY ? process.env.ONLY.split(',') : null
const want = (k) => !only || only.includes(k)
if (!only) writeFileSync(INV, '# Chat inventory (1280x800 unless noted)\n')

const MD = `!!reply Here is a quick plan for the week. I grouped the work so the riskiest item lands first and the rest can slide if needed.\n\n- Fix the flaky export test\n- Review the open pull requests\n- Draft the release notes\n\nA minimal helper looks like this:\n\n\`\`\`ts\nexport function total(xs: number[]) {\n  return xs.reduce((a, b) => a + b, 0)\n}\n\`\`\`\n\nLet me know if you want me to start on any of these.`
const resize = (g, w, h) => g.app.evaluate(({ BrowserWindow }, [w, h]) => BrowserWindow.getAllWindows()[0].setSize(w, h), [w, h])

if (want('basic')) await session(async (g) => {
  const { page } = g
  await attempt('chat-empty', async () => { await newChat(page); await sleep(500); await snap(g, 'chat-empty') })
  await attempt('chat-reply', async () => { await say(page, MD); await settle(page); await snap(g, 'chat-reply') })
  await attempt('chat-composer-footer', async () => { await msgBox(page).click(); await msgBox(page).fill('Draft the release notes'); await snap(g, 'chat-composer-footer'); await msgBox(page).fill('') })
  await attempt('chat-model-menu', async () => { await page.getByRole('button', { name: /^Model/ }).click(); await sleep(400); await snap(g, 'chat-model-menu'); await page.keyboard.press('Escape') })
  await attempt('chat-attach-menu', async () => { await page.getByRole('button', { name: 'Add files to this chat' }).click(); await sleep(400); await snap(g, 'chat-attach-menu'); await page.keyboard.press('Escape') })
  await attempt('chat-autonomy-menu', async () => { await page.getByRole('button', { name: /Work autonomously/ }).click(); await sleep(400); await snap(g, 'chat-autonomy-menu'); await page.keyboard.press('Escape') })
  await attempt('chat-message-hover', async () => { await page.locator('.msg.assistant').last().hover(); await sleep(300); await snap(g, 'chat-message-hover') })
  await attempt('chat-regen-row', async () => { await page.getByRole('button', { name: 'Regenerate' }).scrollIntoViewIfNeeded(); await page.getByRole('button', { name: 'Regenerate' }).hover(); await snap(g, 'chat-regen-row') })
  await attempt('chat-search', async () => {
    await g.app.evaluate(({ Menu }) => {
      const find = (items) => { for (const i of items) { if (i.label === 'Find…') return i; const r = i.submenu && find(i.submenu.items); if (r) return r } }
      find(Menu.getApplicationMenu().items).click()
    })
    const f = page.getByRole('textbox', { name: 'Find in conversation' }); await f.fill('helper'); await sleep(300)
    await snap(g, 'chat-search'); await f.press('Escape')
  })
  await attempt('chat-context-drawer', async () => { await page.getByRole('button', { name: 'Toggle context panel' }).click(); await sleep(800); await snap(g, 'chat-context-drawer'); await page.getByRole('button', { name: 'Toggle context panel' }).click() })
  await attempt('chat-files-popover-empty', async () => { await page.getByRole('button', { name: 'Documents in this chat' }).click(); await sleep(600); await snap(g, 'chat-files-popover-empty'); await page.keyboard.press('Escape') })
  await attempt('chat-narrow', async () => { await resize(g, 820, 520); await sleep(800); await snap(g, 'chat-narrow'); await resize(g, 1280, 800) })
})

if (want('tool')) await session(async (g) => {
  const { page } = g
  await attempt('chat-tool-card', async () => {
    await newChat(page); await say(page, '!!tool current_time {}'); await settle(page)
    await snap(g, 'chat-tool-card')
    await page.getByText('1 tool call').first().click(); await sleep(500)
    await snap(g, 'chat-tool-card-group-open')
    await page.locator('.tool-head').first().click(); await sleep(500)
    await snap(g, 'chat-tool-card-expanded')
  })
}, { settings: { toolDeferAbove: 0 } })

if (want('approval')) {
  const ws = realpathSync(mkdtempSync(join(homedir(), 'grain-e2e-ws-')))
  try {
    await session(async (g) => {
      const { page } = g
      await g.api('/settings', { method: 'PUT', body: { workspaceRoots: [ws], toolDeferAbove: 0 } })
      await newChat(page)
      await say(page, `!!tool shell_run {"command":"touch notes.txt","cwd":"${ws}"}`)
      await attempt('chat-approval-card', async () => { await page.getByRole('group', { name: 'Run command' }).waitFor({ timeout: 60000 }); await sleep(500); await snap(g, 'chat-approval-card') })
      await attempt('chat-approval-rules', async () => {
        const c = page.getByRole('group', { name: 'Run command' })
        await c.getByRole('button', { name: 'Deny with a note…' }).click(); await sleep(400)
        await snap(g, 'chat-approval-deny-note')
      })
    })
  } finally { rmSync(ws, { recursive: true, force: true }) }
}

if (want('allowall')) await session(async (g) => {
  const { page } = g
  await attempt('chat-allow-all-pill', async () => {
    await g.api('/settings', { method: 'PUT', body: { permissionMode: 'allow_all', allowAllConnections: true } })
    await page.reload(); await page.waitForSelector('.sidebar'); await sleep(800)
    await newChat(page); await sleep(500)
    const css = await page.evaluate(() => [...document.querySelectorAll('.skip-perms')].map((e) => {
      const s = getComputedStyle(e)
      // effective opacity = product of ancestor opacities
      let op = 1; for (let n = e; n; n = n.parentElement) op *= parseFloat(getComputedStyle(n).opacity)
      return `${e.innerText.trim()}: color=${s.color} bg=${s.backgroundColor} border=${s.borderColor} fontSize=${s.fontSize} opacity(effective)=${op}`
    }))
    await snap(g, 'chat-allow-all-pill', g.page, 'Computed styles of the pills: ' + css.join(' ;; '))
    // zoomed crop of the footer
    const box = await page.locator('.skip-perms').first().boundingBox()
    if (box) await page.screenshot({ path: join(ROOT, 'docs/ux-shots/before/chat-allow-all-pill-zoom.png'), clip: { x: Math.max(0, box.x - 380), y: Math.max(0, box.y - 20), width: 760, height: 60 } })
  })
})

if (want('email')) await session(async (g) => {
  const { page } = g
  await attempt('chat-email-review-card', async () => {
    const fd = new FormData(); fd.append('file', new Blob(['quarterly numbers']), 'numbers.txt')
    const doc = await (await fetch(`${g.backend.url}/documents`, { method: 'POST', headers: { Authorization: `Bearer ${g.token}` }, body: fd })).json()
    await g.api('/settings', { method: 'PUT', body: { toolDeferAbove: 0, permissionMode: 'allow_all' } })
    await page.reload(); await page.waitForSelector('.sidebar'); await newChat(page)
    await say(page, '!!tool gmail_send ' + JSON.stringify({ to: 'ann@example.com', cc: 'bo@example.com', subject: 'Numbers for Q3', body: 'Hi Ann,\n\nThe Q3 numbers are attached. Let me know if anything looks off.\n\nThanks', attachments: [doc.id] }))
    await page.getByRole('region', { name: 'Email to send' }).waitFor({ timeout: 60000 }); await sleep(600)
    await snap(g, 'chat-email-review-card')
  })
}, { backendEntry: [join(ROOT, 'tests', 'e2e', 'backend_fake_google.py')], settings: { toolDeferAbove: 0 } })

if (want('desk')) await session(async (g) => {
  const { page } = g
  await g.api('/settings', { method: 'PUT', body: { toolDeferAbove: 0 } })
  const llm = await scriptLLM(g)
  llm.push({ calls: [WRITE] }, { calls: [DELIVER] }, { calls: [DONE] }, { text: 'Finished. The report is in the Review tab.' })
  const { desk } = await deskChat(g, { brief: 'Write a short report', title: 'Report chat', autonomy: 'propose' })
  await attempt('chat-desk-review', async () => {
    await openChat(page, 'Report chat')
    for (let i = 0; i < 90; i++) { if ((await g.api(`/cowork/desks/${desk.id}`)).status === 'review') break; await sleep(1000) }
    await sleep(1500)
    await snap(g, 'chat-desk-review')
  })
  await attempt('chat-desk-strip', async () => { await snap(g, 'chat-desk-strip-review', page, 'Strip text: ' + (await page.locator('.desk-strip').innerText().catch(() => 'none'))) })
  await attempt('chat-desk-panel', async () => {
    if (!(await page.locator('.desk-panel').count())) await page.locator('.desk-strip').getByRole('button', { name: 'Files, changes and review' }).click()
    await sleep(600)
    const tabs = page.locator('.desk-panel .desk-tabs button:not(.icon-btn)')
    const n = await tabs.count()
    for (let i = 0; i < n; i++) {
      const label = (await tabs.nth(i).innerText()).trim().split(/\s+/)[0]
      await tabs.nth(i).click(); await sleep(700)
      await snap(g, `chat-desk-panel-${label.toLowerCase()}`)
    }
  })
  await attempt('chat-files-panel', async () => {
    const names = await page.locator('button[aria-label]').evaluateAll((els) => els.map((e) => e.getAttribute('aria-label')).filter((n) => /chat|file|doc/i.test(n)))
    console.log('top buttons', names)
    await page.getByRole('button', { name: /in this chat/ }).first().click(); await sleep(700); await snap(g, 'chat-files-panel'); await page.keyboard.press('Escape')
    await page.getByRole('button', { name: 'Documents from personal chats' }).click(); await sleep(700); await snap(g, 'chat-files-sidebar-tab')
  })
})

if (want('workers')) await session(async (g) => {
  const { page } = g
  await g.api('/settings', { method: 'PUT', body: { toolDeferAbove: 0 } })
  const llm = await scriptLLM(g)
  llm.push({ calls: [{ name: 'delegate', args: { goal: 'look something up', title: 'Alpha job' } }, { name: 'delegate', args: { goal: 'check the calendar notes', title: 'Beta job' } }] },
    { text: 'still working', delay: 120_000 }, { text: 'still working', delay: 120_000 })
  const { desk, chat } = await deskChat(g, { brief: 'look things up', title: 'Delegator', autonomy: 'ask' })
  await attempt('chat-worker-strip', async () => {
    await openChat(page, 'Delegator')
    await page.locator('section.worker-panel').waitFor({ timeout: 60000 }); await sleep(2500)
    await snap(g, 'chat-worker-strip')
    await page.locator('.desk-strip').getByRole('button', { name: 'Files, changes and review' }).click().catch(() => {}); await sleep(600)
    await snap(g, 'chat-worker-strip-panel')
  })
  const { workers } = await g.api(`/conversations/${chat.id}/workers`).catch(() => ({ workers: [] }))
  for (const w of workers) await g.api(`/workers/${w.id}/stop`, { method: 'POST' }).catch(() => {})
  await g.api(`/cowork/desks/${desk.id}/stop`, { method: 'POST' }).catch(() => {})
})

// Checklist side card + always-visible reply meta, at narrow, normal and wide windows.
// Run with SHOTS_PHASE=<name>. Widest last: a hidden window shrunk after the sidebar was hidden can crash its renderer.
import { session, shot, sleep } from './lib.mjs'
import { newChat, say, settle } from './chat-lib.mjs'

const resize = (g, w, h) => g.app.evaluate(({ BrowserWindow }, [w, h]) => BrowserWindow.getAllWindows()[0].setSize(w, h), [w, h])
const plan = { steps: [
  { text: 'Read the migration file', status: 'done', note: 'db.py' },
  { text: 'Add the new column to the table schema', status: 'done' },
  { text: 'Write the backfill for existing rows and verify counts', status: 'in_progress' },
  { text: 'Update the API route', status: 'pending' },
  { text: 'Run the full test suite', status: 'pending' }] }

await session(async (g) => {
  const { page } = g
  await newChat(page)
  await say(page, '!!tool todo_write ' + JSON.stringify(plan))
  await settle(page)
  await page.mouse.move(5, 5)
  for (const [w, h] of [[900, 800], [1280, 800], [1380, 800]]) {
    await resize(g, w, h); await sleep(800)
    // The window is capped by the screen, so a wide pane needs the sidebar hidden. The capture of a hidden
    // window stays at the old width while the layout is already wide, so the side card is cut off in the PNG.
    if (w === 1380) { await page.getByRole('button', { name: 'Hide sidebar' }).click(); await sleep(1200) }
    const toggle = page.getByRole('region', { name: 'Progress' }).getByRole('button', { name: /the steps/ })
    await shot(g, `checklist-${w}-collapsed`)
    await toggle.click(); await sleep(400)
    await shot(g, `checklist-${w}-open`)
    await toggle.click()
  }
})

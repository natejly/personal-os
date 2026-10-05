import { test, expect } from './fixtures.mjs'
import { newChat, say, realErrors } from './helpers/chat.mjs'

const last = (page) => page.locator('.msg.assistant').last()
const settle = (grain) => grain.api('/settings', { method: 'PUT', body: { toolDeferAbove: 0 } })

test('probe email card', async ({ grain }) => {
  const { page, api } = grain
  await settle(grain)
  await newChat(page)
  await say(page, '!!tool gmail_draft ' + JSON.stringify({ to: 'ann@example.com', subject: 'Hello', body: 'Hi Ann' }), { wait: false })
  await page.waitForTimeout(6000)
  console.log('APPR', JSON.stringify(await api('/approvals?status=all')).slice(0, 600))
  console.log('TXT', await last(page).innerText())
  await say(page, '!!tool calendar_create ' + JSON.stringify({ summary: 'Lunch', start: '2026-10-06T12:00:00', end: '2026-10-06T13:00:00' }), { wait: false })
  await page.waitForTimeout(6000)
  console.log('APPR2', JSON.stringify(await api('/approvals?status=all')).slice(0, 900))
  console.log('TXT2', await last(page).innerText())
})

// Setup cards in chat: setup_request for Google, and a Google tool that finds no account.
// Run with SHOTS_PHASE=<name>.
import { session, shot, sleep } from './lib.mjs'
import { newChat, say, settle } from './chat-lib.mjs'

await session(async (g) => {
  const { page } = g
  await newChat(page)
  await say(page, '!!tool setup_request ' + JSON.stringify({ feature: 'google', reason: 'to read your calendar and mail' }))
  await settle(page)
  await say(page, '!!tool gmail_search ' + JSON.stringify({ query: 'from:mira' }))
  await settle(page)
  await say(page, '!!tool setup_request ' + JSON.stringify({ feature: 'telegram' }))
  await settle(page)
  await page.mouse.move(5, 5)
  await sleep(500)
  await shot(g, 'setup-cards')
})

/** The sidebar's default rows: Lists, Calendar, Mail and Health follow the fixed rows, ahead of Memory and Library. */
import test from 'node:test'
import assert from 'node:assert/strict'
import { navEntries } from './nav'

test('the sidebar rows come in a fixed default order', () => {
  assert.deepEqual(navEntries().map((e) => e.view), ['todos', 'calendar', 'mail', 'health', 'memory', 'library'])
})

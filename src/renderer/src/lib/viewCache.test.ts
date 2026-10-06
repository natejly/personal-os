import assert from 'node:assert/strict'
import { afterEach, beforeEach, describe, it } from 'node:test'
import { calendarViewKey, clearViews, readView, writeView } from './viewCache'

const store = new Map<string, string>()
const localStorage = {
  getItem: (k: string): string | null => (store.has(k) ? store.get(k)! : null),
  setItem: (k: string, v: string): void => { store.set(k, v) },
  removeItem: (k: string): void => { store.delete(k) },
  key: (i: number): string | null => [...store.keys()][i] ?? null,
  get length(): number { return store.size }
}

describe('viewCache', () => {
  beforeEach(() => {
    store.clear()
    Object.assign(globalThis, { localStorage })
    clearViews()
  })
  afterEach(() => {
    clearViews()
  })

  it('remembers a screen for the next paint, and still has it if the tab store is what is left', () => {
    writeView('cal:week', [{ id: 'e1' }])
    assert.deepEqual(readView('cal:week'), [{ id: 'e1' }])
    assert.equal(localStorage.getItem('grain.gview.cal:week'), JSON.stringify([{ id: 'e1' }]))
    assert.equal(calendarViewKey('2026-10-04T04:00:00.000Z', 7, 'primary'), 'cal:2026-10-04T04:00:00.000Z:7:primary')
  })

  it('clearViews forgets every saved screen and leaves other keys alone', () => {
    writeView('mail:in:inbox', [{ id: 'm1' }])
    localStorage.setItem('grain.calendar.visibility', '{}')
    clearViews()
    assert.equal(readView('mail:in:inbox'), null)
    assert.equal(localStorage.getItem('grain.calendar.visibility'), '{}')
  })
})

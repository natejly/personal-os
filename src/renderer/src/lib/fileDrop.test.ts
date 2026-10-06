import assert from 'node:assert/strict'
import { test } from 'node:test'
import { installFileDropGuard, shouldSwallowFileDrag } from './fileDrop'

test('only an unclaimed file drag is swallowed', () => {
  assert.equal(shouldSwallowFileDrag(['Files'], false), true)
  assert.equal(shouldSwallowFileDrag(['Files', 'text/uri-list'], false), true)
  assert.equal(shouldSwallowFileDrag(['Files'], true), false, 'a real drop target handled it')
  assert.equal(shouldSwallowFileDrag(['text/plain'], false), false)
  assert.equal(shouldSwallowFileDrag(['application/x-grain-drag'], false), false)
  assert.equal(shouldSwallowFileDrag([], false), false)
  assert.equal(shouldSwallowFileDrag(null, false), false)
})

/** The two listeners, without a DOM: enough to see what each one does to the event. */
class FakeWindow {
  handlers = new Map<string, (e: unknown) => void>()
  addEventListener(name: string, fn: (e: unknown) => void): void { this.handlers.set(name, fn) }
  removeEventListener(name: string): void { this.handlers.delete(name) }
}

const event = (types: string[], defaultPrevented = false): { defaultPrevented: boolean; prevented: boolean; dataTransfer: { types: string[]; dropEffect: string }; preventDefault: () => void } => {
  const e = {
    defaultPrevented,
    prevented: false,
    dataTransfer: { types, dropEffect: 'copy' },
    preventDefault: () => { e.prevented = true }
  }
  return e
}

test('the guard cancels a stray file drop and shows not-allowed; handled drops pass untouched', () => {
  const w = new FakeWindow()
  const off = installFileDropGuard(w as unknown as Window)
  const over = event(['Files'])
  w.handlers.get('dragover')!(over)
  assert.equal(over.prevented, true)
  assert.equal(over.dataTransfer.dropEffect, 'none')
  const drop = event(['Files'])
  w.handlers.get('drop')!(drop)
  assert.equal(drop.prevented, true)
  const handled = event(['Files'], true)
  w.handlers.get('dragover')!(handled)
  w.handlers.get('drop')!(handled)
  assert.equal(handled.prevented, false)
  assert.equal(handled.dataTransfer.dropEffect, 'copy')
  const text = event(['text/plain'])
  w.handlers.get('drop')!(text)
  assert.equal(text.prevented, false)
  off()
  assert.equal(w.handlers.size, 0)
})

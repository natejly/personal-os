/** `sameFrameProps`: the gate that keeps a plane re-render off every window body. */
import test from 'node:test'
import assert from 'node:assert/strict'
import { createElement } from 'react'
import type { CanvasWindow } from '@shared/types'
import StatusRing from './StatusRing'
import WindowFrame, { sameFrameProps } from './WindowFrame'

const win = (over: Partial<CanvasWindow> = {}): CanvasWindow => ({
  id: 'w1', canvas_id: 'c1', kind: 'chat', ref_id: 'conv1', project_id: null, title: '',
  x: 10, y: 20, w: 520, h: 640, z: 3, state: 'normal', restore_bounds: null, popout_bounds: null,
  pinned: 0, opacity: 1, config: {}, created_at: 0, updated_at: 0, ...over
})

const ring = (id: string | null): JSX.Element => createElement(StatusRing, { conversationId: id })
const props = (w: CanvasWindow, live = true, selected = false, status: JSX.Element | null = ring(w.ref_id)) =>
  ({ win: w, live, selected, status })

test('WindowFrame is memoized', () => {
  assert.equal(typeof (WindowFrame as unknown as { compare?: unknown }).compare, 'function')
})

/** A plane re-render hands down an identical row and a brand-new status element. */
test('a bare plane re-render does not re-render the frame', () => {
  const w = win()
  assert.ok(sameFrameProps(props(w), props(w)))
})

test('a fresh row object with identical fields is still the same row', () => {
  const config = {}
  assert.ok(sameFrameProps(props(win({ config })), props(win({ config }))))
})

/** Documented consequence: a reload rebuilds `config`, so every window re-renders exactly once. */
test('a reloaded row re-renders once, because its nested config is a new object', () => {
  assert.equal(sameFrameProps(props(win()), props(win())), false)
})

test('geometry, state, z, title, config and ref changes all re-render', () => {
  const config = {}
  const base = props(win({ config }))
  for (const over of [{ x: 11 }, { y: 21 }, { w: 521 }, { h: 641 }, { z: 4 }, { state: 'maximized' as const }, { title: 'x' }, { config: {} }, { ref_id: 'conv2' }, { pinned: 1 }, { updated_at: 1 }, { canvas_id: 'c2' }]) {
    assert.equal(sameFrameProps(base, props(win({ config, ...over }))), false, `missed ${JSON.stringify(over)}`)
  }
})

test('live and selected re-render', () => {
  const w = win()
  assert.equal(sameFrameProps(props(w), props(w, false)), false)
  assert.equal(sameFrameProps(props(w), props(w, true, true)), false)
})

test('a changed status element re-renders, a null one compares equal', () => {
  const w = win()
  assert.equal(sameFrameProps(props(w), props(w, true, false, ring('conv2'))), false)
  assert.equal(sameFrameProps(props(w, true, false, null), props(w, true, false, null)), true)
  assert.equal(sameFrameProps(props(w, true, false, null), props(w)), false)
})

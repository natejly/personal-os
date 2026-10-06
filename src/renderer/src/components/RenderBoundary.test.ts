import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createElement } from 'react'
import RenderBoundary, { logLine, shouldReset } from './RenderBoundary'

test('shouldReset compares by identity', () => {
  const a = { x: 1 }
  assert.equal(shouldReset(a, a), false)
  assert.equal(shouldReset(a, { ...a }), true)
  assert.equal(shouldReset('s', 's'), false)
  assert.equal(shouldReset('s', 's2'), true)
  assert.equal(shouldReset(NaN, NaN), false)
})

test('logLine names the label and message and keeps six stack lines', () => {
  const stack = Array.from({ length: 10 }, (_, i) => `    at C${i}`).join('\n')
  const line = logLine('tool x', new Error('boom'), stack)
  assert.ok(line.startsWith('[chat:tool x] boom'))
  assert.equal(line.split('\n').length, 1 + 6)
  assert.equal(logLine('markdown', new Error('m'), null), '[chat:markdown] m')
})

test('an error is logged once per distinct message per instance', () => {
  const b = new RenderBoundary({ label: 'l', resetKey: 1, fallback: () => null, children: null })
  const seen: string[] = []
  const orig = console.error
  console.error = (m: string): void => { seen.push(m) }
  try {
    b.componentDidCatch(new Error('a'), { componentStack: '  at X' })
    b.componentDidCatch(new Error('a'), { componentStack: '  at X' })
    b.componentDidCatch(new Error('b'), { componentStack: '  at X' })
  } finally {
    console.error = orig
  }
  assert.equal(seen.length, 2)
  assert.ok(seen[0].startsWith('[chat:l] a') && seen[1].startsWith('[chat:l] b'))
})

test('the derived state captures the error and the element is constructible', () => {
  const e = new Error('x')
  assert.equal(RenderBoundary.getDerivedStateFromError(e).error, e)
  assert.ok(createElement(RenderBoundary, { label: 'l', resetKey: 1, fallback: () => null, children: null }))
})

test('the boundary does not subscribe to the store and the tool fallback offers no standing grants', () => {
  const root = 'src/renderer/src/components/'
  assert.ok(!/useStore\(/.test(readFileSync(`${root}RenderBoundary.tsx`, 'utf8')))
  const tools = readFileSync(`${root}ToolEvents.tsx`, 'utf8')
  const fb = tools.slice(tools.indexOf('function ToolFallback'), tools.indexOf('function genericRow'))
  assert.ok(fb.length > 100)
  assert.ok(!fb.includes('always_chat') && !fb.includes('always_global'))
})

import test from 'node:test'
import assert from 'node:assert'
import { readFileSync } from 'node:fs'
import { buildPreviewDoc, fenceKind, hasScript, PREVIEW_SANDBOX, SVG_CSP } from './htmlFence'

test('fenceKind recognises html and svg fences only', () => {
  assert.equal(fenceKind('html'), 'html')
  assert.equal(fenceKind('HTML'), 'html')
  assert.equal(fenceKind('svg'), 'svg')
  assert.equal(fenceKind('xml'), null)
  assert.equal(fenceKind(''), null)
  assert.equal(fenceKind('js'), null)
})

test('the preview sandbox never includes allow-same-origin', () => {
  assert.equal(PREVIEW_SANDBOX, 'allow-scripts')
})

test('the iframe source files pass the sandbox through the constant and never spell allow-same-origin', () => {
  for (const f of ['HtmlBlock.tsx']) {
    const src = readFileSync(`${process.cwd()}/src/renderer/src/components/${f}`, 'utf8')
    assert.ok(!/allow-same-origin/.test(src.replace(/\/\/.*|\/\*[\s\S]*?\*\//g, '')), `${f} must not grant allow-same-origin`)
    assert.ok(/sandbox=\{(PREVIEW_SANDBOX|FRAME_SANDBOX)\}/.test(src), `${f} sets sandbox from the pinned constant`)
  }
})

test('the CSP meta comes before any model markup, so nothing runs ahead of it', () => {
  const evil = '<!DOCTYPE html><html><head><script>fetch("http://x")</script></head><body>hi</body></html>'
  const out = buildPreviewDoc(evil)
  assert.ok(out.startsWith('<!doctype html><meta http-equiv="Content-Security-Policy"'))
  assert.ok(out.indexOf('Content-Security-Policy') < out.indexOf('<script'))
  assert.ok(out.includes("connect-src 'none'") && out.includes("default-src 'none'"))
  assert.equal((out.match(/<!doctype/gi) ?? []).length, 1)
})

test('a fragment gets wrapped with a body', () => {
  const out = buildPreviewDoc('<button>Go</button>')
  assert.ok(out.includes('<body') && out.includes('<button>Go</button>'))
})

test('svg previews refuse scripts outright', () => {
  assert.ok(SVG_CSP.includes("script-src 'none'"))
  const out = buildPreviewDoc('<svg><script>alert(1)</script></svg>', 'svg')
  assert.ok(out.includes("script-src 'none'"))
})

test('hasScript', () => {
  assert.ok(hasScript('<div><script>1</script></div>'))
  assert.ok(!hasScript('<div>hi</div>'))
})

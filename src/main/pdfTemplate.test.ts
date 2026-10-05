import test from 'node:test'
import assert from 'node:assert/strict'
import { footerTemplate } from './pdfTemplate'

test('footer escapes the title and carries the page number slot', () => {
  const f = footerTemplate('<b>"x" & y</b>')
  assert.ok(!f.includes('<b>'))
  assert.ok(f.includes('&lt;b&gt;&quot;x&quot; &amp; y&lt;/b&gt;'))
  assert.ok(f.includes('class="pageNumber"'))
})

test('a long title is cut', () => {
  assert.ok(footerTemplate('x'.repeat(200)).includes('x'.repeat(69) + '…'))
})

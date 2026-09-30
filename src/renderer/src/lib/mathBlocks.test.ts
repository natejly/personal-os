import test from 'node:test'
import assert from 'node:assert/strict'
import { normalizeMathBlocks } from './mathBlocks'

test('text without display maths is returned untouched', () => {
  const s = 'Just prose with $inline$ maths and a price of $5.'
  assert.equal(normalizeMathBlocks(s), s)
})

test('a one-line $$…$$ becomes a real block', () => {
  assert.equal(normalizeMathBlocks('$$x^2 + y^2 = z^2$$'), '$$\nx^2 + y^2 = z^2\n$$')
})

test('an already-correct block is left alone', () => {
  const s = '$$\nx^2\n$$'
  assert.equal(normalizeMathBlocks(s), s)
})

test('a formula sharing the opening line is moved below it', () => {
  assert.equal(normalizeMathBlocks('$$x^2\n$$'), '$$\nx^2\n$$')
})

test('a closing fence stuck to the formula is split off', () => {
  assert.equal(normalizeMathBlocks('$$\nx^2$$'), '$$\nx^2\n$$')
})

test('surrounding prose and blank lines survive', () => {
  const out = normalizeMathBlocks('Before.\n\n$$E = mc^2$$\n\nAfter.')
  assert.equal(out, 'Before.\n\n$$\nE = mc^2\n$$\n\nAfter.')
})

test('indentation on the delimiters is preserved', () => {
  assert.equal(normalizeMathBlocks('  $$a+b$$'), '  $$\na+b\n  $$')
})

test('several blocks in one document are each normalised', () => {
  const out = normalizeMathBlocks('$$a$$\n\ntext\n\n$$b$$')
  assert.equal(out, '$$\na\n$$\n\ntext\n\n$$\nb\n$$')
})

test('a multi-line block keeps its inner lines', () => {
  const src = '$$\n\\begin{aligned}\na &= b \\\\\nc &= d\n\\end{aligned}\n$$'
  assert.equal(normalizeMathBlocks(src), src)
})

test('$$ inside a fenced code block is not treated as maths', () => {
  const src = '```sh\necho $$\ncat <<EOF\n$$x^2$$\nEOF\n```'
  assert.equal(normalizeMathBlocks(src), src)
})

test('a tilde fence is respected too', () => {
  const src = '~~~\n$$x^2$$\n~~~'
  assert.equal(normalizeMathBlocks(src), src)
})

test('maths after a closed code fence is still normalised', () => {
  const out = normalizeMathBlocks('```\n$$skip$$\n```\n\n$$real$$')
  assert.equal(out, '```\n$$skip$$\n```\n\n$$\nreal\n$$')
})

test('inline maths on a line that merely contains $$ later is not restructured', () => {
  // The line does not *start* with $$, so it stays one line and remark-math reads it inline.
  const s = 'Note that $a$ and then $$b$$ appear together.'
  assert.equal(normalizeMathBlocks(s), s)
})

test('an unterminated block is left unterminated rather than guessed at', () => {
  // Mid-typing: inventing a closing fence would swallow the rest of the document.
  assert.equal(normalizeMathBlocks('$$x^2'), '$$\nx^2')
})

test('empty delimiters are not mangled', () => {
  assert.equal(normalizeMathBlocks('$$$$'), '$$\n$$')
  assert.equal(normalizeMathBlocks('$$\n$$'), '$$\n$$')
})

test('a real LaTeX environment with dollars inside survives a round trip', () => {
  const out = normalizeMathBlocks('$$q(x_t \\mid x_{t-1}) = \\mathcal{N}(x_t; \\sqrt{1-\\beta_t} x_{t-1}, \\beta_t I)$$')
  assert.equal(out, '$$\nq(x_t \\mid x_{t-1}) = \\mathcal{N}(x_t; \\sqrt{1-\\beta_t} x_{t-1}, \\beta_t I)\n$$')
})

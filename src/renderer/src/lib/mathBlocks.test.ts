import test from 'node:test'
import assert from 'node:assert/strict'
import { unified } from 'unified'
import remarkParse from 'remark-parse'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
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

// ---- prices, bracket delimiters, and the parser itself ----

const n = normalizeMathBlocks

test('prices in one sentence stay literal', () => {
  assert.equal(n('It costs $5 and $10 per seat.'), 'It costs \\$5 and $10 per seat.')
  assert.equal(n('Between $1,200 and $3,400 a month.'), 'Between \\$1,200 and $3,400 a month.')
  assert.equal(n('Revenue rose from $5M to $10M.'), 'Revenue rose from \\$5M to $10M.')
  assert.equal(n('($5, was $10)'), '(\\$5, was $10)')
  assert.equal(n('$5-$10'), '\\$5-$10')
  assert.equal(n('Costs $5\nand then $10 more'), 'Costs \\$5\nand then $10 more')
})

test('a price next to a formula keeps the formula', () => {
  assert.equal(n('Paid $20; with $x^2$ maths.'), 'Paid \\$20; with $x^2$ maths.')
  assert.equal(n('$x^2$ and $2x+1$'), '$x^2$ and $2x+1$')
  assert.equal(n('$5$'), '$5$')
})

test('bracket delimiters become dollar maths', () => {
  assert.equal(n('\\(a\\) and \\(5\\)'), '$a$ and $5$')
  assert.equal(n('\\[\nx^2\n\\]'), '$$\nx^2\n$$')
  assert.equal(n('\\[ x^2 \\]'), '$$\nx^2\n$$')
  assert.equal(n('so \\[ x^2 \\] holds'), 'so $$x^2$$ holds')
  assert.equal(n('\\[\nx^2\\]'), '$$\nx^2\n$$')
  assert.equal(n('\\[\nx^2'), '$$\nx^2')
})

test('citations, links and escaped dollars are left alone', () => {
  for (const s of ['\\[1\\] Smith et al.', '\\[not a link\\](http://x)', '\\$5 and \\$10']) assert.equal(n(s), s)
})

test('fences and inline code are untouched', () => {
  for (const s of ['```\ncost $5 and $10 \\(x\\)\n```', 'run `echo $5 and $10` now', '~~~\n\\[\nx\n\\]\n~~~']) assert.equal(n(s), s)
})

test('normalising twice changes nothing', () => {
  for (const s of ['It costs $5 and $10 per seat.', '\\(a\\) \\[ x^2 \\]', '\\[\nx^2\n\\]', 'Paid $20; with $x^2$ maths.', '$$a$$ and $5 or $6']) {
    assert.equal(n(n(s)), n(s))
  }
})

/** What the parser makes of the normalised source: the values of every inline and display formula. */
const formulas = (src: string): string[] => {
  const out: string[] = []
  const walk = (node: { type: string; value?: string; children?: unknown[] }): void => {
    if (node.type === 'inlineMath' || node.type === 'math') out.push(String(node.value))
    for (const c of node.children ?? []) walk(c as never)
  }
  walk(unified().use(remarkParse).use(remarkGfm).use(remarkMath).parse(n(src)) as never)
  return out
}

test('the parser sees no formula in a price pair, however it is split', () => {
  assert.deepEqual(formulas('It costs $5 and $10 per seat.'), [])
  assert.deepEqual(formulas('Revenue rose from $5M to $10M.'), [])
  assert.deepEqual(formulas('Costs $5\nand then $10 more'), [])
  assert.deepEqual(formulas('`$a` and `$b`'), [])
})

test('the parser sees exactly the intended formulas', () => {
  assert.deepEqual(formulas('Paid $20; with $x^2$ maths.'), ['x^2'])
  assert.deepEqual(formulas('\\$5 and $10 per seat, where $x$ is n'), ['x'])
  assert.deepEqual(formulas('$x^2$ and $2x+1$'), ['x^2', '2x+1'])
  assert.deepEqual(formulas('\\(x^2\\)'), ['x^2'])
  assert.deepEqual(formulas('\\[\nx^2\n\\]'), ['x^2'])
  assert.deepEqual(formulas('\\[ x^2 \\]'), ['x^2'])
})

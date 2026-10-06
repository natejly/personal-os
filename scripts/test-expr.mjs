/**
 * Tests for the interactive-chart expression language (src/renderer/src/lib/expr.ts).
 *
 * Run with `npm run test:expr`. There is no frontend test runner in this project, and the evaluator is
 * the one piece of renderer code where a parsing mistake is a correctness *and* a safety problem, so
 * this transpiles the module with the `typescript` devDep already present and asserts against it —
 * no new dependency.
 */
import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, dirname } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import ts from 'typescript'

const root = dirname(dirname(fileURLToPath(import.meta.url)))
const src = readFileSync(join(root, 'src/renderer/src/lib/expr.ts'), 'utf8')
const js = ts.transpileModule(src, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText
const out = join(mkdtempSync(join(tmpdir(), 'expr-test-')), 'expr.mjs')
writeFileSync(out, js)
const { compile, tryCompile, RESERVED } = await import(pathToFileURL(out).href)

let passed = 0
const failures = []

const check = (name, fn) => {
  try {
    fn()
    passed++
  } catch (e) {
    failures.push(`${name}: ${e.message}`)
  }
}

/** `expr` evaluated against `scope` must equal `want` (numbers compared to 1e-9). */
const is = (source, want, scope = {}) =>
  check(`${source}${Object.keys(scope).length ? ` with ${JSON.stringify(scope)}` : ''}`, () => {
    const got = compile(source).eval(scope)
    const ok = typeof want === 'number' && typeof got === 'number'
      ? Math.abs(got - want) < 1e-9 || (Number.isNaN(want) && Number.isNaN(got))
      : got === want
    if (!ok) throw new Error(`expected ${JSON.stringify(want)}, got ${JSON.stringify(got)}`)
  })

/** Compiling `source` must fail, with a message containing `fragment`. */
const fails = (source, fragment) =>
  check(`rejects ${JSON.stringify(source)}`, () => {
    const r = tryCompile(source)
    if (r.ok) throw new Error('expected a compile error, got a working expression')
    if (fragment && !r.error.toLowerCase().includes(fragment.toLowerCase())) {
      throw new Error(`error ${JSON.stringify(r.error)} does not mention ${JSON.stringify(fragment)}`)
    }
  })

// ---- arithmetic and precedence
is('1', 1)
is('1 + 2 * 3', 7)
is('(1 + 2) * 3', 9)
is('10 / 4', 2.5)
is('7 % 3', 1)
is('1.5e2', 150)
is('.5 + .25', 0.75)
// `^` is right-associative and binds tighter than unary minus.
is('2 ^ 3 ^ 2', 512)
is('-2 ^ 2', -4)
is('2 ^ -1', 0.5)
is('2 ** 3', 8, {}) // ** is accepted as an alias for ^
is('-  -3', 3)
is('1 - -1', 2)

// ---- variables
is('x * 2', 42, { x: 21 })
is('a + b', 5, { a: 2, b: 3 })
is('x', NaN, {}) // a missing variable is NaN, never an exception
is('`Revenue (USD)` / 2', 50, { 'Revenue (USD)': 100 }) // backticks reach a non-word data column
check('collects free variables but not functions or constants', () => {
  const vars = [...compile('a + b * sin(c) + pi').vars].sort()
  if (vars.join(',') !== 'a,b,c') throw new Error(`got ${vars.join(',')}`)
})

// ---- comparison, logic, conditionals
is('3 > 2', 1)
is('2 >= 2', 1)
is('1 < 0', 0)
is('1 == 1', 1)
is('1 === 1', 1) // tolerated spelling
is('1 != 2', 1)
is('!0', 1)
is('!5', 0)
is('1 && 0', 0)
is('0 || 3', 1)
is('x > 0 ? 1 : -1', -1, { x: -5 })
is('x > 0 ? 1 : -1', 1, { x: 5 })
is('if(x > 0, 10, 20)', 20, { x: -1 })
is('mode == "monthly"', 1, { mode: 'monthly' })
is('mode == "monthly"', 0, { mode: 'yearly' })
is("mode != 'yearly'", 1, { mode: 'monthly' })
is('on ? 2 : 3', 2, { on: 1 })
// NaN compares false in both directions, as in JS.
is('x > 1', 0, { x: NaN })
is('x < 1', 0, { x: NaN })
// `if` is lazy: the dead branch must not poison the result.
is('if(x > 0, log(x), -1)', -1, { x: 0 })

// ---- functions
is('abs(-3)', 3)
is('sqrt(16)', 4)
is('pow(2, 10)', 1024)
is('min(3, 1, 2)', 1)
is('max(3, 1, 2)', 3)
is('hypot(3, 4)', 5)
is('clamp(15, 0, 10)', 10)
is('clamp(-5, 0, 10)', 0)
is('round(3.14159, 2)', 3.14)
is('round(2.5)', 3)
is('floor(2.9)', 2)
is('ceil(2.1)', 3)
is('sign(-9)', -1)
is('lerp(0, 10, 0.25)', 2.5)
is('mod(-1, 3)', 2) // floor-mod, unlike JS %
is('-1 % 3', -1) // % keeps JS semantics
is('logb(8, 2)', 3)
is('sqr(5)', 25)
is('step(5, 6)', 1)
is('step(5, 4)', 0)
check('pi and e are constants', () => {
  if (Math.abs(compile('pi').eval({}) - Math.PI) > 1e-12) throw new Error('pi wrong')
  if (Math.abs(compile('E').eval({}) - Math.E) > 1e-12) throw new Error('E wrong')
})

// ---- number(): non-finite becomes null so a chart draws a gap
check('number() maps 1/0 to null', () => {
  if (compile('1 / 0').number({}) !== null) throw new Error('expected null')
})
check('number() maps sqrt(-1) to null', () => {
  if (compile('sqrt(-1)').number({}) !== null) throw new Error('expected null')
})
check('number() returns a finite value normally', () => {
  if (compile('2 + 2').number({}) !== 4) throw new Error('expected 4')
})
check('number() coerces a numeric string from scope', () => {
  if (compile('x + 1').number({ x: '41' }) !== 42) throw new Error('expected 42')
})

// ---- syntax errors are reported, not silently wrong
fails('', 'empty')
fails('1 +', 'ended unexpectedly')
fails('(1 + 2', 'expected')
fails('1 2', 'unexpected')
fails('foo(1)', 'unknown function')
fails('pow(1)', 'takes 2 arguments')
fails('clamp(1, 2)', 'takes 3 arguments')
fails('if(1, 2)', '3 arguments')
fails('a'.repeat(2001), 'too long')
fails('1' + '+1'.repeat(500), 'too complex')

// ---- the grammar has no escape hatch: these are all syntax errors, never host access
fails('x.constructor', 'unexpected character')
fails('x["y"]', 'unexpected character')
fails('x = 1', 'unexpected character')
fails('a; b', 'unexpected character')
fails('() => 1', 'unexpected')
fails('x.y.z', 'unexpected character')
fails('new Date()', 'unexpected') // `new` parses as a name, so the next name is a syntax error
check('a bare host name is only ever an unknown variable', () => {
  // These parse as plain variable names, so they read as NaN from an empty scope rather than reaching
  // anything real — including the ones a `{}` scope inherits from Object.prototype. Calling one is an error.
  for (const name of ['constructor', 'toString', 'valueOf', '__proto__', 'hasOwnProperty', 'this',
                      'globalThis', 'window', 'process', 'require', 'eval', 'Function']) {
    const got = compile(name).eval({})
    if (!Number.isNaN(got)) throw new Error(`${name} evaluated to ${JSON.stringify(got)}`)
    if (tryCompile(`${name}("x")`).ok) throw new Error(`${name}() compiled`)
  }
  if (RESERVED.has('eval') || RESERVED.has('Function')) throw new Error('host name is reserved')
})

if (failures.length) {
  console.error(`\n${failures.length} failure(s):`)
  for (const f of failures) console.error(`  ✗ ${f}`)
  console.error(`\n${passed} passed, ${failures.length} failed`)
  process.exit(1)
}
console.log(`expr: ${passed} checks passed`)

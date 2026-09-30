/**
 * A tiny arithmetic expression language, used by ```interactive chart blocks.
 *
 * Deliberately not `eval` / `new Function`. The formulas in an interactive block are written by the
 * model, and model output can carry text from a page a tool fetched — the backend tracks exactly that
 * as `tainted` — so a formula must never be able to turn into code execution in the renderer, which
 * holds the preload bridge. This grammar has no property access, no indexing, no assignment and no
 * statements: only numbers, strings, a fixed operator set and a fixed whitelist of maths functions.
 * There are no loops either, so evaluation cost is bounded by the size of the parsed expression.
 * The worst a hostile formula can do is return NaN.
 */

export type Value = number | string
export type Scope = Record<string, Value | undefined>

const MAX_LENGTH = 2000
const MAX_NODES = 400
const MAX_ARGS = 8

/**
 * Every name table here has a null prototype, and every lookup below goes through `has`. A plain object
 * literal inherits `constructor`, `toString`, `valueOf` and `__proto__` from Object.prototype, so
 * `name in CONSTANTS` or `FUNCTIONS[name]` would otherwise resolve those to real host functions and
 * hand them straight back to a model-authored formula.
 */
const has = (o: object, name: string): boolean => Object.prototype.hasOwnProperty.call(o, name)

/** Bare names that resolve to a constant rather than a scope lookup. */
const CONSTANTS: Record<string, number> = Object.assign(Object.create(null), {
  pi: Math.PI, PI: Math.PI, e: Math.E, E: Math.E, tau: Math.PI * 2, TAU: Math.PI * 2
})

type Fn = { min: number; max: number; call: (a: number[]) => number }
const fn = (arity: number, call: (a: number[]) => number): Fn => ({ min: arity, max: arity, call })
const variadic = (call: (a: number[]) => number): Fn => ({ min: 1, max: MAX_ARGS, call })
const un = (f: (x: number) => number): Fn => fn(1, (a) => f(a[0]))

/**
 * Every callable name. Pure number→number maths only: nothing here can reach a host object, and
 * `if` is handled separately in the compiler because its branches are lazy.
 */
export const FUNCTIONS: Record<string, Fn> = Object.assign(Object.create(null) as Record<string, Fn>, {
  abs: un(Math.abs), sqrt: un(Math.sqrt), cbrt: un(Math.cbrt), exp: un(Math.exp),
  log: un(Math.log), ln: un(Math.log), log2: un(Math.log2), log10: un(Math.log10),
  sin: un(Math.sin), cos: un(Math.cos), tan: un(Math.tan),
  asin: un(Math.asin), acos: un(Math.acos), atan: un(Math.atan),
  sinh: un(Math.sinh), cosh: un(Math.cosh), tanh: un(Math.tanh),
  sign: un(Math.sign), floor: un(Math.floor), ceil: un(Math.ceil), trunc: un(Math.trunc),
  sqr: un((x) => x * x),
  pow: fn(2, (a) => Math.pow(a[0], a[1])),
  atan2: fn(2, (a) => Math.atan2(a[0], a[1])),
  mod: fn(2, (a) => a[0] - a[1] * Math.floor(a[0] / a[1])),
  logb: fn(2, (a) => Math.log(a[0]) / Math.log(a[1])),
  lerp: fn(3, (a) => a[0] + (a[1] - a[0]) * a[2]),
  clamp: fn(3, (a) => Math.min(Math.max(a[0], a[1]), a[2])),
  step: fn(2, (a) => (a[1] >= a[0] ? 1 : 0)),
  min: variadic((a) => Math.min(...a)),
  max: variadic((a) => Math.max(...a)),
  hypot: variadic((a) => Math.hypot(...a)),
  round: { min: 1, max: 2, call: (a: number[]) => { const p = Math.pow(10, a.length > 1 ? Math.trunc(a[1]) : 0); return Math.round(a[0] * p) / p } }
})

/** Names the grammar understands without a scope entry — used to work out an expression's free variables. */
export const RESERVED: ReadonlySet<string> = new Set([...Object.keys(FUNCTIONS), ...Object.keys(CONSTANTS), 'if'])

export class ExprError extends Error {}

// ---------------------------------------------------------------- coercion

export function toNumber(v: Value | undefined): number {
  if (typeof v === 'number') return v
  if (typeof v === 'string' && v.trim() !== '') return Number(v)
  return NaN
}

/** Truthiness for `&&`, `||`, `!` and `if`: a non-zero number, or a non-empty string. */
const truthy = (v: Value): boolean => (typeof v === 'number' ? v !== 0 && !Number.isNaN(v) : v !== '')

/** `==` and `<` compare as text when either side is non-numeric text, so `mode == "monthly"` works. */
function compare(a: Value, b: Value): number {
  const isText = (v: Value): boolean => typeof v === 'string' && (v.trim() === '' || Number.isNaN(Number(v)))
  if (isText(a) || isText(b)) {
    const x = String(a), y = String(b)
    return x < y ? -1 : x > y ? 1 : 0
  }
  const x = toNumber(a), y = toNumber(b)
  return x < y ? -1 : x > y ? 1 : 0
}

// ---------------------------------------------------------------- tokenizer

type TokKind = 'num' | 'str' | 'name' | 'op' | 'end'
interface Tok { kind: TokKind; text: string; num?: number; pos: number }

const OPS3 = ['===', '!=='] // tolerated spellings, folded onto == / !=
const OPS2 = ['<=', '>=', '==', '!=', '&&', '||', '**']
const OPS1 = '+-*/%^()<>!?:,'

function tokenize(src: string): Tok[] {
  const out: Tok[] = []
  let i = 0
  while (i < src.length) {
    const c = src[i]
    if (c === ' ' || c === '\t' || c === '\n' || c === '\r') { i++; continue }
    const start = i
    if (/[0-9]/.test(c) || (c === '.' && /[0-9]/.test(src[i + 1] ?? ''))) {
      const m = /^(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?/.exec(src.slice(i))
      if (!m) throw new ExprError(`Bad number at position ${i}`)
      i += m[0].length
      out.push({ kind: 'num', text: m[0], num: Number(m[0]), pos: start })
      continue
    }
    if (c === '"' || c === "'") {
      i++
      let s = ''
      while (i < src.length && src[i] !== c) {
        if (src[i] === '\\' && i + 1 < src.length) { s += src[i + 1]; i += 2 } else { s += src[i]; i++ }
      }
      if (i >= src.length) throw new ExprError('Unterminated string')
      i++
      out.push({ kind: 'str', text: s, pos: start })
      continue
    }
    // A backtick-quoted name lets a formula reference a data column whose key is not a bare word.
    if (c === '`') {
      i++
      let s = ''
      while (i < src.length && src[i] !== '`') { s += src[i]; i++ }
      if (i >= src.length) throw new ExprError('Unterminated `name`')
      i++
      if (!s) throw new ExprError('Empty `name`')
      out.push({ kind: 'name', text: s, pos: start })
      continue
    }
    if (/[A-Za-z_]/.test(c)) {
      const m = /^[A-Za-z_][A-Za-z0-9_]*/.exec(src.slice(i))!
      i += m[0].length
      out.push({ kind: 'name', text: m[0], pos: start })
      continue
    }
    if (OPS3.includes(src.slice(i, i + 3))) { const t = src.slice(i, i + 2); i += 3; out.push({ kind: 'op', text: t, pos: start }); continue }
    const two = src.slice(i, i + 2)
    if (OPS2.includes(two)) { i += 2; out.push({ kind: 'op', text: two === '**' ? '^' : two, pos: start }); continue }
    if (OPS1.includes(c)) { i++; out.push({ kind: 'op', text: c, pos: start }); continue }
    throw new ExprError(`Unexpected character ${JSON.stringify(c)} at position ${i}`)
  }
  out.push({ kind: 'end', text: '', pos: src.length })
  return out
}

// ---------------------------------------------------------------- parser → closures

type Node = (scope: Scope) => Value

export interface Compiled {
  readonly source: string
  /** Scope keys the expression reads, excluding constants and function names. */
  readonly vars: readonly string[]
  /** Raw result; may be a string when the formula compares or returns text. */
  eval(scope: Scope): Value
  /** Numeric result, with non-finite values mapped to null so a chart draws a gap instead of breaking its axis. */
  number(scope: Scope): number | null
}

/**
 * Parse and compile `source` into a tree of closures. Throws `ExprError` on a syntax problem, an
 * unknown function or a size limit, so a block can report the failure instead of drawing NaN.
 */
export function compile(source: string): Compiled {
  if (typeof source !== 'string') throw new ExprError('Expression must be a string')
  const src = source.trim()
  if (!src) throw new ExprError('Expression is empty')
  if (src.length > MAX_LENGTH) throw new ExprError(`Expression is too long (limit ${MAX_LENGTH} characters)`)

  const toks = tokenize(src)
  const vars = new Set<string>()
  let p = 0
  let nodes = 0

  const peek = (): Tok => toks[p]
  const isOp = (t: string): boolean => peek().kind === 'op' && peek().text === t
  const eat = (t: string): boolean => (isOp(t) ? (p++, true) : false)
  const expect = (t: string): void => { if (!eat(t)) throw new ExprError(`Expected ${JSON.stringify(t)} at position ${peek().pos}`) }
  const count = (): void => { if (++nodes > MAX_NODES) throw new ExprError('Expression is too complex') }

  const binary = (next: () => Node, ops: string[], make: (op: string, l: Node, r: Node) => Node): Node => {
    let left = next()
    for (;;) {
      const t = peek()
      if (t.kind !== 'op' || !ops.includes(t.text)) return left
      p++
      count()
      left = make(t.text, left, next())
    }
  }

  const ternary = (): Node => {
    const cond = or()
    if (!eat('?')) return cond
    count()
    const a = ternary()
    expect(':')
    const b = ternary()
    return (s) => (truthy(cond(s)) ? a(s) : b(s))
  }
  const or = (): Node => binary(and, ['||'], (_o, l, r) => (s) => (truthy(l(s)) || truthy(r(s)) ? 1 : 0))
  const and = (): Node => binary(equality, ['&&'], (_o, l, r) => (s) => (truthy(l(s)) && truthy(r(s)) ? 1 : 0))
  const equality = (): Node => binary(relational, ['==', '!='], (o, l, r) =>
    o === '==' ? (s) => (compare(l(s), r(s)) === 0 ? 1 : 0) : (s) => (compare(l(s), r(s)) !== 0 ? 1 : 0))
  const relational = (): Node => binary(additive, ['<', '<=', '>', '>='], (o, l, r) => {
    const ok = o === '<' ? (c: number) => c < 0 : o === '<=' ? (c: number) => c <= 0 : o === '>' ? (c: number) => c > 0 : (c: number) => c >= 0
    return (s) => {
      const a = l(s), b = r(s)
      // NaN compares false in every direction, as it does in JS.
      if (typeof a === 'number' && Number.isNaN(a)) return 0
      if (typeof b === 'number' && Number.isNaN(b)) return 0
      return ok(compare(a, b)) ? 1 : 0
    }
  })
  const additive = (): Node => binary(multiplicative, ['+', '-'], (o, l, r) =>
    o === '+' ? (s) => toNumber(l(s)) + toNumber(r(s)) : (s) => toNumber(l(s)) - toNumber(r(s)))
  const multiplicative = (): Node => binary(unaryLevel, ['*', '/', '%'], (o, l, r) =>
    o === '*' ? (s) => toNumber(l(s)) * toNumber(r(s))
      : o === '/' ? (s) => toNumber(l(s)) / toNumber(r(s))
        : (s) => toNumber(l(s)) % toNumber(r(s)))

  function unaryLevel(): Node {
    const t = peek()
    if (t.kind === 'op' && (t.text === '-' || t.text === '+' || t.text === '!')) {
      p++
      count()
      const arg = unaryLevel()
      if (t.text === '-') return (s) => -toNumber(arg(s))
      if (t.text === '+') return (s) => toNumber(arg(s))
      return (s) => (truthy(arg(s)) ? 0 : 1)
    }
    return power()
  }

  // `^` binds tighter than unary minus (-2^2 is -4) and is right-associative (2^3^2 is 2^9).
  function power(): Node {
    const base = primary()
    if (!eat('^')) return base
    count()
    const exp = unaryLevel()
    return (s) => Math.pow(toNumber(base(s)), toNumber(exp(s)))
  }

  function args(): Node[] {
    const out: Node[] = []
    expect('(')
    if (eat(')')) return out
    for (;;) {
      out.push(ternary())
      if (eat(')')) return out
      expect(',')
      if (out.length > MAX_ARGS) throw new ExprError(`Too many arguments (limit ${MAX_ARGS})`)
    }
  }

  function primary(): Node {
    const t = peek()
    count()
    if (t.kind === 'num') { p++; const v = t.num!; return () => v }
    if (t.kind === 'str') { p++; const v = t.text; return () => v }
    if (t.kind === 'op' && t.text === '(') { p++; const inner = ternary(); expect(')'); return inner }
    if (t.kind === 'name') {
      p++
      const name = t.text
      if (isOp('(')) {
        const list = args()
        if (name === 'if') {
          if (list.length !== 3) throw new ExprError('if(condition, then, else) takes 3 arguments')
          const [c, a, b] = list
          return (s) => (truthy(c(s)) ? a(s) : b(s))
        }
        const f = has(FUNCTIONS, name) ? FUNCTIONS[name] : undefined
        if (!f) throw new ExprError(`Unknown function ${name}(). Available: ${Object.keys(FUNCTIONS).sort().join(', ')}, if`)
        if (list.length < f.min || list.length > f.max) {
          throw new ExprError(`${name}() takes ${f.min === f.max ? f.min : `${f.min}–${f.max}`} argument${f.max === 1 ? '' : 's'}, got ${list.length}`)
        }
        return (s) => f.call(list.map((a) => toNumber(a(s))))
      }
      if (has(CONSTANTS, name)) { const v = CONSTANTS[name]; return () => v }
      vars.add(name)
      return (s) => {
        // Own properties only, and primitives only: the caller's scope is usually a plain object, so an
        // inherited name must read as NaN like any other unknown one, and a non-primitive value in scope
        // must never flow into the chart.
        if (!has(s, name)) return NaN
        const v = s[name]
        return typeof v === 'number' || typeof v === 'string' ? v : NaN
      }
    }
    throw new ExprError(t.kind === 'end' ? 'Expression ended unexpectedly' : `Unexpected ${JSON.stringify(t.text)} at position ${t.pos}`)
  }

  const root = ternary()
  if (peek().kind !== 'end') throw new ExprError(`Unexpected ${JSON.stringify(peek().text)} at position ${peek().pos}`)

  return {
    source: src,
    vars: Object.freeze([...vars]),
    eval: (scope) => root(scope),
    number: (scope) => {
      const n = toNumber(root(scope))
      return Number.isFinite(n) ? n : null
    }
  }
}

/** `compile`, reporting a failure as a value instead of throwing. */
export function tryCompile(source: string): { ok: true; expr: Compiled } | { ok: false; error: string } {
  try { return { ok: true, expr: compile(source) } } catch (e) { return { ok: false, error: (e as Error).message } }
}

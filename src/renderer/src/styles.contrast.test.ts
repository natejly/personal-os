import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { ACCENTS as ACCENT_LIST } from './lib/appearance'

// WCAG contrast guard for the design tokens. It parses the token blocks out of the real stylesheets (nothing is
// copied here), resolves var() / color-mix() / rgba alpha, and fails loudly when a value cannot be resolved.
// Run from the repo root (npm test does).

const SRC = join(process.cwd(), 'src/renderer/src')
// Derived from the app's own option lists, so a new accent or tone is covered the day it is added.
const ACCENTS: string[] = ACCENT_LIST.map((a) => a.id)
const THEMES = ['dark', 'light'] as const
type Theme = (typeof THEMES)[number]

// ---- tiny CSS reader: rules with their enclosing @media, and `prop: value` declarations ----
type Rule = { sel: string; media: string; decls: Map<string, string> }

function stripComments(css: string): string {
  return css.replace(/\/\*[\s\S]*?\*\//g, '')
}

function parseDecls(body: string): Map<string, string> {
  const out = new Map<string, string>()
  for (const m of body.matchAll(/([\w-]+)\s*:\s*([^;]+);?/g)) out.set(m[1], m[2].trim())
  return out
}

function parseRules(css: string, media = ''): Rule[] {
  const rules: Rule[] = []
  let i = 0
  while (i < css.length) {
    const open = css.indexOf('{', i)
    if (open < 0) break
    let depth = 1
    let j = open + 1
    while (j < css.length && depth > 0) {
      if (css[j] === '{') depth++
      else if (css[j] === '}') depth--
      j++
    }
    assert.equal(depth, 0, 'unbalanced braces in stylesheet')
    const prelude = css.slice(i, open).trim()
    const inner = css.slice(open + 1, j - 1)
    if (prelude.startsWith('@media')) rules.push(...parseRules(inner, prelude))
    else if (prelude.startsWith('@')) { /* keyframes, font-face: no tokens here */ }
    else rules.push({ sel: prelude.replace(/\s+/g, ' '), media, decls: parseDecls(inner) })
    i = j
  }
  return rules
}

const readCss = (path: string) => parseRules(stripComments(readFileSync(path, 'utf8')))
const styles = readCss(join(SRC, 'styles.css'))

function allCssFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const p = join(dir, name)
    return statSync(p).isDirectory() ? allCssFiles(p) : p.endsWith('.css') ? [p] : []
  })
}

function ruleFor(sel: string, media = ''): Rule {
  const r = styles.find((x) => x.sel === sel && x.media === media)
  assert.ok(r, `no rule ${sel} ${media}`)
  return r
}

// ---- colour maths ----
type RGBA = { r: number; g: number; b: number; a: number }

function splitTop(s: string): string[] {
  const parts: string[] = []
  let depth = 0
  let cur = ''
  for (const ch of s) {
    if (ch === '(') depth++
    if (ch === ')') depth--
    if (ch === ',' && depth === 0) { parts.push(cur.trim()); cur = '' } else cur += ch
  }
  parts.push(cur.trim())
  return parts
}

function parseColor(value: string, vars: Map<string, string>, trail: string[] = []): RGBA {
  const v = value.trim()
  const fail = (why: string): never => assert.fail(`cannot resolve "${v}" (${why})${trail.length ? ' via ' + trail.join(' > ') : ''}`)
  if (v.startsWith('var(')) {
    const [name, ...fallback] = splitTop(v.slice(4, -1))
    const hit = vars.get(name)
    if (hit !== undefined) return parseColor(hit, vars, [...trail, name])
    return fallback.length ? parseColor(fallback.join(','), vars, trail) : fail(`${name} is not defined`)
  }
  if (v === 'transparent') return { r: 0, g: 0, b: 0, a: 0 }
  const hex = v.match(/^#([0-9a-f]{3}|[0-9a-f]{6})$/i)
  if (hex) {
    const h = hex[1].length === 3 ? hex[1].replace(/./g, '$&$&') : hex[1]
    return { r: parseInt(h.slice(0, 2), 16), g: parseInt(h.slice(2, 4), 16), b: parseInt(h.slice(4, 6), 16), a: 1 }
  }
  const fn = v.match(/^rgba?\((.*)\)$/)
  if (fn) {
    const n = fn[1].split(/[,\s/]+/).filter(Boolean).map(Number)
    if (n.length < 3 || n.some(Number.isNaN)) fail('bad rgb()')
    return { r: n[0], g: n[1], b: n[2], a: n[3] ?? 1 }
  }
  if (v.startsWith('color-mix(')) {
    const [space, ...ends] = splitTop(v.slice(10, -1))
    if (space !== 'in srgb' || ends.length !== 2) fail('only two-colour "in srgb" mixes are supported')
    const end = (s: string) => {
      const m = s.match(/^(.*?)(?:\s+([\d.]+)%)?$/)!
      return { c: parseColor(m[1], vars, trail), p: m[2] === undefined ? undefined : Number(m[2]) }
    }
    const [x, y] = ends.map(end)
    const px = x.p ?? (y.p === undefined ? 50 : 100 - y.p)
    const py = y.p ?? 100 - px
    // Premultiplied mix, as CSS does it, so `transparent` as the second colour just scales alpha.
    const wx = (px / 100) * x.c.a
    const wy = (py / 100) * y.c.a
    const a = (px / 100) * x.c.a + (py / 100) * y.c.a
    if (a === 0) return { r: 0, g: 0, b: 0, a: 0 }
    return {
      r: (x.c.r * wx + y.c.r * wy) / a,
      g: (x.c.g * wx + y.c.g * wy) / a,
      b: (x.c.b * wx + y.c.b * wy) / a,
      a
    }
  }
  return fail('unsupported colour syntax')
}

const over = (fg: RGBA, bg: RGBA): RGBA => ({
  r: fg.r * fg.a + bg.r * (1 - fg.a),
  g: fg.g * fg.a + bg.g * (1 - fg.a),
  b: fg.b * fg.a + bg.b * (1 - fg.a),
  a: 1
})
const lum = ({ r, g, b }: RGBA) => {
  const [R, G, B] = [r, g, b].map((v) => { const c = v / 255; return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4 })
  return 0.2126 * R + 0.7152 * G + 0.0722 * B
}
const ratio = (a: RGBA, b: RGBA) => {
  const [x, y] = [lum(a), lum(b)]
  return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05)
}

// ---- the token set a theme + accent resolves to ----
// Cascade order: dark root, then the dark accent rules, then (light only) the light theme block and the light accent
// rules, which win on specificity. 'sage' is the base and has no rule.
function tokens(theme: Theme, accent: string): Map<string, string> {
  const vars = new Map<string, string>()
  const layer = (sel: string, media = '') => {
    const r = styles.find((x) => x.sel === sel && x.media === media)
    if (r) for (const [k, v] of r.decls) if (k.startsWith('--')) vars.set(k, v)
    return r
  }
  const tones = (prefix: string) => {
    if (accent !== 'sage') assert.ok(layer(`${prefix}[data-accent='${accent}']`), `no ${prefix} accent rule for ${accent}`)
  }
  layer(':root')
  tones(':root')
  if (theme === 'light') {
    assert.ok(layer(`:root[data-theme='light']`), 'no light theme block')
    tones(`:root[data-theme='light']`)
  }
  return vars
}

const tok = (vars: Map<string, string>, name: string, trail = ''): RGBA => parseColor(`var(${name})`, vars, trail ? [trail] : [])
const label = (theme: string, accent: string, what: string) => `${theme}/${accent}: ${what}`

function expectAtLeast(min: number, fg: RGBA, bg: RGBA, what: string) {
  const r = ratio(fg, bg)
  assert.ok(r >= min, `${what} is ${r.toFixed(2)}:1, needs ${min}:1`)
}

// ---- tests ----
test('the system-theme light block is identical to the light theme block', () => {
  const norm = (r: Rule) => [...r.decls].map(([k, v]) => `${k}:${v}`).join(';')
  const light = ruleFor(`:root[data-theme='light']`)
  const system = ruleFor(`:root[data-theme='system']`, '@media (prefers-color-scheme: light)')
  assert.equal(norm(system), norm(light), 'system block drifted from the light block')
  for (const a of ACCENTS.filter((x) => x !== 'sage')) {
    const l = ruleFor(`:root[data-theme='light'][data-accent='${a}']`)
    const s = ruleFor(`:root[data-theme='system'][data-accent='${a}']`, '@media (prefers-color-scheme: light)')
    assert.equal(norm(s), norm(l), `system ${a} accent drifted from the light one`)
    // A light accent must replace every accent token the dark variant sets, or the dark value leaks through.
    const dark = ruleFor(`:root[data-accent='${a}']`)
    for (const k of dark.decls.keys()) assert.ok(l.decls.has(k), `light ${a} accent does not set ${k}`)
  }
})

test('the default font option has no rules: they are the base tokens', () => {
  for (const r of styles) {
    assert.ok(!/data-font='system'/.test(r.sel), `${r.sel} would change the default look`)
  }
})

test('--on-accent clears 4.5:1 on every accent solid and its hover, in both themes', () => {
  for (const theme of THEMES) for (const accent of ACCENTS) {
    const v = tokens(theme, accent)
    for (const solid of ['--accent-solid', '--accent-solid-hover']) {
      expectAtLeast(4.5, tok(v, '--on-accent'), tok(v, solid), label(theme, accent, `--on-accent on ${solid}`))
    }
  }
})

test('--accent, --text, --text-muted and the status colours read as text on --bg and --bg-elev', () => {
  for (const theme of THEMES) for (const accent of ACCENTS) {
    const v = tokens(theme, accent)
    for (const surface of ['--bg', '--bg-elev']) {
      for (const fg of ['--accent', '--accent-hover', '--text', '--text-muted', '--danger', '--ok', '--warn', '--info']) {
        expectAtLeast(4.5, tok(v, fg), tok(v, surface), label(theme, accent, `${fg} on ${surface}`))
      }
    }
    expectAtLeast(4.5, tok(v, '--text-muted'), tok(v, '--user-bubble'), label(theme, accent, '--text-muted on --user-bubble'))
  }
})

test('--control-border is a 3:1 edge against the control and its surroundings', () => {
  for (const theme of THEMES) {
    const v = tokens(theme, 'sage')
    for (const surface of ['--bg-input', '--bg-elev', '--bg']) {
      expectAtLeast(3, tok(v, '--control-border'), tok(v, surface), label(theme, 'sage', `--control-border on ${surface}`))
    }
    expectAtLeast(3, tok(v, '--control-border'), tok(v, '--user-bubble'), label(theme, 'sage', '--control-border on --user-bubble'))
  }
})

test('every tinted status chip keeps its text at 4.5:1 on its own tint', () => {
  // Any rule that paints `color-mix(in srgb, var(--X) N%, transparent)` behind `color: var(--X)` is a chip: the
  // tint is alpha over whatever surface the chip sits on, so check it over both --bg and --bg-elev.
  const found: string[] = []
  const failures: string[] = []
  for (const file of allCssFiles(SRC)) {
    for (const rule of readCss(file)) {
      const bg = rule.decls.get('background') ?? rule.decls.get('background-color')
      const fg = rule.decls.get('color')
      const m = bg?.match(/^color-mix\(in srgb, var\((--[\w-]+)\) [\d.]+%, transparent\)$/)
      if (!m || fg !== `var(${m[1]})`) continue
      found.push(rule.sel)
      const accents = m[1] === '--accent' ? ACCENTS : ['sage']
      for (const theme of THEMES) for (const accent of accents) {
        const v = tokens(theme, accent)
        const tint = parseColor(bg!, v)
        for (const surface of ['--bg', '--bg-elev']) {
          const under = tok(v, surface)
          const r = ratio(tok(v, m[1]), over(tint, under))
          if (r < 4.5) failures.push(label(theme, accent, `${rule.sel} (${m[1]} on its tint over ${surface}) is ${r.toFixed(2)}:1`))
        }
      }
    }
  }
  assert.deepEqual(failures, [], 'chip text under 4.5:1')
  for (const must of ['.tc-status.awaiting', '.tc-status.done', '.tc-status.failed', '.tag.bad', '.todo-due.overdue', '.mc-badge.bad', '.cite-chip']) {
    assert.ok(found.some((s) => s.includes(must)), `the scan did not find ${must}: has the chip markup changed?`)
  }
  assert.ok(found.length >= 20, `only ${found.length} chip rules found`)
})

test('the parser fails loudly on what it cannot resolve', () => {
  assert.throws(() => parseColor('var(--nope)', new Map()), /--nope is not defined/)
  assert.throws(() => parseColor('hsl(10 20% 30%)', new Map()), /unsupported colour/)
  const c = parseColor('color-mix(in srgb, #ff0000 25%, transparent)', new Map())
  assert.deepEqual([c.r, c.g, c.b, c.a], [255, 0, 0, 0.25])
  const m = parseColor('color-mix(in srgb, #000000 50%, #ffffff)', new Map())
  assert.deepEqual([m.r, m.a], [127.5, 1])
})

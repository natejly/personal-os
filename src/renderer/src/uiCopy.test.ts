import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import ts from 'typescript'

// Rule: the UI must not carry text that describes a feature. Long prose in JSX
// text or in descriptive props (description, hint, help, ...) is explanation the
// interface should not need. Keep labels short; move real explanation to docs/.
//
// Scanned: every .tsx under src/renderer/src (recursively, skipping *.test.*).
// Flagged:
//   - JSX text longer than MAX chars (trimmed, whitespace collapsed)
//   - string literals rendered from expression children ({cond ? 'long' : null}), longer than MAX
//   - string literals used as the value of the JSX attributes in ATTRS, longer than MAX
//   - string literals in object properties named in PROPS, longer than MAX
// Exempt by rule:
//   - attributes title / aria-label / placeholder / alt (and anything not in ATTRS)
//   - text whose enclosing JSX element, attribute, variable or property name
//     contains "err", "confirm" or "danger" (error and destructive-action text)
//   - text inside a confirm( ... ) or window.confirm( ... ) call
// Everything else long goes in uiCopy.allow.json, keyed by file, with the exact string.

const MAX = 80
const ATTRS = new Set([
  'description', 'hint', 'help', 'subtitle', 'blurb', 'caption', 'helper', 'intro',
  'empty', 'emptyText', 'emptyHint', 'body', 'text', 'label',
])
const PROPS = new Set(['description', 'hint', 'help', 'subtitle', 'blurb', 'intro'])
const EXEMPT_NAME = /err|confirm|danger/i

// TEMPORARY: owned by other lanes right now. The orchestrator removes these exclusions after merge.
const EXCLUDED_PATHS: string[] = [
  'components/ContextDrawer.tsx',
  'components/DeskPanel.tsx',
  'components/PageAgentPanel.tsx',
  'components/TracesPane.tsx',
]

const ROOT = path.join(process.cwd(), 'src/renderer/src')
const ALLOW_FILE = path.join(ROOT, 'uiCopy.allow.json')

interface Hit { file: string; line: number; text: string }

function walk(dir: string, out: string[]): void {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name)
    if (e.isDirectory()) walk(p, out)
    else if (e.name.endsWith('.tsx') && !/\.test\./.test(e.name)) out.push(p)
  }
}

function nameOf(n: ts.Node | undefined): string {
  return n ? n.getText() : ''
}

// role="alert" / "alertdialog" elements, or a className naming error, confirm, warn or danger.
function isAlertElement(el: ts.JsxOpeningElement): boolean {
  for (const a of el.attributes.properties) {
    if (!ts.isJsxAttribute(a) || !a.initializer || !ts.isStringLiteral(a.initializer)) continue
    const n = nameOf(a.name)
    if (n === 'role' && /^alert(dialog)?$/.test(a.initializer.text)) return true
    if (n === 'className' && /err|confirm|danger|warn/i.test(a.initializer.text)) return true
  }
  return false
}

// True when an ancestor is an error/confirm/danger-named element, attribute,
// variable or property, or sits inside a confirm( ) call.
function exempt(node: ts.Node): boolean {
  for (let p: ts.Node | undefined = node.parent; p; p = p.parent) {
    let name = ''
    if (ts.isJsxElement(p) && isAlertElement(p.openingElement)) return true
    if (ts.isJsxElement(p)) name = nameOf(p.openingElement.tagName)
    else if (ts.isJsxSelfClosingElement(p)) name = nameOf(p.tagName)
    else if (ts.isJsxAttribute(p)) {
      const a = nameOf(p.name)
      if (a === 'title' || a === 'aria-label' || a === 'placeholder' || a === 'alt') return true
      name = a
    } else if (ts.isVariableDeclaration(p) || ts.isPropertyAssignment(p)) name = nameOf(p.name)
    else if (ts.isCallExpression(p)) {
      const callee = nameOf(p.expression)
      if (callee === 'confirm' || callee === 'window.confirm') return true
    }
    if (name && EXEMPT_NAME.test(name)) return true
  }
  return false
}

function scanFile(abs: string): Hit[] {
  const rel = path.relative(ROOT, abs).split(path.sep).join('/')
  const src = fs.readFileSync(abs, 'utf8')
  const sf = ts.createSourceFile(abs, src, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
  const hits: Hit[] = []
  const seen = new Set<number>()
  const add = (node: ts.Node, text: string): void => {
    if (text.length <= MAX || seen.has(node.getStart(sf)) || exempt(node)) return
    seen.add(node.getStart(sf))
    hits.push({ file: rel, line: sf.getLineAndCharacterOfPosition(node.getStart(sf)).line + 1, text })
  }
  const isStr = (n: ts.Node): n is ts.StringLiteral | ts.NoSubstitutionTemplateLiteral =>
    ts.isStringLiteral(n) || ts.isNoSubstitutionTemplateLiteral(n)
  // String literals anywhere in an expression (covers ternaries and concatenation).
  const strings = (n: ts.Node, fn: (s: ts.StringLiteral | ts.NoSubstitutionTemplateLiteral) => void): void => {
    if (isStr(n)) fn(n)
    else ts.forEachChild(n, (c) => strings(c, fn))
  }
  const visit = (n: ts.Node): void => {
    if (ts.isJsxText(n)) {
      add(n, n.text.replace(/\s+/g, ' ').trim())
    } else if (ts.isJsxExpression(n) && n.expression && (ts.isJsxElement(n.parent) || ts.isJsxFragment(n.parent))) {
      // Strings rendered from an expression child: {cond ? 'long text' : null}, {`long text`}.
      strings(n.expression, (s) => add(s, s.text.replace(/\s+/g, ' ').trim()))
    } else if (ts.isJsxAttribute(n) && ATTRS.has(nameOf(n.name)) && n.initializer) {
      strings(n.initializer, (s) => add(s, s.text.replace(/\s+/g, ' ').trim()))
    } else if (ts.isPropertyAssignment(n) && PROPS.has(nameOf(n.name).replace(/^['"]|['"]$/g, ''))) {
      strings(n.initializer, (s) => add(s, s.text.replace(/\s+/g, ' ').trim()))
    }
    ts.forEachChild(n, visit)
  }
  visit(sf)
  return hits
}

function scanAll(excluded: string[]): Hit[] {
  const files: string[] = []
  walk(ROOT, files)
  const hits: Hit[] = []
  for (const f of files.sort()) {
    const rel = path.relative(ROOT, f).split(path.sep).join('/')
    if (excluded.some((x) => rel === x || (x.endsWith('/') && rel.startsWith(x)))) continue
    hits.push(...scanFile(f))
  }
  return hits
}

function loadAllow(): Record<string, string[]> {
  return JSON.parse(fs.readFileSync(ALLOW_FILE, 'utf8')) as Record<string, string[]>
}

const excluded = process.env.UICOPY_NO_EXCLUDE === '1' ? [] : EXCLUDED_PATHS
const hits = scanAll(excluded)
const fmt = (h: Hit): string => `${h.file}:${h.line}  ${h.text}`

if (process.env.UICOPY_DUMP) fs.writeFileSync(process.env.UICOPY_DUMP, hits.map(fmt).join('\n') + '\n')

test('UI copy has no long feature-describing text', () => {
  const allow = loadAllow()
  const bad = hits.filter((h) => !(allow[h.file] ?? []).includes(h.text))
  assert.equal(bad.length, 0, `Long UI copy (>${MAX} chars). Shorten it or move it to docs:\n${bad.map(fmt).join('\n')}`)
})

test('every uiCopy.allow.json entry still matches a long string', () => {
  const allow = loadAllow()
  const stale: string[] = []
  for (const [file, texts] of Object.entries(allow)) {
    const live = new Set(hits.filter((h) => h.file === file).map((h) => h.text))
    for (const t of texts) if (!live.has(t)) stale.push(`${file}  ${t}`)
  }
  assert.equal(stale.length, 0, `Stale allowlist entries:\n${stale.join('\n')}`)
})

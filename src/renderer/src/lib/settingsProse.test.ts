import test from 'node:test'
import assert from 'node:assert/strict'
import { readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'

/** Settings is a place for labels and controls, not explanations. This guard fails on long prose in the settings surface. */
const SRC = join(process.cwd(), 'src/renderer/src')
const MAX = 80

const EXTRA = [
  'components/SettingsModal.tsx', 'components/CoworkSettings.tsx', 'components/DataSettings.tsx', 'components/TraceExportSettings.tsx',
  'components/SupportSettings.tsx', 'components/ModelsSettings.tsx', 'components/ProvidersSettings.tsx', 'components/ProviderForm.tsx',
  'components/TelegramSettings.tsx', 'components/MemoryPanel.tsx', 'components/EmbeddingHint.tsx', 'components/AdvancedRetrieval.tsx',
  'components/TrashPanel.tsx', 'components/ToolPermissions.tsx', 'components/PermissionMode.tsx', 'components/PermissionRules.tsx',
  'components/PermissionsPanel.tsx', 'components/GrantsPanel.tsx', 'components/GoogleSettings.tsx', 'components/McpSettings.tsx',
  'components/PlannerMailSettings.tsx', 'components/RunSafetySettings.tsx', 'components/VoiceInputSettings.tsx', 'components/DestructiveDefaults.tsx',
  'extensions/coding/RecipesPanel.tsx', 'extensions/coding/extension.tsx'
]

/** Explicit exceptions: a file, a fragment of the text, and why it may stay long. Keep this small. */
const ALLOW: { file: string; includes: string; why: string }[] = [
  { file: 'components/GoogleSettings.tsx', includes: 'Synced ${', why: 'runtime sync status with a timestamp' },
  { file: 'components/GoogleSettings.tsx', includes: 'Last backup (${', why: 'runtime backup error status' },
  { file: 'components/GoogleSettings.tsx', includes: 'Last backup ${', why: 'runtime backup result status' },
  { file: 'components/McpSettings.tsx', includes: 'This server says the tool can change or delete things', why: 'confirmation of a dangerous change' }
]

function files(): string[] {
  const own = readdirSync(join(SRC, 'components/settings')).filter((f) => f.endsWith('.tsx') && !f.endsWith('.test.tsx')).map((f) => `components/settings/${f}`)
  return [...own, ...EXTRA]
}

/** Every quoted or template string literal in a chunk of source. */
function literals(code: string): string[] {
  const out: string[] = []
  const re = /'((?:[^'\\\n]|\\.)*)'|"((?:[^"\\\n]|\\.)*)"|`((?:[^`\\]|\\.)*)`/g
  for (let m = re.exec(code); m; m = re.exec(code)) out.push((m[1] ?? m[2] ?? m[3] ?? '').replace(/\s+/g, ' ').trim())
  return out
}

/** The source after `at`, up to the matching close of a `{...}` group (or the single quoted value when no brace). */
function propValue(src: string, at: number): string {
  if (src[at] !== '{') return src.slice(at).match(/^("[^"]*"|'[^']*')/)?.[0] ?? ''
  let depth = 0
  for (let i = at; i < src.length; i++) {
    if (src[i] === '{') depth++
    else if (src[i] === '}' && --depth === 0) return src.slice(at, i + 1)
  }
  return src.slice(at)
}

/** Source with comments and import/export-from statements blanked out, so only rendered code is scanned. */
function stripNoise(src: string): string {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ' '))
    .replace(/(^|[\s;{}()])\/\/[^\n]*/g, '$1')
    .replace(/^\s*(?:import|export)\b[^'"]*from\s+['"][^'"]*['"]/gm, '')
}

export function findProse(source: string): string[] {
  const src = stripNoise(source)
  const found: string[] = []
  // JSX text between tags or expressions; skips anything that reads as code.
  for (const m of src.matchAll(/(?<!=)[>}]([^<>{}]+)(?=[<{])/g)) {
    const t = m[1].replace(/\s+/g, ' ').trim()
    if (t.length > MAX && !/[=;|\\[\]]|\w\(/.test(t)) found.push(t)
  }
  // help / description / hint props holding long strings (also inside ternaries and templates).
  for (const m of src.matchAll(/\b(?:help|description|hint)=/g)) {
    for (const s of literals(propValue(src, m.index + m[0].length))) if (s.length > MAX) found.push(s)
  }
  // strings inside <p>, <small> and set-help / muted elements.
  for (const m of src.matchAll(/<(p|small|span|div)\b([^>]*)>/g)) {
    const isProse = m[1] === 'p' || m[1] === 'small' || /\b(?:set-help|muted)\b/.test(m[2])
    if (!isProse) continue
    const body = src.slice(m.index + m[0].length, src.indexOf(`</${m[1]}>`, m.index) >>> 0 || undefined)
    for (const s of literals(body)) if (s.length > MAX) found.push(s)
  }
  return [...new Set(found)]
}

test('settings components carry no long explanatory prose', () => {
  const bad: string[] = []
  for (const f of files()) {
    for (const t of findProse(readFileSync(join(SRC, f), 'utf8'))) {
      if (ALLOW.some((a) => a.file === f && t.includes(a.includes))) continue
      bad.push(`${f}: ${t.length} chars: ${t.slice(0, 100)}`)
    }
  }
  assert.deepEqual(bad, [], `Settings holds labels and controls, not descriptions. Remove or shorten:\n${bad.join('\n')}`)
})

test('the prose scan catches the shapes it guards', () => {
  const long = 'x'.repeat(30) + ' ' + 'y'.repeat(30) + ' ' + 'z'.repeat(30)
  assert.equal(findProse(`<div>${long}</div>`).length, 1)
  assert.equal(findProse(`<SettingRow help="${long}" />`).length, 1)
  assert.equal(findProse(`<SettingRow help={a ? '${long}' : 'short'} />`).length, 1)
  assert.equal(findProse(`<p className="x">{'${long}'}</p>`).length, 1)
  assert.equal(findProse(`<span className="muted">{\`${long}\`}</span>`).length, 1)
  assert.equal(findProse(`<p>short</p><SettingRow help="short" />`).length, 0)
})

import { useState } from 'react'
import { X } from 'lucide-react'
import type { PermissionEvaluation, PermissionRules as Rules } from '@shared/types'
import { api } from '../lib/api'
import { TESTERS, testBody, type TesterTool } from '../lib/permTester'

const KINDS: { key: keyof Rules; label: string; hint: string }[] = [
  { key: 'deny', label: 'Deny', hint: 'Refused before the tool runs. Beats ask and allow.' },
  { key: 'ask', label: 'Ask', hint: 'Always shows a card, even when the tool is on.' },
  { key: 'allow', label: 'Allow', hint: 'Runs without a card. Never lifts a forced approval.' }
]

/** Allow / ask / deny lists of `Tool(pattern)` rules, with a box that says what they decide for one call. */
export default function PermissionRules({ value, onChange }: { value: Rules | undefined; onChange: (next: Rules) => void }): JSX.Element {
  const rules: Rules = { allow: value?.allow ?? [], ask: value?.ask ?? [], deny: value?.deny ?? [] }
  const [draft, setDraft] = useState<Record<string, string>>({})
  const [bad, setBad] = useState<string | null>(null)
  const [cmd, setCmd] = useState('')
  const [tool, setTool] = useState<TesterTool>('shell_run')
  const tester = TESTERS.find((t) => t.tool === tool) ?? TESTERS[0]
  const [result, setResult] = useState<PermissionEvaluation | null>(null)

  const add = async (key: keyof Rules): Promise<void> => {
    const text = (draft[key] ?? '').trim()
    if (!text) return
    const v = await api.evaluatePermission({ rule: text }).catch(() => null)
    if (!v?.ok) { setBad(v?.error ?? 'Could not check that rule'); return }
    setBad(null)
    setDraft((d) => ({ ...d, [key]: '' }))
    if (!rules[key].includes(v.rule ?? text)) onChange({ ...rules, [key]: [...rules[key], v.rule ?? text] })
  }
  const test = async (): Promise<void> => {
    if (!cmd.trim()) return
    setResult(await api.evaluatePermission(testBody(tool, cmd)).catch(() => null))
  }

  return (
    <div className="perm-rules">
      <h4>Permission rules</h4>
      <p className="muted small">
        A rule is <code>Tool</code> or <code>Tool(pattern)</code>: <code>Bash(git push *)</code>, <code>Read(~/Documents/**)</code>, <code>Edit(~/Projects/app/**)</code>, <code>Agent(researcher)</code>.
        <code>*</code> matches any run of characters, <code>?</code> one. A command line is split on <code>&amp;&amp; || ; |</code> and every part is judged.
        Deny wins over ask, ask over allow.
      </p>
      {KINDS.map((k) => (
        <div key={k.key} className="perm-rule-group">
          <span className="toggle-text"><b>{k.label}</b><small>{k.hint}</small></span>
          <ul>
            {rules[k.key].map((r) => (
              <li key={r}><code>{r}</code>
                <button type="button" className="icon-btn sm" aria-label={`Remove ${r}`} title="Remove" onClick={() => onChange({ ...rules, [k.key]: rules[k.key].filter((x) => x !== r) })}><X size={12} /></button>
              </li>
            ))}
          </ul>
          <form className="perm-rule-add" onSubmit={(e) => { e.preventDefault(); void add(k.key) }}>
            <input value={draft[k.key] ?? ''} onChange={(e) => setDraft((d) => ({ ...d, [k.key]: e.target.value }))} placeholder={`Add a ${k.label.toLowerCase()} rule`} spellCheck={false} aria-label={`New ${k.label.toLowerCase()} rule`} />
            <button type="submit" className="ghost-btn">Add</button>
          </form>
        </div>
      ))}
      {bad && <p className="small err" role="alert">{bad}</p>}
      <div className="perm-rule-test">
        <span className="toggle-text"><b>Test a call</b><small>The test reads the rules you have saved. Rules are conveniences; the sandbox is the boundary.</small></span>
        <form className="perm-rule-add" onSubmit={(e) => { e.preventDefault(); void test() }}>
          <select value={tool} onChange={(e) => { setTool(e.target.value as TesterTool); setResult(null) }} aria-label="Tool to test">
            {TESTERS.map((t) => <option key={t.tool} value={t.tool}>{t.label}</option>)}
          </select>
          <input value={cmd} onChange={(e) => setCmd(e.target.value)} placeholder={tester.placeholder} spellCheck={false} aria-label={`${tester.label}: ${tester.key} to test`} />
          <button type="submit" className="ghost-btn">Test</button>
        </form>
        {result && (
          <p className="small">
            <b>{result.action === 'none' ? 'No rule applies' : result.action}</b>
            {result.hardline ? ' (never allowed)' : result.rule ? <> by <code>{result.rule}</code></> : null}
            {result.reason && !result.hardline ? ` - ${result.reason}` : ''}
            {result.action === 'none' && ' - the tool’s own setting decides.'}
            {result.subjects.length > 0 && <><br /><small className="muted">Matched on {result.subjects.map((s) => <code key={s}>{s} </code>)}</small></>}
          </p>
        )}
      </div>
    </div>
  )
}

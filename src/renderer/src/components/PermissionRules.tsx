import { useState } from 'react'
import { X } from '../icons'
import type { PermissionEvaluation, PermissionRules as Rules } from '@shared/types'
import { api } from '../lib/api'
import { TESTERS, testBody, type TesterTool } from '../lib/permTester'

const KINDS: { key: keyof Rules; label: string }[] = [
  { key: 'deny', label: 'Deny' },
  { key: 'ask', label: 'Ask' },
  { key: 'allow', label: 'Allow' }
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
      <h4 title="Tool or Tool(pattern), e.g. Bash(git push *), Read(~/Documents/**), Agent(researcher). * matches any run of characters, ? one. Deny wins over ask, ask over allow.">Permission rules</h4>
      {KINDS.map((k) => (
        <div key={k.key} className="perm-rule-group">
          <span className="toggle-text"><b>{k.label}</b></span>
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
        <span className="toggle-text"><b title="Checks against the saved rules">Test a call</b></span>
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
           
            {result.subjects.length > 0 && <><br /><small className="muted">Matched on {result.subjects.map((s) => <code key={s}>{s} </code>)}</small></>}
          </p>
        )}
      </div>
    </div>
  )
}

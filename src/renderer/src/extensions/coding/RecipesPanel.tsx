import { useCallback, useEffect, useState } from 'react'
import type { CodingRecipe, CodingRecipeDetail, CodingRecipeInput, CodingRecipeRun } from '@shared/types'
import { api } from '../../lib/api'
import { useStore } from '../../store'

const STARTER = JSON.stringify({
  name: 'fix-failing-test', description: 'Fix a failing test and prove it', prompt: 'Make this test pass: {{test}}',
  inputs: [{ name: 'test', type: 'string' }], repo_path: '/path/to/repo', driver: 'claude',
  verify: { command: 'npm test' }, max_retries: 1
}, null, 2)

type Values = Record<string, string | boolean>

/** The inputs as the backend takes them: numbers as numbers, blanks left out so a default can apply. */
function inputsOf(specs: CodingRecipeInput[], values: Values): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const i of specs) {
    const v = values[i.name]
    if (v === undefined || v === '') continue
    out[i.name] = i.type === 'number' ? Number(v) : v
  }
  return out
}

function InputField({ spec, value, onChange }: { spec: CodingRecipeInput; value: string | boolean | undefined; onChange: (v: string | boolean) => void }): JSX.Element {
  const label = `${spec.name}${spec.required && spec.default === undefined ? ' *' : ''}`
  if (spec.type === 'boolean') {
    return <label className="set-row"><input type="checkbox" checked={value === true} onChange={(e) => onChange(e.target.checked)} /> {label}</label>
  }
  if (spec.type === 'enum') {
    return (
      <label className="set-row">{label}{' '}
        <select value={String(value ?? '')} onChange={(e) => onChange(e.target.value)}>
          <option value="">{spec.default !== undefined ? `default (${String(spec.default)})` : 'choose'}</option>
          {(spec.values ?? []).map((v) => <option key={v}>{v}</option>)}
        </select>
      </label>
    )
  }
  return (
    <label className="set-row">{label}{' '}
      <input type={spec.type === 'number' ? 'number' : 'text'} value={String(value ?? '')} placeholder={spec.default !== undefined ? String(spec.default) : ''}
        maxLength={spec.max_len} onChange={(e) => onChange(e.target.value)} />
    </label>
  )
}

/** Confirming a run: the recipe's inputs, then the settings the run would use, then the user's go. */
function RunForm({ recipe, onDone }: { recipe: CodingRecipe; onDone: () => void }): JSX.Element {
  const toast = useStore((s) => s.toast)
  const [detail, setDetail] = useState<CodingRecipeDetail | null>(null)
  const [values, setValues] = useState<Values>({})
  const [lines, setLines] = useState<string[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => { api.coding.recipes.get(recipe.id).then(setDetail).catch((e: Error) => setError(e.message)) }, [recipe.id])
  const specs = detail?.current.body.inputs ?? []
  const preview = (): void => {
    setError(null)
    api.coding.recipes.preview(recipe.id, inputsOf(specs, values)).then((p) => setLines(p.lines)).catch((e: Error) => { setLines(null); setError(e.message) })
  }
  const run = (): void => {
    api.coding.recipes.run(recipe.id, inputsOf(specs, values))
      .then((r) => { toast(`${recipe.name} started`, 'info'); if (r.session_id) useStore.getState().openSettings('ext:coding:recipes'); onDone() })
      .catch((e: Error) => setError(e.message))
  }
  return (
    <div className="adv-group" data-testid="recipe-run">
      <h4>Run {recipe.name}</h4>
      {specs.map((s) => <InputField key={s.name} spec={s} value={values[s.name]} onChange={(v) => { setLines(null); setValues((x) => ({ ...x, [s.name]: v })) }} />)}
      {error && <p className="set-help" role="status">{error}</p>}
      {lines && <ul className="set-help" data-testid="recipe-preview">{lines.map((l) => <li key={l}>{l}</li>)}</ul>}
      <div className="set-row">
        <button type="button" className="ghost-btn sm" onClick={preview}>Show settings</button>
        <button type="button" className="ghost-btn sm" disabled={!lines} title={lines ? '' : 'Show the settings first: starting a run approves them'} onClick={run}>Start run</button>
        <button type="button" className="ghost-btn sm" onClick={onDone}>Cancel</button>
      </div>
    </div>
  )
}

const OUTCOME: Record<CodingRecipeRun['status'], string> = {
  queued: 'waiting for a free session', working: 'the agent is working', verifying: 'checking', passed: 'passed', failed: 'verify still failing',
  agent_failed: 'the agent did not finish', timeout: 'out of time', stopped: 'stopped', verify_blocked: 'verify could not run', queued_timeout: 'no session came free'
}

/** Settings > Coding recipes: saved coding tasks that are done only when their verify command passes. */
export default function RecipesPanel(): JSX.Element {
  const [recipes, setRecipes] = useState<CodingRecipe[]>([])
  const [runs, setRuns] = useState<CodingRecipeRun[]>([])
  const [running, setRunning] = useState<CodingRecipe | null>(null)
  const [draft, setDraft] = useState('')
  const [error, setError] = useState<string | null>(null)
  const load = useCallback((): void => {
    void api.coding.recipes.list().then((r) => setRecipes(r.recipes)).catch((e: Error) => setError(e.message))
    void api.coding.recipes.runs().then((r) => setRuns(r.runs)).catch(() => undefined)
  }, [])
  useEffect(() => {
    load()
    const t = setInterval(load, 4000)
    return () => clearInterval(t)
  }, [load])
  const act = (p: Promise<unknown>): void => { setError(null); p.then(load).catch((e: Error) => setError(e.message)) }
  const save = (): void => {
    let body: Record<string, unknown>
    try { body = JSON.parse(draft) as Record<string, unknown> } catch { setError('That is not valid JSON.'); return }
    act(api.coding.recipes.save(body).then(() => setDraft('')))
  }
  return (
    <>
      {error && <p className="set-help" role="status">{error}</p>}
      {recipes.length === 0 && <p className="set-help">No recipes yet.</p>}
      {recipes.map((r) => (
        <div key={r.id} className="set-row" data-testid={`recipe-${r.name}`}>
          <b>{r.name}</b> <small>v{r.current_version}{r.created_by === 'agent' ? ' (saved by the assistant)' : ''}</small>
          {r.description && <span className="set-help">{r.description}</span>}
          {r.approved
            ? <button type="button" className="ghost-btn sm" onClick={() => setRunning(r)}>Run</button>
            : <button type="button" className="ghost-btn sm" onClick={() => act(api.coding.recipes.approve(r.id, r.current_version))}>Approve v{r.current_version}</button>}
          <button type="button" className="ghost-btn sm" onClick={() => act(api.coding.recipes.archive(r.id))}>Remove</button>
        </div>
      ))}
      {running && <RunForm recipe={running} onDone={() => { setRunning(null); load() }} />}
      <h4>Recent runs</h4>
      {runs.length === 0 && <p className="set-help">None yet.</p>}
      {runs.slice(0, 10).map((x) => (
        <p key={x.id} className="set-help" data-testid={`recipe-run-${x.id}`}>
          {x.resolved.recipe} v{x.recipe_version} · {OUTCOME[x.status]}{x.verify_exit !== null ? ` (verify exit ${x.verify_exit})` : ''}
        </p>
      ))}
      <h4>New recipe</h4>
      <textarea className="mono" rows={12} value={draft} placeholder={STARTER} onChange={(e) => setDraft(e.target.value)} spellCheck={false} />
      <div className="set-row">
        <button type="button" className="ghost-btn sm" onClick={() => setDraft(STARTER)}>Fill an example</button>
        <button type="button" className="ghost-btn sm" disabled={!draft.trim()} onClick={save}>Save as a new version</button>
      </div>
    </>
  )
}

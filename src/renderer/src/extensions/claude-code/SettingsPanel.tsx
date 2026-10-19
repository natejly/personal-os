import { useStore } from '../../store'

/** Stored as `ext.claude-code.<name>` like every extension setting. */
export const SETTING_KEYS = {
  maxConcurrent: 'claudeCodeMaxConcurrent', defaultModel: 'claudeCodeDefaultModel', permissionMode: 'claudeCodeDefaultPermissionMode',
  budgetUsd: 'claudeCodeBudgetUsd', maxTurns: 'claudeCodeMaxTurns', worktreeRoot: 'claudeCodeWorktreeRoot'
} as const
export const PERMISSION_MODES = ['default', 'acceptEdits', 'plan', 'dontAsk'] as const
const full = (k: string): string => `ext.claude-code.${k}`

/** A number from the field, or null for blank/invalid (null clears the setting back to its default). */
export const numberOrNull = (raw: string): number | null => {
  if (!raw.trim()) return null
  const n = Number(raw)
  return Number.isFinite(n) && n >= 0 ? n : null
}

export default function ClaudeCodeSettings(): JSX.Element {
  const settings = useStore((s) => s.settings) as unknown as Record<string, unknown>
  const save = useStore((s) => s.saveSettings)
  const toast = useStore((s) => s.toast)
  const get = (k: string): string => { const v = settings[full(k)]; return v === undefined || v === null ? '' : String(v) }
  const put = (k: string, v: unknown): void => { void save({ [full(k)]: v } as never).catch((e: Error) => toast(`Claude Code settings: ${e.message}`, 'error')) }
  const text = (k: string, label: string, placeholder = ''): JSX.Element => (
    <label className="set-row">{label}{' '}
      <input type="text" defaultValue={get(k)} placeholder={placeholder} key={get(k)} onBlur={(e) => { if (e.target.value !== get(k)) put(k, e.target.value.trim() || null) }} />
    </label>
  )
  const number = (k: string, label: string, placeholder = ''): JSX.Element => (
    <label className="set-row">{label}{' '}
      <input type="number" min={0} defaultValue={get(k)} placeholder={placeholder} key={get(k)} onBlur={(e) => { if (e.target.value !== get(k)) put(k, numberOrNull(e.target.value)) }} />
    </label>
  )
  return (
    <div className="set-sec" data-testid="claude-code-settings">
      <h3>Claude Code</h3>
      <p className="set-help">Limits for sessions the agent starts. A session stops when it reaches its budget or turn limit.</p>
      {number(SETTING_KEYS.maxConcurrent, 'Sessions at once', 'default')}
      {text(SETTING_KEYS.defaultModel, 'Default model', 'Claude Code default')}
      <label className="set-row">Permission mode{' '}
        <select value={get(SETTING_KEYS.permissionMode)} onChange={(e) => put(SETTING_KEYS.permissionMode, e.target.value || null)}>
          <option value="">default</option>
          {PERMISSION_MODES.filter((m) => m !== 'default').map((m) => <option key={m} value={m}>{m}</option>)}
        </select>
      </label>
      {number(SETTING_KEYS.budgetUsd, 'Budget per session (USD)', 'none')}
      {number(SETTING_KEYS.maxTurns, 'Turn limit per session', 'none')}
      {text(SETTING_KEYS.worktreeRoot, 'Worktree folder', '~/Grain/worktrees')}
    </div>
  )
}

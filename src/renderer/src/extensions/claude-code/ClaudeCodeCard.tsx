import CardShell from '../../components/toolcards/CardShell'
import { Badge, Meta } from '../../components/toolcards/blocks'
import type { ToolCardProps } from '../../components/toolcards/registry'
import { Bot } from '../../icons'
import { useClaudeCode } from './store'
import { formatCost, parseJson, pathBase, statusLabel, statusTone, type CcSession, type CcStatus } from './lib'

export const CARD_TOOLS = ['claude_code_run', 'claude_code_fleet', 'claude_code_result'] as const

const TITLE: Record<string, string> = { claude_code_run: 'Run Claude Code', claude_code_fleet: 'Claude Code fleet', claude_code_result: 'Claude Code result' }

const asList = (v: unknown): Record<string, unknown>[] => (Array.isArray(v) ? v.filter((x): x is Record<string, unknown> => !!x && typeof x === 'object') : [])
const str = (v: unknown): string | null => (typeof v === 'string' && v ? v : null)
const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null)

/** One session line: live status from the store when the id is known, else what the tool result said. */
function SessionLine({ raw }: { raw: Record<string, unknown> }): JSX.Element {
  const id = str(raw.id) ?? str(raw.session_id)
  const live = useClaudeCode((s) => (id ? s.sessions[id] : undefined)) as CcSession | undefined
  const status = (live?.status ?? str(raw.status)) as CcStatus | null
  const name = live?.name ?? str(raw.name) ?? id ?? 'session'
  const where = live?.branch ?? str(raw.branch) ?? pathBase(live?.worktree ?? str(raw.worktree))
  const cost = live?.cost_usd ?? num(raw.cost_usd)
  return (
    <div className="tc-row" style={{ display: 'flex', gap: 8, alignItems: 'center', minWidth: 0 }}>
      {status && <Badge tone={statusTone(status)}>{statusLabel(status)}</Badge>}
      <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={name}>{name}</span>
      {where && <span className="tc-muted small">{where}</span>}
      {cost != null && <span className="tc-muted small">{formatCost(cost)}</span>}
    </div>
  )
}

export default function ClaudeCodeCard(props: ToolCardProps): JSX.Element {
  const { event } = props
  const data = props.pending ? null : parseJson(event.result_preview)
  const sessions = asList(data?.sessions)
  const queued = asList(data?.queued)
  const single = !sessions.length && data && (str(data.id) || str(data.status)) && event.name === 'claude_code_run' ? [data] : []
  const lines = event.name === 'claude_code_run' ? (single.length ? single : sessions) : sessions
  const cost = num(data?.cost_usd) ?? num((data?.cost as Record<string, unknown> | undefined)?.total_usd)
  const diff = typeof data?.diff === 'string' ? data.diff : null
  const denials = Array.isArray(data?.permission_denials) ? data.permission_denials.length : 0
  return (
    <CardShell {...props} icon={<Bot size={14} />} title={TITLE[event.name] ?? 'Claude Code'} subject={str(event.arguments?.name) ?? str(event.arguments?.repo_path) ?? undefined}>
      {lines.map((s, i) => <SessionLine key={str(s.id) ?? i} raw={s} />)}
      {queued.length > 0 && <div className="tc-muted small">{queued.length} queued</div>}
      {event.name === 'claude_code_result' && data && (
        <Meta items={[
          ['Result', str(data.subtype)], ['Cost', cost != null ? formatCost(cost) : null], ['Turns', num(data.num_turns)],
          ['Denied', denials ? `${denials} permission${denials === 1 ? '' : 's'}` : null], ['Diff', diff ? `${diff.split('\n').length} lines` : null]
        ]} />
      )}
      {event.name === 'claude_code_fleet' && cost != null && <Meta items={[['Cost', formatCost(cost)]]} />}
    </CardShell>
  )
}

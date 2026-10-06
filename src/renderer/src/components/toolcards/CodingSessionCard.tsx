import { useEffect, useState } from 'react'
import { Bot } from 'lucide-react'
import type { CodingSession, CodingSessionDiff } from '@shared/types'
import { useStore } from '../../store'
import { api } from '../../lib/api'
import { AGENT_LABEL, codingDot, codingStatusLabel, codingStoppable, codingTone } from '../../lib/codingSessions'
import { str } from '../../lib/toolResult'
import CardShell from './CardShell'
import { Badge, ErrorLine, Meta, MonoBlock, useParsed } from './blocks'
import { registerToolCard, type ToolCardProps } from './registry'

const TITLE: Record<string, string> = {
  coding_session_start: 'Start coding session', coding_session_list: 'Coding sessions', coding_session_status: 'Check coding session',
  coding_session_send: 'Message coding session', coding_session_stop: 'Stop coding session', coding_session_diff: 'Coding session diff'
}

const PERMISSION_WARNING: Record<string, string> = {
  acceptEdits: 'This session will edit files without asking you first.',
  bypassPermissions: 'This session skips every permission check inside Claude Code: it can run any command and edit any file in that repo without asking.'
}

/** Stop button state shared by the card and the list: the row comes back from the route and goes into the store. */
export function useStop(id: string): { stop: () => Promise<void>; busy: boolean } {
  const upsert = useStore((s) => s.upsertCodingSession)
  const toast = useStore((s) => s.toast)
  const [busy, setBusy] = useState(false)
  const stop = async (): Promise<void> => {
    setBusy(true)
    try { upsert(await api.coding.stop(id)) } catch (e) { toast(`Stop: ${(e as Error).message}`, 'error') } finally { setBusy(false) }
  }
  return { stop, busy }
}

/** One live session: status, current step, where it works, a log tail, Stop while it runs, and how to answer a prompt. */
export function CodingSessionView({ session: c }: { session: CodingSession }): JSX.Element {
  const { stop, busy } = useStop(c.id)
  const [log, setLog] = useState(false)
  return (
    <div className="coding-session">
      <div className="tc-statusrow">
        <Badge tone={codingTone(c.status)}>{codingStatusLabel(c.status)}</Badge>
        <span className="tc-muted">{AGENT_LABEL[c.agent]}</span>
        {c.model && <span className="tc-muted mono">{c.model}</span>}
        {codingStoppable(c.status) && (
          <button type="button" className="ghost-btn sm" disabled={busy} onClick={() => void stop()}>Stop</button>
        )}
        {c.log_tail && (
          <button type="button" className="tc-more" aria-expanded={log} onClick={() => setLog((o) => !o)}>{log ? 'Hide log' : 'Log'}</button>
        )}
      </div>
      {c.detail && <div className="tc-muted small">{c.detail}</div>}
      {c.status === 'needs_you' && c.attach_hint && (
        <div className="tc-hint warn">Waiting in its own window: run <span className="mono">{c.attach_hint}</span> in a terminal</div>
      )}
      <Meta items={[['Folder', <span className="mono" title={c.worktree}>{c.worktree}</span>], ['Branch', c.branch ? <span className="mono">{c.branch}</span> : null]]} />
      {log && <MonoBlock text={c.log_tail} tail />}
    </div>
  )
}

/** The changes a session left in its worktree: file stats and commits, the full patch on demand. */
function CodingDiffView({ id }: { id: string }): JSX.Element {
  const [d, setD] = useState<CodingSessionDiff | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [patch, setPatch] = useState<string | null>(null)
  useEffect(() => {
    api.coding.diff(id).then(setD).catch((e: Error) => setErr(e.message))
  }, [id])
  const showPatch = (): void => {
    if (patch !== null) return setPatch(null)
    api.coding.diff(id, true).then((r) => setPatch(r.diff ?? '')).catch((e: Error) => setErr(e.message))
  }
  if (err) return <div className="tc-muted">Could not read the changes: {err}</div>
  if (!d) return <div className="tc-muted">Reading changes…</div>
  return (
    <div className="coding-session">
      <Meta items={[['Folder', <span className="mono">{d.worktree}</span>], ['Branch', d.branch ? <span className="mono">{d.branch}</span> : null]]} />
      <MonoBlock text={d.status} label="Status" empty="No uncommitted changes." />
      <MonoBlock text={d.diff_stat} label="Changes" />
      <MonoBlock text={d.log} label="Commits" />
      <button type="button" className="tc-more" aria-expanded={patch !== null} onClick={showPatch}>{patch !== null ? 'Hide patch' : 'Show patch'}</button>
      {patch !== null && <MonoBlock text={patch} label={d.truncated ? 'Patch (cut short)' : 'Patch'} empty="No patch." />}
    </div>
  )
}

/** One line per session, for coding_session_list. Live rows come from the store so a result read later still moves. */
function CodingListRows({ rows }: { rows: CodingSession[] }): JSX.Element {
  const live = useStore((s) => s.codingSessions)
  if (!rows.length) return <div className="tc-muted">No coding sessions yet.</div>
  return (
    <ul className="plain-list coding-list">
      {rows.map((r) => {
        const c = live[r.id] ?? r
        return (
          <li key={c.id}>
            <span className={`inbox-dot ${codingDot(c.status)}`} role="img" aria-label={codingStatusLabel(c.status)} title={codingStatusLabel(c.status)} />
            <span className="coding-name">{c.name}</span>
            <span className="tc-muted">{AGENT_LABEL[c.agent]} · {codingStatusLabel(c.status)}</span>
          </li>
        )
      })}
    </ul>
  )
}

/** The six coding_session_* tools: the approval says what will run and where; afterwards the live session. */
export default function CodingSessionCard(props: ToolCardProps): JSX.Element {
  const { event, pending } = props
  const p = useParsed(event)
  const a = event.arguments
  const tool = event.name
  const id = tool === 'coding_session_start' ? str(p.data?.id) : str(a.id) || str(p.data?.id)
  const live = useStore((s) => (id ? s.codingSessions[id] : undefined))
  const upsert = useStore((s) => s.upsertCodingSession)
  const fetchIt = !!id && !live && tool !== 'coding_session_diff' && tool !== 'coding_session_list'
  useEffect(() => {
    if (fetchIt) api.coding.get(id).then(upsert).catch(() => undefined)
  }, [fetchIt, id, upsert])

  const agent = str(a.agent) === 'opencode' ? 'opencode' : 'claude'
  const repo = str(a.repo_path)
  const branch = str(a.branch)
  const mode = str(a.permission_mode)
  const where = a.new_worktree === true ? `a new worktree of ${repo}${branch ? ` on branch ${branch}` : ''}` : repo
  const listed = Array.isArray(p.data?.sessions) ? (p.data?.sessions as CodingSession[]) : null
  const subject = tool === 'coding_session_list' ? undefined : live?.name || str(a.name) || id || undefined

  return (
    <CardShell {...props} icon={<Bot size={14} />} title={TITLE[tool] ?? 'Coding session'} subject={subject}
      tone={pending && mode ? 'warn' : undefined} hideResult={!!live || !!listed || tool === 'coding_session_diff'}>
      {pending && tool === 'coding_session_start' && (
        <>
          <div>Runs <strong>{AGENT_LABEL[agent]}</strong> in <span className="mono">{where || '(no folder)'}</span> in the background.</div>
          {str(a.prompt) && <MonoBlock text={str(a.prompt)} label="Task" collapseAt={6} />}
          {PERMISSION_WARNING[mode] && <div className="tc-hint warn">{PERMISSION_WARNING[mode]}</div>}
          {agent === 'claude' && (
            <div className="tc-muted small">Runs outside Grain&apos;s sandbox with your own account; Claude Code asks for permission in its own session.</div>
          )}
          {agent === 'opencode' && <div className="tc-muted small">Runs inside the OS sandbox, writing only within the workspace folder that holds the repo.</div>}
        </>
      )}
      {pending && tool === 'coding_session_send' && (
        <>
          <div>Sends a follow-up to <strong>{live?.name ?? id}</strong>:</div>
          <MonoBlock text={str(a.message)} collapseAt={6} />
        </>
      )}
      {event.pending && !pending && <div className="tc-muted">Working…</div>}
      {tool === 'coding_session_list' && listed && <CodingListRows rows={listed} />}
      {tool === 'coding_session_diff' && id && !event.pending && !event.error && <CodingDiffView id={id} />}
      {live && tool !== 'coding_session_diff' && tool !== 'coding_session_list' && !pending && <CodingSessionView session={live} />}
      <ErrorLine event={event} />
    </CardShell>
  )
}

for (const name of Object.keys(TITLE)) registerToolCard(name, CodingSessionCard)

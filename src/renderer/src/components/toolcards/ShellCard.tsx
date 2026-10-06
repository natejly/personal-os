import { Terminal } from 'lucide-react'
import { fmtSeconds, networkLine, num, shellState, str } from '../../lib/toolResult'
import CardShell from './CardShell'
import { Badge, ErrorLine, Meta, MonoBlock, unreadable, useParsed } from './blocks'
import { registerToolCard, type ToolCardProps } from './registry'

/** shell_run / shell_poll: the command, where it ran, how it ended, the end of its output, and which hosts it reached. */
function ShellCard(props: ToolCardProps): JSX.Element {
  const { event, pending } = props
  const p = useParsed(event)
  const d = p.data
  const poll = event.name === 'shell_poll'
  const a = event.arguments
  const state = shellState(event.name, d, !!event.pending)
  const net = d ? networkLine(d.network) : null
  const background = a.background === true
  return (
    <CardShell {...props} icon={<Terminal size={14} />} title={poll ? 'Check command output' : 'Run command'} subject={poll ? `job ${str(a.job_id)}` : undefined}
      tone={pending && a.unsandboxed === true ? 'warn' : undefined} hideResult={!unreadable(p, event)}>
      {!poll && <pre className="tc-mono tc-cmd">{str(a.command) || '(no command)'}</pre>}
      {pending && a.unsandboxed === true && <div className="tc-hint warn">Runs outside the OS sandbox, with access to your whole account.</div>}
      {!event.error && !event.pending && (
        <div className="tc-statusrow">
          <Badge tone={state.tone}>{state.label}</Badge>
          {d && <span className="tc-muted">{fmtSeconds(num(d.duration_s))}</span>}
        </div>
      )}
      {event.pending && !pending && <div className="tc-muted">{background ? 'Starting in the background…' : 'Running…'}</div>}
      <Meta items={[['Folder', d && str(d.cwd) ? <span className="mono">{str(d.cwd)}</span> : null], ['Network', net]]} />
      {d && str(d.output) && <MonoBlock text={str(d.output)} tail label={d.truncated === true || p.cut ? 'Output (cut short)' : 'Output'} />}
      {d && !str(d.output) && !event.pending && !event.error && !background && d.exit_code !== undefined && <div className="tc-muted">No output.</div>}
      {typeof d?.dropped_chars === 'number' && <div className="tc-muted">{d.dropped_chars} earlier characters were dropped.</div>}
      <ErrorLine event={event} />
    </CardShell>
  )
}

registerToolCard('shell_run', ShellCard)
registerToolCard('shell_poll', ShellCard)

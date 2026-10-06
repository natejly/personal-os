import { Box } from 'lucide-react'
import { networkLine, num, sandboxEntries, sandboxLine, shellState, str } from '../../lib/toolResult'
import { describeCall } from '../../lib/toolDisplay'
import CardShell from './CardShell'
import { Badge, ErrorLine, Meta, MonoBlock, unreadable, useParsed } from './blocks'
import { registerToolCard, type ToolCardProps } from './registry'

/**
 * sandbox_*: a run shows its command, exit state and output tails; a file call shows the path and what it read,
 * wrote or listed; checkpoint, restore and reset are one status line. A networked sandbox's results are untrusted.
 */
export default function SandboxCard(props: ToolCardProps): JSX.Element {
  const { event } = props
  const p = useParsed(event)
  const d = p.data
  const a = event.arguments
  const name = event.name
  const done = !event.pending && !event.error
  const net = d ? networkLine(d.network) : null
  const path = str(d?.path) || str(d?.from_sandbox) || str(a.path) || (name === 'sandbox_list_files' ? '/workspace' : '')
  const subject = name === 'sandbox_exec' ? undefined : name === 'sandbox_checkpoint' || name === 'sandbox_restore' ? str(a.label) || undefined : path || undefined
  const line = sandboxLine(name, d)
  const entries = name === 'sandbox_list_files' ? sandboxEntries(d) : []
  const total = num(d?.total)
  const state = shellState(name, d, false)
  return (
    <CardShell {...props} icon={<Box size={14} />} title={describeCall(name, {}).verb} subject={subject} hideResult={!unreadable(p, event)}>
      {name === 'sandbox_exec' && <pre className="tc-mono tc-cmd">{str(a.command) || '(no command)'}</pre>}
      {name === 'sandbox_exec' && done && (
        <div className="tc-statusrow">
          <Badge tone={state.tone}>{state.label}</Badge>
        </div>
      )}
      {event.pending && !props.pending && <div className="tc-muted">Running…</div>}
      {line && <div className="tc-muted">{line}</div>}
      <Meta items={[['Network', net]]} />
      {net && <div className="tc-hint warn">This sandbox can reach the internet, so its results are treated as untrusted.</div>}
      {d && str(d.stdout) && <MonoBlock text={str(d.stdout)} tail label={d.truncated === true || p.cut ? 'Output (cut short)' : 'Output'} />}
      {d && str(d.stderr) && <MonoBlock text={str(d.stderr)} tail label="Errors" />}
      {name === 'sandbox_exec' && done && d && !str(d.stdout) && !str(d.stderr) && <div className="tc-muted">No output.</div>}
      {name === 'sandbox_read_file' && d && str(d.text) && (
        <MonoBlock text={str(d.text)} collapseAt={10}
          label={d.truncated === true || p.cut ? `Contents (part of ${num(d.total_bytes)?.toLocaleString('en-US') ?? '?'} bytes)` : 'Contents'} />
      )}
      {name === 'sandbox_write_file' && <MonoBlock text={str(a.content)} label="Content" collapseAt={8} />}
      {entries.length > 0 && (
        <ul className="tc-list mono">
          {entries.map((e) => <li key={e.path}>{e.path}{e.type === 'dir' ? '/' : <span className="tc-muted"> {e.bytes.toLocaleString('en-US')} B</span>}</li>)}
        </ul>
      )}
      {name === 'sandbox_list_files' && total !== null && total > entries.length && <div className="tc-muted">Showing {entries.length} of {total}.</div>}
      {name === 'sandbox_list_files' && done && d && total === 0 && <div className="tc-muted">Empty.</div>}
      {event.images && event.images.length > 0 && (
        <div className="tool-images">
          {event.images.map((im) => (
            <figure key={im.name}>
              <img src={im.data} alt={im.name} />
              <figcaption>{im.name} <a href={im.data} download={im.name}>save</a></figcaption>
            </figure>
          ))}
        </div>
      )}
      <ErrorLine event={event} />
    </CardShell>
  )
}

for (const n of ['sandbox_exec', 'sandbox_read_file', 'sandbox_write_file', 'sandbox_list_files', 'sandbox_put_document', 'sandbox_export_file',
  'sandbox_checkpoint', 'sandbox_restore', 'sandbox_reset']) registerToolCard(n, SandboxCard)

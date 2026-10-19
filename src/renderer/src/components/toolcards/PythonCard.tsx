import { useState } from 'react'
import { FileCode2 } from '../../icons'
import { fmtCallMs, toolsArgLabel, TRACE_VISIBLE, traceRows, visibleTrace } from '../../lib/codeTrace'
import { fmtSeconds, num, str, strList } from '../../lib/toolResult'
import CardShell from './CardShell'
import { Badge, ErrorLine, Meta, MonoBlock, unreadable, useParsed } from './blocks'
import { registerToolCard, type ToolCardProps } from './registry'

/** The inner tool calls a code run made: name, outcome, duration. Capped, with a toggle for the rest. */
function CallTrace({ rows }: { rows: ReturnType<typeof traceRows> }): JSX.Element {
  const [all, setAll] = useState(false)
  const { shown, hidden } = visibleTrace(rows, all)
  return (
    <div className="tc-trace">
      <ul>
        {shown.map((r, i) => (
          <li key={i}>
            <Badge tone={r.mark === 'ok' || r.mark === 'approved' ? 'ok' : 'bad'}>{r.label}</Badge>
            <span className="tc-trace-name">{r.name}</span>
            <span className="tc-muted">{fmtCallMs(r.ms)}</span>
          </li>
        ))}
      </ul>
      {hidden > 0 && <button type="button" className="tc-trace-toggle" onClick={() => setAll(true)}>Show all {rows.length}</button>}
      {all && rows.length > TRACE_VISIBLE && <button type="button" className="tc-trace-toggle" onClick={() => setAll(false)}>Show fewer</button>}
    </div>
  )
}

/** run_python: the script (collapsed), what it printed, and the files it wrote into the desk. Figures show as images under the card. */
function PythonCard(props: ToolCardProps): JSX.Element {
  const { event } = props
  const p = useParsed(event)
  const d = p.data
  const code = str(event.arguments.code)
  const files = d ? strList(d.workspace_files) : []
  const calls = traceRows(d)
  const summary = d ? str(d.summary) : ''
  const code0 = num(d?.exit_code)
  return (
    <CardShell {...props} icon={<FileCode2 size={14} />} title="Run Python" hideResult={!unreadable(p, event)}>
      {props.pending && window.os.platform !== 'darwin' && <div className="tc-hint warn">Runs unsandboxed on this computer, with access to your files and the network.</div>}
      <MonoBlock text={code} label="Script" collapseAt={6} empty="(no code)" />
      {d && !event.error && (
        <div className="tc-statusrow">
          {d.timed_out === true ? <Badge tone="bad">timed out</Badge> : code0 !== null && <Badge tone={code0 === 0 ? 'ok' : 'bad'}>{code0 === 0 ? 'exit 0' : `exit ${code0}`}</Badge>}
          <span className="tc-muted">{fmtSeconds(num(d.duration_s))}</span>
        </div>
      )}
      {summary && <div className="tc-muted tc-trace-summary">{summary}</div>}
      {calls.length > 0 && <CallTrace rows={calls} />}
      {d && str(d.stdout) && <MonoBlock text={str(d.stdout)} tail label="Printed" />}
      {d && str(d.stderr) && <MonoBlock text={str(d.stderr)} tail label="Errors" />}
      <Meta items={[['Tools', toolsArgLabel(event.arguments.tools)], ['Wrote', files.length ? files.join(', ') : null]]} />
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

registerToolCard('run_python', PythonCard)

import { FileCode2 } from 'lucide-react'
import { fmtSeconds, num, str, strList } from '../../lib/toolResult'
import CardShell from './CardShell'
import { Badge, ErrorLine, Meta, MonoBlock, unreadable, useParsed } from './blocks'
import { registerToolCard, type ToolCardProps } from './registry'

/** run_python: the script (collapsed), what it printed, and the files it wrote into the desk. Figures show as images under the card. */
export default function PythonCard(props: ToolCardProps): JSX.Element {
  const { event } = props
  const p = useParsed(event)
  const d = p.data
  const code = str(event.arguments.code)
  const files = d ? strList(d.workspace_files) : []
  const code0 = num(d?.exit_code)
  return (
    <CardShell {...props} icon={<FileCode2 size={14} />} title="Run Python" hideResult={!unreadable(p, event)}>
      <MonoBlock text={code} label="Script" collapseAt={6} empty="(no code)" />
      {d && !event.error && (
        <div className="tc-statusrow">
          {d.timed_out === true ? <Badge tone="bad">timed out</Badge> : code0 !== null && <Badge tone={code0 === 0 ? 'ok' : 'bad'}>{code0 === 0 ? 'exit 0' : `exit ${code0}`}</Badge>}
          <span className="tc-muted">{fmtSeconds(num(d.duration_s))}</span>
        </div>
      )}
      {d && str(d.stdout) && <MonoBlock text={str(d.stdout)} tail label="Printed" />}
      {d && str(d.stderr) && <MonoBlock text={str(d.stderr)} tail label="Errors" />}
      <Meta items={[['Wrote', files.length ? files.join(', ') : null]]} />
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

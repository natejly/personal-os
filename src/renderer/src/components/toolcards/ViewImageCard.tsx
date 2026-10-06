import { Eye } from 'lucide-react'
import { num, str } from '../../lib/toolResult'
import CardShell from './CardShell'
import { Badge, ErrorLine, Meta, MonoBlock, unreadable, useParsed } from './blocks'
import { registerToolCard, type ToolCardProps } from './registry'

/** view_image: which picture, what was asked of it, and what came back (a description, or OCR text when no vision model is set). */
function ViewImageCard(props: ToolCardProps): JSX.Element {
  const { event } = props
  const p = useParsed(event)
  const d = p.data
  const text = str(d?.description) || str(d?.text)
  const ocr = d?.ocr === true
  const w = num(d?.width)
  const h = num(d?.height)
  return (
    <CardShell {...props} icon={<Eye size={14} />} title="Look at image" subject={str(event.arguments.path) || undefined} hideResult={!unreadable(p, event)}>
      <Meta items={[['Question', str(event.arguments.question) || null]]} />
      {text && (
        <div className="tc-section">
          {ocr && <div className="tc-statusrow"><Badge tone="warn">OCR only</Badge><span className="tc-muted">{str(d?.note) || 'No vision model is set, so this is the text found in the picture.'}</span></div>}
          <MonoBlock text={text} collapseAt={8} />
        </div>
      )}
      <Meta items={[['Size', w && h ? `${w} × ${h}` : null], ['Model', str(d?.model) || null]]} />
      <ErrorLine event={event} />
    </CardShell>
  )
}

registerToolCard('view_image', ViewImageCard)

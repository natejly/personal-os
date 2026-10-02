import { FileOutput } from 'lucide-react'
import { str } from '../../lib/toolResult'
import CardShell from './CardShell'
import { ErrorLine, Meta, unreadable, useParsed } from './blocks'
import { registerToolCard, type ToolCardProps } from './registry'

interface Page { page?: unknown; path?: unknown }

const kb = (n: unknown): string | null => (typeof n === 'number' ? (n < 1024 ? `${n} bytes` : `${Math.round(n / 1024).toLocaleString()} KB`) : null)

/** convert_document (source → output, converter used) and render_preview (source → page image paths). Paths only; no thumbnails. */
export default function DocumentCard(props: ToolCardProps): JSX.Element {
  const { event } = props
  const p = useParsed(event)
  const d = p.data
  const a = event.arguments
  const convert = event.name === 'convert_document'
  const pages = d && Array.isArray(d.pages) ? (d.pages as Page[]).filter((x) => typeof x?.path === 'string') : []
  return (
    <CardShell {...props} icon={<FileOutput size={14} />} title={convert ? 'Convert document' : 'Preview document pages'} hideResult={!unreadable(p, event)}>
      <Meta items={[
        ['From', <span className="mono" key="f">{str(a.path)}</span>],
        convert ? ['To', str(d?.output) ? <span className="mono" key="t">{str(d?.output)}</span> : str(a.to) ? `.${str(a.to)}` : null] : ['Pages', str(a.pages) || null],
        ['Converter', str(d?.converter) || null],
        ['Size', kb(d?.bytes)]
      ]} />
      {!convert && pages.length > 0 && (
        <ul className="tc-list mono">
          {pages.map((x, i) => <li key={i}>page {String(x.page ?? i + 1)}: {String(x.path)}</li>)}
        </ul>
      )}
      {!convert && d && str(d.note) && <div className="tc-muted">{str(d.note)}</div>}
      <ErrorLine event={event} />
    </CardShell>
  )
}

registerToolCard('convert_document', DocumentCard)
registerToolCard('render_preview', DocumentCard)

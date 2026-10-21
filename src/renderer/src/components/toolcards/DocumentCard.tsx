import { FilePlus, FileOutput } from '../../icons'
import { str } from '../../lib/toolResult'
import { useStore } from '../../store'
import CardShell from './CardShell'
import { ErrorLine, unreadable, useParsed } from './blocks'
import FileLine from './FileLine'
import { registerToolCard, type ToolCardProps } from './registry'

const base = (p: string): string => p.split('/').filter(Boolean).pop() ?? p

/** convert_document (source → output) and render_preview (source → page images): the produced file as one line that opens in the side panel. */
function DocumentCard(props: ToolCardProps): JSX.Element {
  const { event, conversationId } = props
  const p = useParsed(event)
  const d = p.data
  const a = event.arguments
  const convert = event.name === 'convert_document'
  const pages = d && Array.isArray(d.pages) ? (d.pages as Array<{ path?: unknown }>).map((x) => x?.path).filter((x): x is string => typeof x === 'string') : []
  const target = convert ? str(d?.output) : pages[0] ?? ''
  if (!props.pending && !event.pending && !event.error && target) {
    const title = base(convert ? target : str(a.path) || target)
    return <FileLine icon={<FileOutput size={14} />} title={title} onOpen={conversationId ? () => useStore.getState().openShow(conversationId, { kind: 'file', title: base(target), path: target, name: base(target) }) : undefined} />
  }
  return (
    <CardShell {...props} icon={<FileOutput size={14} />} title={convert ? 'Convert document' : 'Preview document pages'} subject={str(a.path) || undefined} hideResult={!unreadable(p, event)}>
      <ErrorLine event={event} />
    </CardShell>
  )
}

/** doc_create: the new doc as one line that opens it in the Files view. */
function DocCreateCard(props: ToolCardProps): JSX.Element {
  const { event } = props
  const p = useParsed(event)
  const id = str(p.data?.doc_id)
  const title = str(p.data?.created) || str(event.arguments.title) || 'New doc'
  if (!props.pending && !event.pending && !event.error && id) {
    return <FileLine icon={<FilePlus size={14} />} title={title} onOpen={() => void useStore.getState().openDoc(id)} />
  }
  return (
    <CardShell {...props} icon={<FilePlus size={14} />} title="Create doc" subject={title} hideResult={!unreadable(p, event)}>
      <ErrorLine event={event} />
    </CardShell>
  )
}

registerToolCard('doc_create', DocCreateCard)
registerToolCard('convert_document', DocumentCard)
registerToolCard('render_preview', DocumentCard)

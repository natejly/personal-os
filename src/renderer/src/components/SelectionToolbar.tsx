import { useEffect, useState } from 'react'
import { useStore } from '../store'
import { appendToDraft } from '../lib/drafts'
import { COMPOSER_INSERT_EVENT } from '../lib/composerInsert'
import { askDraft, selectionMessage, SELECTION_VERBS, type SelectionVerb } from '../lib/selectionActions'
import { CORE, ownerOn, registerSelectionAction, selectionActions } from '../extensions/host'
import { emitSelection, type SelectionSource } from '../extensions/events'

/** Where a selection gets the toolbar: chat transcripts, the page agent panel, the note editor, the mail reader. */
const SURFACES: Record<SelectionSource, string> = { chat: '.messages', 'page-agent': '.page-agent-body', note: '.md-surface', mail: '.mail-body' }

const surfaceOf = (node: Element): SelectionSource | undefined =>
  (Object.keys(SURFACES) as SelectionSource[]).find((source) => node.closest(SURFACES[source]))

/** The highlighted text right now, from a text box or from the page. */
function selectedText(): string {
  const el = document.activeElement
  if (el instanceof HTMLTextAreaElement || el instanceof HTMLInputElement) return el.value.slice(el.selectionStart ?? 0, el.selectionEnd ?? 0).trim()
  return window.getSelection()?.toString().trim() ?? ''
}

/** Opens the page agent panel and hands it the quote: Ask… waits for a question, the rest send at once. */
export function runSelectionVerb(verb: SelectionVerb, quote: string): void {
  if (!quote) return
  useStore.setState({ pageAgentOpen: true })
  if (verb === 'ask') {
    appendToDraft('page', askDraft(quote))
    // The panel may only just have mounted: let it subscribe before it is told to take focus.
    setTimeout(() => window.dispatchEvent(new CustomEvent(COMPOSER_INSERT_EVENT, { detail: { text: '', key: 'page' } })), 50)
  } else void useStore.getState().sendToPageAgent(selectionMessage(verb, quote))
}

for (const v of SELECTION_VERBS) registerSelectionAction(CORE, { id: v.id, label: v.label, run: (text) => runSelectionVerb(v.id, text) })

/** A small bubble over a selection with its actions (the four verbs, and any an extension adds); the verbs also arrive from the right-click menu. */
export default function SelectionToolbar(): JSX.Element | null {
  const settings = useStore((s) => s.settings)
  const enabled = settings.selectionToolbar !== false
  const [at, setAt] = useState<{ x: number; y: number; quote: string } | null>(null)

  useEffect(() => window.os.onMenu((action) => {
    const found = selectionActions.find((a) => `selection:${a.id}` === action && ownerOn(settings, a.extension))
    if (found) found.run(selectedText())
  }), [settings])

  useEffect(() => {
    if (!enabled) return setAt(null)
    const show = (e: Event): void => {
      if (e.target instanceof Element && e.target.closest('.selection-toolbar')) return
      // After the click has settled the selection.
      setTimeout(() => {
        const quote = selectedText()
        const el = document.activeElement
        const inBox = el instanceof HTMLTextAreaElement
        const sel = window.getSelection()
        const node = inBox ? el : sel?.anchorNode instanceof Element ? sel.anchorNode : sel?.anchorNode?.parentElement
        const source = node && surfaceOf(node)
        if (!quote || !source) return setAt(null)
        emitSelection({ text: quote, source })
        const pointer = e instanceof MouseEvent ? { x: e.clientX, y: e.clientY } : null
        const rect = !inBox && sel && sel.rangeCount ? sel.getRangeAt(0).getBoundingClientRect() : null
        const x = rect ? rect.left + rect.width / 2 : pointer?.x ?? 0
        const y = rect ? rect.top : pointer?.y ?? 0
        setAt({ x: Math.min(Math.max(x, 130), window.innerWidth - 130), y: Math.max(y - 8, 44), quote })
      }, 0)
    }
    const hide = (e: Event): void => {
      if (!(e.target instanceof Element && e.target.closest('.selection-toolbar'))) setAt(null)
    }
    const key = (e: KeyboardEvent): void => (e.key === 'Escape' ? setAt(null) : e.shiftKey ? show(e) : undefined)
    document.addEventListener('mouseup', show)
    document.addEventListener('keyup', key)
    document.addEventListener('mousedown', hide)
    document.addEventListener('scroll', () => setAt(null), true)
    return () => {
      document.removeEventListener('mouseup', show)
      document.removeEventListener('keyup', key)
      document.removeEventListener('mousedown', hide)
    }
  }, [enabled])

  if (!enabled || !at) return null
  return (
    <div className="selection-toolbar" role="toolbar" aria-label="Selection actions" style={{ left: at.x, top: at.y }}
      onMouseDown={(e) => e.preventDefault() /* keep the selection alive while clicking */}>
      {selectionActions.filter((a) => ownerOn(settings, a.extension)).map((a) => (
        <button key={a.id} type="button" onClick={() => { a.run(at.quote); setAt(null) }}>{a.label}</button>
      ))}
    </div>
  )
}

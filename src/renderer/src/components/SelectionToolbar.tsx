import { useEffect, useState } from 'react'
import { useStore } from '../store'
import { appendToDraft } from '../lib/drafts'
import { COMPOSER_INSERT_EVENT } from '../lib/composerInsert'
import { askDraft, selectionMessage, SELECTION_VERBS, type SelectionVerb } from '../lib/selectionActions'

/** Where a selection gets the toolbar: chat transcripts, the page agent panel, the note editor, the mail reader. */
const SURFACES = '.messages, .page-agent-body, .md-surface, .mail-body'

const verbOf = (action: string): SelectionVerb | undefined => SELECTION_VERBS.find((v) => `selection:${v.id}` === action)?.id

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

/** A small bubble over a selection with the four verbs; the same verbs arrive from the right-click menu. */
export default function SelectionToolbar(): JSX.Element | null {
  const enabled = useStore((s) => s.settings.selectionToolbar !== false)
  const [at, setAt] = useState<{ x: number; y: number; quote: string } | null>(null)

  useEffect(() => window.os.onMenu((action) => {
    const verb = verbOf(action)
    if (verb) runSelectionVerb(verb, selectedText())
  }), [])

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
        if (!quote || !node || !node.closest(SURFACES)) return setAt(null)
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
      {SELECTION_VERBS.map((v) => (
        <button key={v.id} type="button" onClick={() => { runSelectionVerb(v.id, at.quote); setAt(null) }}>{v.label}</button>
      ))}
    </div>
  )
}

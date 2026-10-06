import { useCallback, useEffect, useRef, useState, type RefObject } from 'react'
import { ChevronDown, ChevronUp, X } from 'lucide-react'
import { findMatches } from '../lib/findText'

const SKIP = '.katex-mathml, .doc-find'
let nextId = 0

function clear(): void {
  try { CSS.highlights.delete('doc-find'); CSS.highlights.delete('doc-find-current') } catch { /* no Highlight API */ }
}

/**
 * Find inside one doc surface: the editor's highlight mirror or the rendered pane. ⌘F reaches the page as
 * the Edit menu's `chat:find`, so this answers only when the last pointer or focus landed inside `scope`
 * (or, with `fallback`, when nothing is focused). Matches are painted with the CSS Custom Highlight API;
 * the DOM under `textRoot` never changes. Renders null while closed.
 */
export default function DocFind({ scope, textRoot, reveal, onClose, fallback }: {
  scope: RefObject<HTMLElement>
  textRoot: () => Element | null
  /** Bring a match into view; the default scrolls its element to the centre. */
  reveal?: (r: Range) => void
  onClose?: () => void
  fallback?: boolean
}): JSX.Element | null {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [count, setCount] = useState(0)
  const [index, setIndex] = useState(0)
  const input = useRef<HTMLInputElement>(null)
  const ranges = useRef<Range[]>([])
  const indexRef = useRef(0)
  indexRef.current = index
  const inside = useRef(false)
  const id = useRef(++nextId)

  const paint = useCallback((cur: number, scrollTo: boolean) => {
    const current = ranges.current[cur]
    try {
      CSS.highlights.set('doc-find', new Highlight(...ranges.current.filter((_, i) => i !== cur)))
      if (current) CSS.highlights.set('doc-find-current', new Highlight(current))
      else CSS.highlights.delete('doc-find-current')
    } catch { /* no Highlight API */ }
    if (scrollTo && current) {
      if (reveal) reveal(current)
      else current.startContainer.parentElement?.scrollIntoView({ block: 'center' })
    }
  }, [reveal])

  // Recompute the ranges for `q`; `scrollTo` is true only when the user asked (typing, stepping).
  const compute = useCallback((q: string, scrollTo: boolean) => {
    const root = textRoot()
    if (!root || !q.trim()) { ranges.current = []; setCount(0); clear(); return }
    const nodes: Text[] = []
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT)
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
      const t = n as Text
      const el = t.parentElement
      if (t.nodeValue && el && !el.closest(SKIP) && el.checkVisibility()) nodes.push(t)
    }
    const out = findMatches(nodes.map((t) => t.nodeValue ?? ''), q).map((m) => {
      const r = document.createRange()
      r.setStart(nodes[m.startSeg], m.startOff)
      r.setEnd(nodes[m.endSeg], m.endOff)
      return r
    })
    ranges.current = out
    setCount(out.length)
    const cur = scrollTo ? 0 : Math.min(indexRef.current, Math.max(0, out.length - 1))
    setIndex(cur)
    paint(cur, scrollTo)
  }, [textRoot, paint])

  const close = useCallback((refocus: boolean) => {
    setOpen(false); setQuery(''); setCount(0); ranges.current = []; clear()
    if (refocus) onClose?.()
  }, [onClose])

  const step = useCallback((dir: 1 | -1) => {
    const n = ranges.current.length
    if (!n) return
    const next = (indexRef.current + dir + n) % n
    setIndex(next)
    paint(next, true)
  }, [paint])

  // Which doc surface the user is working in: the last pointer or focus event decides.
  useEffect(() => {
    const mark = (e: Event): void => { inside.current = !!scope.current?.contains(e.target as Node) }
    document.addEventListener('pointerdown', mark, true)
    document.addEventListener('focusin', mark, true)
    return () => { document.removeEventListener('pointerdown', mark, true); document.removeEventListener('focusin', mark, true) }
  }, [scope])

  useEffect(() => window.os.onMenu((action) => {
    if (action === 'chat:find') {
      const idle = !document.activeElement || document.activeElement === document.body
      if (!inside.current && !(fallback && idle)) return
      window.dispatchEvent(new CustomEvent('doc-find-open', { detail: id.current }))
      setOpen(true)
      requestAnimationFrame(() => { input.current?.focus(); input.current?.select() })
    } else if (open && action === 'chat:find-next') step(1)
    else if (open && action === 'chat:find-prev') step(-1)
  }), [fallback, open, step])

  // Only one doc surface at a time owns the highlight names (split view has two).
  useEffect(() => {
    const other = (e: Event): void => { if ((e as CustomEvent<number>).detail !== id.current) close(false) }
    window.addEventListener('doc-find-open', other)
    return () => window.removeEventListener('doc-find-open', other)
  }, [close])
  useEffect(() => () => { if (ranges.current.length) clear() }, [])

  useEffect(() => {
    if (!open) return
    const t = setTimeout(() => compute(query, true), 120)
    return () => clearTimeout(t)
  }, [open, query, compute])

  // Typing in the editor or a re-render replaces the text under the ranges; recount quietly.
  useEffect(() => {
    const root = open ? textRoot() : null
    if (!root) return
    let t: ReturnType<typeof setTimeout> | undefined
    const obs = new MutationObserver(() => { clearTimeout(t); t = setTimeout(() => compute(query, false), 120) })
    obs.observe(root, { childList: true, subtree: true, characterData: true })
    return () => { clearTimeout(t); obs.disconnect() }
  }, [open, query, textRoot, compute])

  if (!open) return null
  return (
    <div className="doc-find" role="search" onDoubleClick={(e) => e.stopPropagation()}>
      <input
        ref={input}
        aria-label="Find in file"
        placeholder="Find in file"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(true) }
          else if (e.key === 'Enter') { e.preventDefault(); step(e.shiftKey ? -1 : 1) }
        }}
      />
      <span className="find-count" aria-live="polite">{!query.trim() ? '' : count ? `${index + 1} of ${count}` : 'No matches'}</span>
      <button className="icon-btn" aria-label="Previous match" title="Previous (Shift+Enter)" onClick={() => step(-1)}><ChevronUp size={14} /></button>
      <button className="icon-btn" aria-label="Next match" title="Next (Enter)" onClick={() => step(1)}><ChevronDown size={14} /></button>
      <button className="icon-btn" aria-label="Close find" title="Close (Esc)" onClick={() => close(true)}><X size={14} /></button>
    </div>
  )
}

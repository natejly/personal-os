import { useCallback, useEffect, useRef, useState, type RefObject } from 'react'
import { ChevronDown, ChevronUp, X } from 'lucide-react'
import { findMatches } from '../lib/findText'

const SKIP = '.msg-actions, .avatar, .katex-mathml'

/** Text nodes of every message under `root`, in document order, minus chrome and hidden copies. */
function collect(root: Element): Text[] {
  const nodes: Text[] = []
  for (const msg of Array.from(root.querySelectorAll('.msg'))) {
    const walker = document.createTreeWalker(msg, NodeFilter.SHOW_TEXT)
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
      const t = n as Text
      const el = t.parentElement
      if (!t.nodeValue || !el || el.closest(SKIP) || !el.checkVisibility()) continue
      nodes.push(t)
    }
  }
  return nodes
}

function clear(): void {
  try { CSS.highlights.delete('chat-find'); CSS.highlights.delete('chat-find-current') } catch { /* no Highlight API */ }
}

/**
 * Find in the open conversation. Matches are painted with the CSS Custom Highlight API, so nothing in the
 * transcript's DOM changes. The bar renders null while closed; `resetKey` (the conversation id) closes it.
 */
export default function FindBar({ scope, resetKey }: { scope: RefObject<HTMLElement>; resetKey?: string }): JSX.Element | null {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [count, setCount] = useState(0)
  const [index, setIndex] = useState(0)
  const input = useRef<HTMLInputElement>(null)
  const ranges = useRef<Range[]>([])
  const indexRef = useRef(0)
  indexRef.current = index

  const paint = useCallback((cur: number, scroll: boolean) => {
    const current = ranges.current[cur]
    try {
      CSS.highlights.set('chat-find', new Highlight(...ranges.current.filter((_, i) => i !== cur)))
      if (current) CSS.highlights.set('chat-find-current', new Highlight(current))
      else CSS.highlights.delete('chat-find-current')
    } catch { /* no Highlight API */ }
    if (scroll) current?.startContainer.parentElement?.scrollIntoView({ block: 'center' })
  }, [])

  // Recompute the ranges for `query`; `scroll` is true only when the user asked (typing, stepping).
  const compute = useCallback((q: string, scroll: boolean) => {
    const root = scope.current?.querySelector('.messages-inner')
    if (!root || !q.trim()) { ranges.current = []; setCount(0); clear(); return }
    const nodes = collect(root)
    const out: Range[] = []
    // Matches never cross a message: one findMatches call per message's run of nodes.
    const perMsg = new Map<Element | null, Text[]>()
    for (const t of nodes) {
      const k = t.parentElement?.closest('.msg') ?? null
      const list = perMsg.get(k)
      if (list) list.push(t); else perMsg.set(k, [t])
    }
    for (const list of perMsg.values()) {
      for (const m of findMatches(list.map((t) => t.nodeValue ?? ''), q, 2000 - out.length)) {
        const r = document.createRange()
        r.setStart(list[m.startSeg], m.startOff)
        r.setEnd(list[m.endSeg], m.endOff)
        out.push(r)
      }
      if (out.length >= 2000) break
    }
    ranges.current = out
    setCount(out.length)
    const cur = scroll ? 0 : Math.min(indexRef.current, Math.max(0, out.length - 1))
    setIndex(cur)
    paint(cur, scroll)
  }, [scope, paint])

  const close = useCallback(() => {
    setOpen(false); setQuery(''); setCount(0); ranges.current = []; clear()
  }, [])

  const step = useCallback((dir: 1 | -1) => {
    const n = ranges.current.length
    if (!n) return
    const next = (indexRef.current + dir + n) % n
    setIndex(next)
    paint(next, true)
  }, [paint])

  useEffect(() => window.os.onMenu((action) => {
    if (action === 'chat:find') {
      if (!scope.current?.querySelector('.messages-inner')) return
      setOpen(true)
      requestAnimationFrame(() => { input.current?.focus(); input.current?.select() })
    } else if (action === 'chat:find-next') step(1)
    else if (action === 'chat:find-prev') step(-1)
  }), [scope, step])

  useEffect(() => { close() }, [resetKey, close])
  useEffect(() => clear, [])

  useEffect(() => {
    if (!open) return
    const t = setTimeout(() => compute(query, true), 150)
    return () => clearTimeout(t)
  }, [open, query, compute])

  // Streaming tokens, expanded rows and late renders change the text under the ranges; recount quietly.
  useEffect(() => {
    const root = open ? scope.current?.querySelector('.messages-inner') : null
    if (!root) return
    let t: ReturnType<typeof setTimeout> | undefined
    const obs = new MutationObserver(() => { clearTimeout(t); t = setTimeout(() => compute(query, false), 150) })
    obs.observe(root, { childList: true, subtree: true, characterData: true })
    return () => { clearTimeout(t); obs.disconnect() }
  }, [open, query, scope, compute])

  if (!open) return null
  return (
    <div className="find-bar" role="search">
      <input
        ref={input}
        aria-label="Find in conversation"
        placeholder="Find in conversation"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Escape') { e.preventDefault(); close(); document.querySelector<HTMLTextAreaElement>('.composer textarea')?.focus() }
          else if (e.key === 'Enter') { e.preventDefault(); step(e.shiftKey ? -1 : 1) }
        }}
      />
      <span className="find-count" aria-live="polite">{!query.trim() ? '' : count ? `${index + 1} of ${count}` : 'No matches'}</span>
      <button className="icon-btn" aria-label="Previous match" title="Previous (⇧⌘G)" onClick={() => step(-1)}><ChevronUp size={14} /></button>
      <button className="icon-btn" aria-label="Next match" title="Next (⌘G)" onClick={() => step(1)}><ChevronDown size={14} /></button>
      <button className="icon-btn" aria-label="Close find" title="Close (Esc)" onClick={close}><X size={14} /></button>
      <span className="find-hint">Collapsed tool rows and reasoning are not searched.</span>
    </div>
  )
}

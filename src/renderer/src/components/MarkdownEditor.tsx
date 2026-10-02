import { forwardRef, useCallback, useEffect, useImperativeHandle, useLayoutEffect, useMemo, useRef, useState } from 'react'
import CaretMenu from '../features/notes/CaretMenu'
import { measureCaret, type CaretRect } from '../features/notes/caretPosition'
import type { MarkdownEditorHandle } from '../features/notes/handle'
import { linkFromPaste } from '../features/notes/smartPaste'
import { builtinCommands, detectSlash, filterCommands, type SlashCommand } from '../features/notes/slash'
import { readingTime, wordCount } from '../features/notes/stats'
import { diffRange, insertWithoutFocus, replaceInTextarea } from '../features/notes/textEdit'
import { detectWikiTrigger, filterTargets, wikiText } from '../features/notes/wikilinks'
import '../styles/notes.css'

/**
 * The editing surface: a plain textarea over a highlighted mirror of the same text.
 *
 * The textarea keeps its own caret, undo stack, IME and spellcheck — everything a hand-rolled
 * contenteditable loses — and the mirror behind it paints markdown and maths. They must agree to the
 * pixel, so both use the same font metrics and padding, and the mirror scrolls with the textarea.
 */

export interface EditorHandleProps {
  value: string
  onChange: (next: string) => void
  /** ⌘S — the editor autosaves anyway; this is for people who cannot help themselves. */
  onSave?: () => void
  placeholder?: string
  readOnly?: boolean
  wrap?: boolean
  /** Scroll position as a 0..1 fraction, so a side-by-side preview can follow. */
  onScrollFraction?: (f: number) => void
  /** Opt in: typing `/` at a line start or after a space opens the command menu. */
  slash?: boolean
  /** Commands appended after the built-in ones (Record, Dictate, ...). Only used with `slash`. */
  extraCommands?: SlashCommand[]
  /** Opt in: docs `[[` can link to. Also turns on the `[[...]]` tint in the highlight layer. */
  linkTargets?: { id: string; title: string }[]
  /** Opt in: pasting a URL over selected text makes `[selection](url)`. */
  smartPaste?: boolean
  /** Called with the caret's 1-based line whenever it changes (drives the outline). */
  onCaretLine?: (line: number) => void
  /** Opt in: reading time and the size of the selection in the status bar. */
  richStatus?: boolean
  /** Keep the caret line at ~45% of the editor height as you type. */
  typewriter?: boolean
  /** Dim everything outside the current paragraph; hides the gutter and status bar. */
  focusMode?: boolean
}

export type { MarkdownEditorHandle }

const esc = (s: string): string => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')

/**
 * A deliberately small markdown tokenizer for the highlight layer. It is line-oriented, which is what
 * keeps it honest against the textarea: every input line produces exactly one output line, so the two
 * layers can never drift. Fenced code and maths blocks are tracked as state across lines.
 */
export function highlight(src: string, wikilinks = false, activeLine?: number): string {
  const out = highlightLines(src, wikilinks)
  if (activeLine === undefined) return out.join('\n')
  const [a, b] = paragraphRange(src.split('\n'), activeLine - 1)
  return out.map((h, i) => (i < a || i > b ? `<span class="dim">${h}</span>` : h)).join('\n')
}

/** Inclusive 0-based line span of the blank-line-delimited paragraph holding line `i`. */
export function paragraphRange(lines: string[], i: number): [number, number] {
  if (!(lines[i] ?? '').trim()) return [i, i]
  let a = i
  let b = i
  while (a > 0 && lines[a - 1].trim()) a--
  while (b < lines.length - 1 && lines[b + 1].trim()) b++
  return [a, b]
}

function highlightLines(src: string, wikilinks: boolean): string[] {
  const out: string[] = []
  let fence: string | null = null
  let mathBlock = false
  for (const raw of src.split('\n')) {
    const line = raw
    if (fence !== null) {
      out.push(`<span class="tk-code">${esc(line)}</span>`)
      if (line.trimStart().startsWith(fence)) fence = null
      continue
    }
    const fenceOpen = /^\s*(```+|~~~+)/.exec(line)
    if (fenceOpen) {
      fence = fenceOpen[1].slice(0, 3)
      out.push(`<span class="tk-fence">${esc(line)}</span>`)
      continue
    }
    if (mathBlock) {
      out.push(`<span class="tk-math">${esc(line)}</span>`)
      if (line.includes('$$')) mathBlock = false
      continue
    }
    if (/^\s*\$\$\s*$/.test(line)) {
      mathBlock = true
      out.push(`<span class="tk-math">${esc(line)}</span>`)
      continue
    }
    out.push(inline(line, wikilinks))
  }
  return out
}

function inline(line: string, wiki: boolean): string {
  // Block-level prefixes first — they colour the whole line.
  const heading = /^(\s{0,3}#{1,6}\s)(.*)$/.exec(line)
  if (heading) return `<span class="tk-head">${esc(heading[1])}${span(heading[2], wiki)}</span>`
  const quote = /^(\s*>+\s?)(.*)$/.exec(line)
  if (quote) return `<span class="tk-punct">${esc(quote[1])}</span><span class="tk-quote">${span(quote[2], wiki)}</span>`
  const rule = /^\s*([-*_])(\s*\1){2,}\s*$/.test(line)
  if (rule) return `<span class="tk-punct">${esc(line)}</span>`
  const list = /^(\s*(?:[-*+]|\d+[.)])\s+(?:\[[ xX]\]\s+)?)(.*)$/.exec(line)
  if (list) return `<span class="tk-bullet">${esc(list[1])}</span>${span(list[2], wiki)}`
  const table = /^\s*\|/.test(line)
  if (table) return `<span class="tk-table">${esc(line)}</span>`
  return span(line, wiki)
}

/** Inline spans: maths, code, links, emphasis. One pass, longest-delimiter-first. */
function span(text: string, wiki = false): string {
  const pattern = new RegExp(
    [
      '(\\$\\$[^$]+\\$\\$)', // block maths on one line
      '(\\$(?:\\\\.|[^$\\\\\\n])+\\$)', // inline maths
      '(`[^`\\n]+`)', // code
      // Only when the host opted in, and before the plain link so `[[a]](b)` is not misread.
      ...(wiki ? ['(\\[\\[[^\\[\\]\\n|]+(?:\\|[^\\[\\]\\n]+)?\\]\\])'] : []), // wikilink
      '(!?\\[[^\\]\\n]*\\]\\([^)\\n]*\\))', // link / image
      '(\\*\\*[^*\\n]+\\*\\*|__[^_\\n]+__)', // strong
      '(\\*[^*\\n]+\\*|_[^_\\n]+_)', // emphasis
      '(~~[^~\\n]+~~)' // strike
    ].join('|'),
    'g'
  )
  let out = ''
  let last = 0
  for (let m = pattern.exec(text); m; m = pattern.exec(text)) {
    out += esc(text.slice(last, m.index))
    // With the wikilink group present every later group shifts up by one.
    const g = wiki ? m.slice(1) : [m[1], m[2], m[3], undefined, ...m.slice(4)]
    const cls = g[0] || g[1] ? 'tk-math' : g[2] ? 'tk-code' : g[3] ? 'tk-wikilink' : g[4] ? 'tk-link' : g[5] ? 'tk-strong' : g[6] ? 'tk-em' : 'tk-strike'
    out += `<span class="${cls}">${esc(m[0])}</span>`
    last = m.index + m[0].length
  }
  return out + esc(text.slice(last))
}

/** Wrap or unwrap the selection with a markdown delimiter, keeping the selection on the text. */
function wrapSelection(el: HTMLTextAreaElement, left: string, right = left): { value: string; start: number; end: number } {
  const { value, selectionStart: s, selectionEnd: e } = el
  const sel = value.slice(s, e)
  const already = value.slice(s - left.length, s) === left && value.slice(e, e + right.length) === right
  if (already) {
    return { value: value.slice(0, s - left.length) + sel + value.slice(e + right.length), start: s - left.length, end: e - left.length }
  }
  if (sel.startsWith(left) && sel.endsWith(right) && sel.length >= left.length + right.length) {
    const inner = sel.slice(left.length, sel.length - right.length)
    return { value: value.slice(0, s) + inner + value.slice(e), start: s, end: s + inner.length }
  }
  return { value: value.slice(0, s) + left + sel + right + value.slice(e), start: s + left.length, end: e + left.length }
}

const LIST_ITEM = /^(\s*)([-*+]|(\d+)[.)])(\s+)(\[[ xX]\]\s+)?(.*)$/

/** Indent or outdent every line the selection touches. */
function shiftLines(value: string, s: number, e: number, out: boolean): { value: string; start: number; end: number } {
  const from = value.lastIndexOf('\n', s - 1) + 1
  const toEnd = value.indexOf('\n', e)
  const to = toEnd === -1 ? value.length : toEnd
  const block = value.slice(from, to)
  let firstDelta = 0
  const lines = block.split('\n').map((l, i) => {
    if (out) {
      const m = /^(\t| {1,2})/.exec(l)
      if (!m) return l
      if (i === 0) firstDelta = -m[1].length
      return l.slice(m[1].length)
    }
    if (i === 0) firstDelta = 2
    return '  ' + l
  })
  const next = lines.join('\n')
  return { value: value.slice(0, from) + next + value.slice(to), start: Math.max(from, s + firstDelta), end: e + (next.length - block.length) }
}

const MarkdownEditor = forwardRef<MarkdownEditorHandle, EditorHandleProps>(function MarkdownEditor({
  value, onChange, onSave, placeholder, readOnly = false, wrap = true, onScrollFraction,
  slash = false, extraCommands, linkTargets, smartPaste = false, onCaretLine, richStatus = false, typewriter = false, focusMode = false
}, ref): JSX.Element {
  const ta = useRef<HTMLTextAreaElement>(null)
  const mirror = useRef<HTMLPreElement>(null)
  const surface = useRef<HTMLDivElement>(null)
  const gutter = useRef<HTMLDivElement>(null)
  const [caret, setCaret] = useState({ line: 1, col: 1 })
  const [sel, setSel] = useState({ start: 0, end: 0 })
  const lineCount = useMemo(() => value.split('\n').length, [value])
  const wikiOn = !!linkTargets
  // Keyed on the paragraph span, not the caret line, so moving within a paragraph does not re-highlight.
  const para = useMemo(() => (focusMode ? paragraphRange(value.split('\n'), caret.line - 1).join(':') : ''), [focusMode, value, caret.line])
  const html = useMemo(() => {
    if (!focusMode) return highlight(value, wikiOn) + '\n'
    return highlight(value, wikiOn, Number(para.split(':')[0]) + 1) + '\n'
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value, wikiOn, focusMode, para])

  const syncScroll = useCallback((): void => {
    const el = ta.current
    if (!el) return
    if (mirror.current) {
      mirror.current.scrollTop = el.scrollTop
      mirror.current.scrollLeft = el.scrollLeft
    }
    if (gutter.current) gutter.current.scrollTop = el.scrollTop
    if (onScrollFraction) {
      const range = el.scrollHeight - el.clientHeight
      onScrollFraction(range > 0 ? el.scrollTop / range : 0)
    }
  }, [onScrollFraction])

  useLayoutEffect(syncScroll, [value, syncScroll])

  // A mouse click moves the caret where the reader pointed; typewriter scrolling waits for typing or keys.
  const clicked = useRef(false)
  const trackCaret = useCallback((): void => {
    const el = ta.current
    if (!el) return
    const upto = el.value.slice(0, el.selectionStart)
    const nl = upto.lastIndexOf('\n')
    setCaret({ line: upto.split('\n').length, col: el.selectionStart - nl })
    const start = el.selectionStart
    const end = el.selectionEnd
    setSel((s) => (s.start === start && s.end === end ? s : { start, end }))
  }, [])

  // After the mirror repaints: put the caret line at ~45% of the height. The mirror follows via syncScroll.
  useLayoutEffect(() => {
    const el = ta.current
    if (!typewriter || !el || !mirror.current || document.activeElement !== el || clicked.current) return
    const r = measureCaret(mirror.current, el.selectionStart)
    if (!r) return
    const top = r.top - mirror.current.getBoundingClientRect().top + el.scrollTop
    el.scrollTop = Math.max(0, top - el.clientHeight * 0.45)
    syncScroll()
  }, [typewriter, value, caret.line, caret.col, syncScroll])

  const lastLine = useRef(0)
  useEffect(() => {
    if (caret.line === lastLine.current) return
    lastLine.current = caret.line
    onCaretLine?.(caret.line)
  }, [caret.line, onCaretLine])

  /**
   * Apply a computed whole-value edit as ONE replacement of just the range that changed, through the
   * undo-preserving insert. `setRangeText` over the whole value would make ⌘Z drop or skip the edit.
   * The `input` event the insert fires is what reaches `onChange`.
   */
  const apply = useCallback((next: { value: string; start: number; end: number }): void => {
    const el = ta.current
    if (!el) return
    const d = diffRange(el.value, next.value)
    if (d.start === d.end && d.text === '') el.setSelectionRange(next.start, next.end)
    else replaceInTextarea(el, d.start, d.end, d.text, next.start, next.end)
    trackCaret()
  }, [trackCaret])

  // ---- slash menu and wikilink picker ----
  const commands = useMemo(() => (slash ? [...builtinCommands(), ...(extraCommands ?? [])] : []), [slash, extraCommands])
  const trigger = useMemo((): { kind: 'slash' | 'wiki'; start: number; query: string } | null => {
    if (readOnly || sel.start !== sel.end) return null
    if (wikiOn) {
      const w = detectWikiTrigger(value, sel.start)
      if (w) return { kind: 'wiki', ...w }
    }
    if (slash) {
      const s = detectSlash(value, sel.start)
      if (s) return { kind: 'slash', ...s }
    }
    return null
  }, [value, sel.start, sel.end, readOnly, wikiOn, slash])
  const menuItems = useMemo(() => {
    if (!trigger) return []
    if (trigger.kind === 'slash') return filterCommands(commands, trigger.query).map((c) => ({ key: c.id, label: c.label, hint: c.hint }))
    return filterTargets(linkTargets ?? [], trigger.query).map((t) => ({ key: t.id, label: t.title || 'Untitled' }))
  }, [trigger, commands, linkTargets])
  const menuKey = trigger ? `${trigger.kind}:${trigger.start}` : ''
  // Escape closes the menu for this trigger only; a new `/` or `[[` opens it again.
  const [dismissed, setDismissed] = useState('')
  useEffect(() => { if (!trigger) setDismissed('') }, [trigger])
  const menuOpen = !!trigger && menuItems.length > 0 && dismissed !== menuKey
  const [act, setAct] = useState({ key: '', i: 0 })
  const queryKey = trigger ? `${menuKey}:${trigger.query}` : ''
  const active = act.key === queryKey ? Math.min(act.i, Math.max(0, menuItems.length - 1)) : 0

  const [anchor, setAnchor] = useState<{ rect: CaretRect; w: number; h: number } | null>(null)
  const placeMenu = useCallback((): void => {
    const sr = surface.current?.getBoundingClientRect()
    if (!menuOpen || !trigger || !mirror.current || !sr) { setAnchor(null); return }
    const r = measureCaret(mirror.current, trigger.start)
    if (!r) { setAnchor(null); return }
    const rect = { top: r.top - sr.top, left: r.left - sr.left, height: r.height }
    setAnchor((cur) => (cur && cur.rect.top === rect.top && cur.rect.left === rect.left && cur.w === sr.width && cur.h === sr.height
      ? cur : { rect, w: sr.width, h: sr.height }))
  }, [menuOpen, trigger])
  // After the mirror has repainted this value, so the marker position is for the text the user sees.
  useLayoutEffect(placeMenu, [placeMenu, value, wrap])

  const pick = (i: number): void => {
    const el = ta.current
    if (!el || !trigger) return
    const end = el.selectionStart
    if (trigger.kind === 'slash') {
      const cmd = filterCommands(commands, trigger.query)[i]
      if (!cmd) return
      // Remove the typed `/query`, then let the command insert where it stood.
      replaceInTextarea(el, trigger.start, end, '')
      trackCaret()
      cmd.run(handle)
      return
    }
    const target = filterTargets(linkTargets ?? [], trigger.query)[i]
    if (!target) return
    // Swallow a `]]` that was already typed after the caret instead of doubling it.
    const tail = el.value.slice(end, end + 2) === ']]' ? 2 : 0
    replaceInTextarea(el, trigger.start, end + tail, wikiText(target.title))
    trackCaret()
  }

  const handle: MarkdownEditorHandle = useMemo(() => ({
    focus: () => ta.current?.focus(),
    insertAtCaret: (text, caretOffset) => {
      const el = ta.current
      if (!el || el.readOnly) return
      const s = el.selectionStart
      replaceInTextarea(el, s, el.selectionEnd, text, caretOffset === undefined ? undefined : s + caretOffset)
      trackCaret()
    },
    insertQuietly: (text) => {
      const el = ta.current
      if (!el || el.readOnly) return false
      insertWithoutFocus(el, el.selectionStart, el.selectionEnd, text)
      trackCaret()
      return true
    },
    replaceRange: (start, end, text) => {
      const el = ta.current
      if (!el || el.readOnly) return
      replaceInTextarea(el, start, end, text)
      trackCaret()
    },
    getSelection: () => {
      const el = ta.current
      const start = el?.selectionStart ?? 0
      const end = el?.selectionEnd ?? 0
      return { start, end, text: el ? el.value.slice(start, end) : '' }
    },
    getText: () => ta.current?.value ?? '',
    jumpToLine: (line) => {
      const el = ta.current
      if (!el) return
      let at = 0
      for (let n = 1; n < line; n++) {
        const nl = el.value.indexOf('\n', at)
        if (nl === -1) break
        at = nl + 1
      }
      el.focus()
      el.setSelectionRange(at, at)
      trackCaret()
      const r = mirror.current ? measureCaret(mirror.current, at) : null
      if (r && mirror.current) {
        // Mirror and textarea share a scroll offset, so the caret's content y is its viewport y plus that offset.
        el.scrollTop = Math.max(0, r.top - mirror.current.getBoundingClientRect().top + el.scrollTop - el.clientHeight * 0.25)
      }
    }
  }), [trackCaret])
  useImperativeHandle(ref, () => handle, [handle])

  const onPaste = (e: React.ClipboardEvent<HTMLTextAreaElement>): void => {
    if (!smartPaste || readOnly) return
    const el = e.currentTarget
    const link = linkFromPaste(el.value.slice(el.selectionStart, el.selectionEnd), e.clipboardData.getData('text/plain'))
    if (!link) return
    e.preventDefault()
    replaceInTextarea(el, el.selectionStart, el.selectionEnd, link)
    trackCaret()
  }

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>): void => {
    const el = e.currentTarget
    const mod = e.metaKey || e.ctrlKey
    if (mod && e.key.toLowerCase() === 's') {
      e.preventDefault()
      onSave?.()
      return
    }
    if (readOnly) return
    if (menuOpen && !e.nativeEvent.isComposing && !mod) {
      const n = menuItems.length
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault()
        setAct({ key: queryKey, i: (active + (e.key === 'ArrowDown' ? 1 : n - 1)) % n })
        return
      }
      if (e.key === 'Enter' || e.key === 'Tab') {
        e.preventDefault()
        return pick(active)
      }
      if (e.key === 'Escape') {
        e.preventDefault()
        e.stopPropagation()
        setDismissed(menuKey)
        return
      }
    }
    if (mod && !e.shiftKey && e.key.toLowerCase() === 'b') {
      e.preventDefault()
      return apply(wrapSelection(el, '**'))
    }
    // ⇧⌘I, not ⌘I: the app menu owns ⌘I (Ask About This Page) and a menu accelerator never
    // reaches the page, so the plain chord here could not fire. ⌥⌘I is Electron's dev tools.
    if (mod && e.shiftKey && e.key.toLowerCase() === 'i') {
      e.preventDefault()
      return apply(wrapSelection(el, '*'))
    }
    if (mod && e.shiftKey && e.key.toLowerCase() === 'e') {
      e.preventDefault()
      return apply(wrapSelection(el, '`'))
    }
    if (mod && e.shiftKey && e.key.toLowerCase() === 'm') {
      e.preventDefault()
      return apply(wrapSelection(el, '$'))
    }
    if (mod && e.key.toLowerCase() === 'k') {
      e.preventDefault()
      const { value: v, selectionStart: s, selectionEnd: en } = el
      const sel = v.slice(s, en)
      // Selection becomes the link text; the caret lands where the url goes.
      const next = `${v.slice(0, s)}[${sel}]()${v.slice(en)}`
      return apply({ value: next, start: s + sel.length + 3, end: s + sel.length + 3 })
    }
    if (e.key === 'Tab') {
      e.preventDefault()
      const { value: v, selectionStart: s, selectionEnd: en } = el
      if (s !== en || e.shiftKey) return apply(shiftLines(v, s, en, e.shiftKey))
      return apply({ value: v.slice(0, s) + '  ' + v.slice(en), start: s + 2, end: s + 2 })
    }
    if (e.key === 'Enter' && !e.shiftKey && !mod) {
      // Continue the list the caret is sitting in; a second Enter on an empty item ends the list.
      const { value: v, selectionStart: s, selectionEnd: en } = el
      if (s !== en) return
      const from = v.lastIndexOf('\n', s - 1) + 1
      const cur = v.slice(from, s)
      const m = LIST_ITEM.exec(cur)
      if (!m) return
      const [, indent, marker, num, gap, task, body] = m
      if (!body.trim()) {
        e.preventDefault()
        return apply({ value: v.slice(0, from) + v.slice(s), start: from, end: from })
      }
      e.preventDefault()
      const nextMarker = num ? `${Number(num) + 1}${marker.slice(String(num).length)}` : marker
      const insert = `\n${indent}${nextMarker}${gap}${task ? task.replace(/[xX]/, ' ') : ''}`
      return apply({ value: v.slice(0, s) + insert + v.slice(en), start: s + insert.length, end: s + insert.length })
    }
  }

  return (
    <div className={`md-editor ${wrap ? '' : 'nowrap'} ${focusMode ? 'focus' : ''}`}>
      <div className="md-gutter" ref={gutter} aria-hidden>
        {Array.from({ length: lineCount }, (_, i) => (
          <div key={i} className={i + 1 === caret.line ? 'cur' : undefined}>{i + 1}</div>
        ))}
      </div>
      <div className="md-surface" ref={surface}>
        <pre className="md-mirror" ref={mirror} aria-hidden dangerouslySetInnerHTML={{ __html: html }} />
        <textarea
          ref={ta}
          className="md-input"
          value={value}
          placeholder={placeholder}
          readOnly={readOnly}
          spellCheck
          wrap={wrap ? 'soft' : 'off'}
          onChange={(e) => { clicked.current = false; onChange(e.target.value); trackCaret() }}
          onMouseDown={() => { clicked.current = true }}
          onKeyDown={(e) => { clicked.current = false; onKeyDown(e) }}
          onKeyUp={trackCaret}
          onClick={trackCaret}
          onSelect={trackCaret}
          onPaste={onPaste}
          onBlur={() => setDismissed(menuKey)}
          onScroll={() => { syncScroll(); if (menuOpen) placeMenu() }}
        />
        {menuOpen && anchor && trigger && (
          <CaretMenu
            items={menuItems}
            active={active}
            anchor={anchor.rect}
            bounds={{ w: anchor.w, h: anchor.h }}
            label={trigger.kind === 'slash' ? 'Commands' : 'Link to a doc'}
            onPick={pick}
            onHover={(i) => setAct({ key: queryKey, i })}
          />
        )}
      </div>
      <div className="md-status">
        <span>Ln {caret.line}, Col {caret.col}</span>
        <span>{lineCount} lines</span>
        <span>{wordCount(value)} words</span>
        {richStatus && readingTime(wordCount(value)) && <span>{readingTime(wordCount(value))}</span>}
        {richStatus && sel.end > sel.start && (
          <span className="md-sel">
            {wordCount(value.slice(sel.start, sel.end))} words, {sel.end - sel.start} chars selected
          </span>
        )}
        <span className="md-hints">⌘B bold · ⇧⌘I italic · ⌘K link · ⇧⌘M maths · ⇧⌘E code · Tab indent</span>
      </div>
    </div>
  )
})

export default MarkdownEditor

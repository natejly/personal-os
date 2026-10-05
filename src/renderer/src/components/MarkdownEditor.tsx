import { forwardRef, memo, useCallback, useEffect, useImperativeHandle, useLayoutEffect, useMemo, useRef, useState } from 'react'
import CaretMenu from '../features/notes/CaretMenu'
import { measureCaret, type CaretRect } from '../features/notes/caretPosition'
import type { MarkdownEditorHandle } from '../features/notes/handle'
import { linkFromPaste, pickImage, withTitle } from '../features/notes/smartPaste'
import { linkTitle, uploadDocAsset } from '../features/notes/api'
import { builtinCommands, detectSlash, filterCommands, type SlashCommand } from '../features/notes/slash'
import { wrapToggle } from '../features/notes/format'
import { readingTime, wordCount } from '../features/notes/stats'
import { diffRange, insertWithoutFocus, replaceInTextarea } from '../features/notes/textEdit'
import { TAG_BODY } from '../features/notes/tags'
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
  /** With smartPaste: pasting or dropping an image stores it under this doc and inserts `![](url)`. */
  imageDocId?: string
  /** Called with the caret's 1-based line whenever it changes (drives the outline). */
  onCaretLine?: (line: number) => void
  /** Opt in: reading time and the size of the selection in the status bar. */
  richStatus?: boolean
  /** In-flight dictation words, drawn in a pill at the caret. Display only: never part of `value`. */
  previewText?: string
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
  return highlightedLines(src, wikilinks, activeLine).join('\n')
}

function highlightedLines(src: string, wikilinks: boolean, activeLine?: number): string[] {
  const out = highlightLines(src, wikilinks)
  if (activeLine === undefined) return out
  const [a, b] = paragraphRange(src.split('\n'), activeLine - 1)
  return out.map((h, i) => (i < a || i > b ? `<span class="dim">${h}</span>` : h))
}

const MIRROR_CHUNK_LINES = 64

/**
 * The mirror's html in runs of lines, each line ending in its own newline. Painted as one element per
 * run, a keystroke in a 250 KB doc re-parses and re-lays-out one run instead of the whole mirror
 * (about a third of the cost); runs that did not change keep their DOM.
 */
export function highlightChunks(src: string, wikilinks = false, activeLine?: number): string[] {
  const lines = highlightedLines(src, wikilinks, activeLine)
  const out: string[] = []
  for (let i = 0; i < lines.length; i += MIRROR_CHUNK_LINES) out.push(lines.slice(i, i + MIRROR_CHUNK_LINES).join('\n') + '\n')
  return out
}

const MirrorChunk = memo(function MirrorChunk({ html }: { html: string }): JSX.Element {
  return <span dangerouslySetInnerHTML={{ __html: html }} />
})

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
    out.push(inlineCached(line, wikilinks))
  }
  return out
}

const inlineMemo = new Map<string, string>()

/** A keystroke changes one line of a long doc; the other thousands come back from here instead of being re-tokenised. */
function inlineCached(line: string, wiki: boolean): string {
  if (line.length > 2000) return inline(line, wiki)
  const key = (wiki ? 'w' : 'p') + line
  let html = inlineMemo.get(key)
  if (html === undefined) {
    if (inlineMemo.size > 30_000) inlineMemo.clear()
    html = inline(line, wiki)
    inlineMemo.set(key, html)
  }
  return html
}

function inline(line: string, wiki: boolean): string {
  // Block-level prefixes first — they colour the whole line.
  const heading = /^(\s{0,3}#{1,6}\s)(.*)$/.exec(line)
  if (heading) return `<span class="tk-head">${esc(heading[1])}${span(heading[2], wiki, false)}</span>`
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

const spanPatterns = new Map<string, RegExp>()

/** Built once per option set: compiling this alternation per line made a 250 KB doc cost ~100 ms per keystroke. */
function spanPattern(wiki: boolean, tags: boolean): RegExp {
  const key = `${wiki}${tags}`
  let re = spanPatterns.get(key)
  if (re) return re
  re = new RegExp(
    [
      '(?<m1>\\$\\$[^$]+\\$\\$)', // block maths on one line
      '(?<m2>\\$(?:\\\\.|[^$\\\\\\n])+\\$)', // inline maths
      '(?<code>`[^`\\n]+`)', // code
      // Only when the host opted in, and before the plain link so `[[a]](b)` is not misread.
      ...(wiki ? ['(?<wiki>\\[\\[[^\\[\\]\\n|]+(?:\\|[^\\[\\]\\n]+)?\\]\\])'] : []), // wikilink
      '(?<link>!?\\[[^\\]\\n]*\\]\\([^)\\n]*\\))', // link / image
      '(?<strong>\\*\\*[^*\\n]+\\*\\*|__[^_\\n]+__)', // strong
      '(?<em>\\*[^*\\n]+\\*|_[^_\\n]+_)', // emphasis
      '(?<strike>~~[^~\\n]+~~)', // strike
      ...(tags ? [`(?<tag>(?<=^|\\s)#${TAG_BODY})`] : []) // #tag (docs only)
    ].join('|'),
    'gu'
  )
  spanPatterns.set(key, re)
  return re
}

/** Inline spans: maths, code, links, emphasis. One pass, longest-delimiter-first. */
function span(text: string, wiki = false, tags = wiki): string {
  const pattern = spanPattern(wiki, tags)
  pattern.lastIndex = 0
  let out = ''
  let last = 0
  for (let m = pattern.exec(text); m; m = pattern.exec(text)) {
    out += esc(text.slice(last, m.index))
    // Named groups, so adding one never shifts the others.
    const g = m.groups ?? {}
    const cls = g.m1 || g.m2 ? 'tk-math' : g.code ? 'tk-code' : g.wiki ? 'tk-wikilink' : g.link ? 'tk-link' : g.strong ? 'tk-strong' : g.em ? 'tk-em' : g.tag ? 'tk-tag' : 'tk-strike'
    out += `<span class="${cls}">${esc(m[0])}</span>`
    last = m.index + m[0].length
  }
  return out + esc(text.slice(last))
}

function wrapSelection(el: HTMLTextAreaElement, left: string, right = left): { value: string; start: number; end: number } {
  const { text, start, end } = wrapToggle(el.value, el.selectionStart, el.selectionEnd, left, right)
  return { value: text, start, end }
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

/** One row per line, re-rendered only when the line count or the caret's line changes, never per caret column. */
const Gutter = memo(forwardRef<HTMLDivElement, { lineCount: number; cur: number }>(function Gutter({ lineCount, cur }, ref) {
  return (
    <div className="md-gutter" ref={ref} aria-hidden>
      {Array.from({ length: lineCount }, (_, i) => (
        <div key={i} className={i + 1 === cur ? 'cur' : undefined}>{i + 1}</div>
      ))}
    </div>
  )
}))

const MarkdownEditor = forwardRef<MarkdownEditorHandle, EditorHandleProps>(function MarkdownEditor({
  value, onChange, onSave, placeholder, readOnly = false, wrap = true, onScrollFraction,
  slash = false, extraCommands, linkTargets, smartPaste = false, imageDocId, onCaretLine, richStatus = false, previewText = '', typewriter = false, focusMode = false
}, ref): JSX.Element {
  const ta = useRef<HTMLTextAreaElement>(null)
  const mirror = useRef<HTMLPreElement>(null)
  const surface = useRef<HTMLDivElement>(null)
  const gutter = useRef<HTMLDivElement>(null)
  const [caret, setCaret] = useState({ line: 1, col: 1 })
  const [sel, setSel] = useState({ start: 0, end: 0 })
  const lineCount = useMemo(() => value.split('\n').length, [value])
  // Counted once per value, not once per render: a selection drag renders the status bar hundreds of times.
  const words = useMemo(() => wordCount(value), [value])
  const selWords = useMemo(() => (richStatus && sel.end > sel.start ? wordCount(value.slice(sel.start, sel.end)) : 0), [richStatus, value, sel.start, sel.end])
  const wikiOn = !!linkTargets
  // Keyed on the paragraph span, not the caret line, so moving within a paragraph does not re-highlight.
  const para = useMemo(() => (focusMode ? paragraphRange(value.split('\n'), caret.line - 1).join(':') : ''), [focusMode, value, caret.line])
  const chunks = useMemo(() => {
    if (!focusMode) return highlightChunks(value, wikiOn)
    return highlightChunks(value, wikiOn, Number(para.split(':')[0]) + 1)
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
  // `select` fires on every pointer move of a drag, so this must cost nothing when nothing moved: no
  // slice or split of the document, and no new state object unless a number actually changed.
  const trackCaret = useCallback((): void => {
    const el = ta.current
    if (!el) return
    const start = el.selectionStart
    const end = el.selectionEnd
    setSel((s) => (s.start === start && s.end === end ? s : { start, end }))
    const v = el.value
    let line = 1
    let nl = -1
    for (let i = v.indexOf('\n'); i !== -1 && i < start; i = v.indexOf('\n', i + 1)) { line++; nl = i }
    const col = start - nl
    setCaret((c) => (c.line === line && c.col === col ? c : { line, col }))
  }, [])

  // A value swapped in from outside (another doc opened) moves the real caret; re-read it so the
  // status bar, gutter and outline do not keep the last doc's line.
  const emitted = useRef(value)
  useLayoutEffect(() => {
    if (value !== emitted.current) trackCaret()
    emitted.current = value
  }, [value, trackCaret])

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

  // The in-flight dictation pill: hangs below the caret, drawn here and never written into `value`.
  const [pill, setPill] = useState<{ top: number; left: number } | null>(null)
  useLayoutEffect(() => {
    const sr = surface.current?.getBoundingClientRect()
    const r = previewText && mirror.current && sr ? measureCaret(mirror.current, sel.end) : null
    setPill(r && sr ? { top: r.top - sr.top + r.height + 4, left: Math.max(0, Math.min(r.left - sr.left, sr.width - 120)) } : null)
  }, [previewText, sel.end, value, wrap])

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
    replaceRange: (start, end, text, selStart, selEnd) => {
      const el = ta.current
      if (!el || el.readOnly) return
      replaceInTextarea(el, start, end, text, selStart, selEnd)
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

  const [notice, setNotice] = useState('')
  useEffect(() => { if (notice) { const t = setTimeout(() => setNotice(''), 4000); return () => clearTimeout(t) } }, [notice])

  /** Upload the first image in `files` and put `![](url)` at the caret. False when there was no image to take. */
  const takeImage = (files: FileList | null): boolean => {
    const pick = pickImage(files)
    const el = ta.current
    if (!pick || !el || !imageDocId) return false
    if (!pick.ok) { setNotice(pick.reason); return true }
    setNotice('Adding image...')
    uploadDocAsset(imageDocId, files![pick.index]).then(({ url }) => {
      const e = ta.current
      if (!e) return
      replaceInTextarea(e, e.selectionStart, e.selectionEnd, `![](${url})`)
      trackCaret()
      setNotice('')
    }).catch((err) => setNotice(`Could not add the image: ${err instanceof Error ? err.message : err}`))
    return true
  }

  const onPaste = (e: React.ClipboardEvent<HTMLTextAreaElement>): void => {
    if (!smartPaste || readOnly) return
    if (takeImage(e.clipboardData.files)) { e.preventDefault(); return }
    const el = e.currentTarget
    const sel = el.value.slice(el.selectionStart, el.selectionEnd)
    const url = e.clipboardData.getData('text/plain').trim()
    const link = linkFromPaste(sel, url)
    if (!link) return
    e.preventDefault()
    const at = el.selectionStart
    replaceInTextarea(el, at, el.selectionEnd, link)
    trackCaret()
    // Bare URL: the link is already in the text; the title arrives later and never blocks the paste.
    if (!sel.trim()) {
      linkTitle(url).then(({ title }) => {
        const cur = ta.current
        const swap = cur && title ? withTitle(cur.value, at, url, title) : null
        if (cur && swap) {
          const caret = cur.selectionStart
          replaceInTextarea(cur, swap.start, swap.end, swap.text, caret >= swap.end ? caret + swap.text.length - (swap.end - swap.start) : caret)
        }
      }).catch(() => undefined)
    }
  }

  const onDrop = (e: React.DragEvent<HTMLTextAreaElement>): void => {
    if (!smartPaste || readOnly || !e.dataTransfer.files.length) return
    if (takeImage(e.dataTransfer.files)) e.preventDefault()
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
    // Every chord here avoids the app menu's accelerators (src/main/index.ts): a menu accelerator never
    // reaches the page. So ⇧⌘B (⌘B toggles the sidebar), ⇧⌘I (⌘I asks about the page; ⌥⌘I is dev
    // tools) and ⌃⌘M (⇧⌘M opens Meetings).
    if (mod && e.shiftKey && e.key.toLowerCase() === 'b') {
      e.preventDefault()
      return apply(wrapSelection(el, '**'))
    }
    if (mod && e.shiftKey && e.key.toLowerCase() === 'i') {
      e.preventDefault()
      return apply(wrapSelection(el, '*'))
    }
    if (mod && e.shiftKey && e.key.toLowerCase() === 'e') {
      e.preventDefault()
      return apply(wrapSelection(el, '`'))
    }
    if (e.metaKey && e.ctrlKey && e.key.toLowerCase() === 'm') {
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
      // A caret in a list item indents the item (nesting it), like a selection does; elsewhere Tab types two spaces.
      const inList = s === en && LIST_ITEM.test(v.slice(v.lastIndexOf('\n', s - 1) + 1, v.indexOf('\n', s) === -1 ? v.length : v.indexOf('\n', s)))
      if (s !== en || e.shiftKey || inList) return apply(shiftLines(v, s, en, e.shiftKey))
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
        // At the top level leave a blank line, or the next paragraph renders as part of the last item.
        const gap = indent ? '' : '\n'
        return apply({ value: v.slice(0, from) + gap + v.slice(s), start: from + gap.length, end: from + gap.length })
      }
      e.preventDefault()
      const nextMarker = num ? `${Number(num) + 1}${marker.slice(String(num).length)}` : marker
      const insert = `\n${indent}${nextMarker}${gap}${task ? task.replace(/[xX]/, ' ') : ''}`
      return apply({ value: v.slice(0, s) + insert + v.slice(en), start: s + insert.length, end: s + insert.length })
    }
  }

  return (
    <div className={`md-editor ${wrap ? '' : 'nowrap'} ${focusMode ? 'focus' : ''}`}>
      <Gutter ref={gutter} lineCount={lineCount} cur={caret.line} />
      <div className="md-surface" ref={surface}>
        <pre className="md-mirror" ref={mirror} aria-hidden>{chunks.map((h, i) => <MirrorChunk key={i} html={h} />)}</pre>
        <textarea
          ref={ta}
          className="md-input"
          value={value}
          placeholder={placeholder}
          readOnly={readOnly}
          spellCheck
          wrap={wrap ? 'soft' : 'off'}
          onChange={(e) => { clicked.current = false; emitted.current = e.target.value; onChange(e.target.value); trackCaret() }}
          onMouseDown={() => { clicked.current = true }}
          onKeyDown={(e) => { clicked.current = false; onKeyDown(e) }}
          onKeyUp={trackCaret}
          onClick={trackCaret}
          onSelect={trackCaret}
          onPaste={onPaste}
          onDrop={onDrop}
          onBlur={() => setDismissed(menuKey)}
          onScroll={() => { syncScroll(); if (menuOpen) placeMenu() }}
        />
        {pill && <div className="caret-pill" role="status" aria-live="off" style={pill}>{previewText}</div>}
        {menuOpen && anchor && trigger && (
          <CaretMenu
            items={menuItems}
            active={active}
            anchor={anchor.rect}
            bounds={{ w: anchor.w, h: anchor.h }}
            label={trigger.kind === 'slash' ? 'Commands' : 'Link to a file'}
            onPick={pick}
            onHover={(i) => setAct({ key: queryKey, i })}
          />
        )}
      </div>
      <div className="md-status">
        <span>Ln {caret.line}, Col {caret.col}</span>
        <span>{lineCount} lines</span>
        <span>{words} words</span>
        {richStatus && readingTime(words) && <span>{readingTime(words)}</span>}
        {richStatus && sel.end > sel.start && (
          <span className="md-sel">
            {selWords} words, {sel.end - sel.start} chars selected
          </span>
        )}
        {notice && <span className="md-sel">{notice}</span>}
        <span className="md-hints">⇧⌘B bold · ⇧⌘I italic · ⌘K link · ⌃⌘M maths · ⇧⌘E code · Tab indent</span>
      </div>
    </div>
  )
})

export default MarkdownEditor

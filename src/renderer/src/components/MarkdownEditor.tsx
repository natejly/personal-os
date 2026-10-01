import { useCallback, useLayoutEffect, useMemo, useRef, useState } from 'react'

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
}

const esc = (s: string): string => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')

/**
 * A deliberately small markdown tokenizer for the highlight layer. It is line-oriented, which is what
 * keeps it honest against the textarea: every input line produces exactly one output line, so the two
 * layers can never drift. Fenced code and maths blocks are tracked as state across lines.
 */
function highlight(src: string): string {
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
    out.push(inline(line))
  }
  return out.join('\n')
}

function inline(line: string): string {
  // Block-level prefixes first — they colour the whole line.
  const heading = /^(\s{0,3}#{1,6}\s)(.*)$/.exec(line)
  if (heading) return `<span class="tk-head">${esc(heading[1])}${span(heading[2])}</span>`
  const quote = /^(\s*>+\s?)(.*)$/.exec(line)
  if (quote) return `<span class="tk-punct">${esc(quote[1])}</span><span class="tk-quote">${span(quote[2])}</span>`
  const rule = /^\s*([-*_])(\s*\1){2,}\s*$/.test(line)
  if (rule) return `<span class="tk-punct">${esc(line)}</span>`
  const list = /^(\s*(?:[-*+]|\d+[.)])\s+(?:\[[ xX]\]\s+)?)(.*)$/.exec(line)
  if (list) return `<span class="tk-bullet">${esc(list[1])}</span>${span(list[2])}`
  const table = /^\s*\|/.test(line)
  if (table) return `<span class="tk-table">${esc(line)}</span>`
  return span(line)
}

/** Inline spans: maths, code, links, emphasis. One pass, longest-delimiter-first. */
function span(text: string): string {
  const pattern = new RegExp(
    [
      '(\\$\\$[^$]+\\$\\$)', // block maths on one line
      '(\\$(?:\\\\.|[^$\\\\\\n])+\\$)', // inline maths
      '(`[^`\\n]+`)', // code
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
    const cls = m[1] || m[2] ? 'tk-math' : m[3] ? 'tk-code' : m[4] ? 'tk-link' : m[5] ? 'tk-strong' : m[6] ? 'tk-em' : 'tk-strike'
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

export default function MarkdownEditor({
  value, onChange, onSave, placeholder, readOnly = false, wrap = true, onScrollFraction
}: EditorHandleProps): JSX.Element {
  const ta = useRef<HTMLTextAreaElement>(null)
  const mirror = useRef<HTMLPreElement>(null)
  const gutter = useRef<HTMLDivElement>(null)
  const [caret, setCaret] = useState({ line: 1, col: 1 })
  const lineCount = useMemo(() => value.split('\n').length, [value])
  const html = useMemo(() => highlight(value) + '\n', [value])

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

  const trackCaret = useCallback((): void => {
    const el = ta.current
    if (!el) return
    const upto = el.value.slice(0, el.selectionStart)
    const nl = upto.lastIndexOf('\n')
    setCaret({ line: upto.split('\n').length, col: el.selectionStart - nl })
  }, [])

  /** Apply a computed edit through setRangeText, which keeps it on the textarea's native undo stack. */
  const apply = useCallback((next: { value: string; start: number; end: number }): void => {
    const el = ta.current
    if (!el) return
    el.setRangeText(next.value, 0, el.value.length, 'preserve')
    el.setSelectionRange(next.start, next.end)
    onChange(el.value)
    trackCaret()
  }, [onChange, trackCaret])

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>): void => {
    const el = e.currentTarget
    const mod = e.metaKey || e.ctrlKey
    if (mod && e.key.toLowerCase() === 's') {
      e.preventDefault()
      onSave?.()
      return
    }
    if (readOnly) return
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
    <div className={`md-editor ${wrap ? '' : 'nowrap'}`}>
      <div className="md-gutter" ref={gutter} aria-hidden>
        {Array.from({ length: lineCount }, (_, i) => (
          <div key={i} className={i + 1 === caret.line ? 'cur' : undefined}>{i + 1}</div>
        ))}
      </div>
      <div className="md-surface">
        <pre className="md-mirror" ref={mirror} aria-hidden dangerouslySetInnerHTML={{ __html: html }} />
        <textarea
          ref={ta}
          className="md-input"
          value={value}
          placeholder={placeholder}
          readOnly={readOnly}
          spellCheck
          wrap={wrap ? 'soft' : 'off'}
          onChange={(e) => { onChange(e.target.value); trackCaret() }}
          onKeyDown={onKeyDown}
          onKeyUp={trackCaret}
          onClick={trackCaret}
          onSelect={trackCaret}
          onScroll={syncScroll}
        />
      </div>
      <div className="md-status">
        <span>Ln {caret.line}, Col {caret.col}</span>
        <span>{lineCount} lines</span>
        <span>{value.trim() ? value.trim().split(/\s+/).length : 0} words</span>
        <span className="md-hints">⌘B bold · ⇧⌘I italic · ⌘K link · ⇧⌘M maths · ⇧⌘E code · Tab indent</span>
      </div>
    </div>
  )
}

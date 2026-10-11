import { useEffect, useRef, useState } from 'react'
import { api } from '../lib/api'
import { suggestionGhost, tabSuggestion } from '../lib/composerSuggest'

/**
 * A textarea with AI ghost-text completion: pause typing and a faint continuation appears,
 * Tab accepts it, Esc (or any edit) dismisses it. The ghost lives in a mirror div with the
 * same metrics as the textarea, so `sharedStyle` must carry anything that affects text layout.
 * Suggestions only fire with the cursor at the end of the text; mid-text ghosts would shift
 * everything after the cursor.
 */
interface Props {
  value: string
  onChange: (v: string) => void
  /** What is being written ('mail', 'note', …); steers the backend prompt. */
  kind: string
  /** Extra context for the model, e.g. the subject line or the message being replied to. */
  context?: string
  /** 'field' is the boxed input look; 'bare' inherits colors, for skinned hosts like notes. */
  variant?: 'field' | 'bare'
  /** Layout-affecting styles applied to both the textarea and its mirror. */
  sharedStyle?: React.CSSProperties
  placeholder?: string
  /** The field's accessible name; defaults to the placeholder. */
  ariaLabel?: string
  autoFocus?: boolean
  rows?: number
  className?: string
  /** Skip the request until the draft is at least this long. Todos fire earlier than mail. */
  minChars?: number
  /** Grow with the text, capped at `maxHeight`. Used by the chat composer. */
  autoGrow?: boolean
  maxHeight?: number
  onBlur?: () => void
  onKeyDown?: (e: React.KeyboardEvent<HTMLTextAreaElement>) => void
  onPaste?: (e: React.ClipboardEvent<HTMLTextAreaElement>) => void
  /** No ghost text while true: the composer's slash menu owns Tab and Esc then. */
  noGhost?: boolean
  /**
   * Likely next texts, shown as ghost text while the box is empty (no request is made). Tab fills the
   * first; Tab again while the text is still that suggestion steps to the next. Esc dismisses them.
   */
  suggestions?: string[]
}

const NO_SUGGESTIONS: string[] = []

const DEBOUNCE_MS = 600
const MIN_CHARS = 15

export default function SmartTextarea({
  value, onChange, kind, context = '', variant = 'field', sharedStyle, placeholder, ariaLabel, autoFocus, onBlur, onKeyDown, onPaste,
  rows = 2, className = '', minChars = MIN_CHARS, autoGrow = false, maxHeight = 240, noGhost = false, suggestions = NO_SUGGESTIONS
}: Props): JSX.Element {
  const [ghost, setGhost] = useState('')
  const taRef = useRef<HTMLTextAreaElement>(null)
  const mirrorRef = useRef<HTMLDivElement>(null)
  const seq = useRef(0)
  const [idx, setIdx] = useState(0)
  const [dismissed, setDismissed] = useState(false)
  // The suggestion Tab last put in the box, while the text still equals it: Tab again steps to the next one.
  const accepted = useRef<string | null>(null)
  const suggestKey = suggestions.join('\u0000')
  useEffect(() => { setIdx(0); setDismissed(false); accepted.current = null }, [suggestKey])
  const suggestGhost = noGhost ? '' : suggestionGhost(value, suggestions, idx, dismissed)
  const shown = suggestGhost || ghost
  // After Tab accepts a suggestion the hint stays, so it is clear Tab steps through the rest.
  const cycling = !noGhost && suggestions.length > 1 && !!value && value === suggestions[idx % suggestions.length] && accepted.current === value

  useEffect(() => {
    setGhost('')
    const mine = ++seq.current
    if (accepted.current !== null && value !== accepted.current) accepted.current = null
    if (noGhost || value.trim().length < minChars || value === accepted.current) return
    const ctl = new AbortController()
    const t = setTimeout(() => {
      const ta = taRef.current
      if (!ta || document.activeElement !== ta || ta.selectionStart !== value.length || ta.selectionEnd !== value.length) return
      api.assist
        .complete({ kind, before: value.slice(-4000), context }, ctl.signal)
        .then(({ completion }) => { if (seq.current === mine && completion) setGhost(completion) })
        .catch(() => undefined) // ghost text is a nicety; never surface its errors
    }, DEBOUNCE_MS)
    return (): void => { clearTimeout(t); ctl.abort() }
  }, [value, kind, context, minChars, noGhost])

  useEffect(() => {
    if (!autoGrow) return
    const el = taRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, maxHeight)}px`
  }, [value, shown, autoGrow, maxHeight])

  // The mirror must track the textarea's scroll or the ghost drifts on long texts.
  const syncScroll = (): void => {
    if (mirrorRef.current && taRef.current) mirrorRef.current.scrollTop = taRef.current.scrollTop
  }
  useEffect(syncScroll, [shown, value])

  const keyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>): void => {
    if (e.key === 'Tab' && !e.shiftKey && !noGhost && !e.nativeEvent.isComposing) {
      const next = tabSuggestion(value, suggestions, idx, accepted.current)
      if (next) {
        e.preventDefault()
        const ta = e.currentTarget
        accepted.current = next.text
        setIdx(next.idx)
        onChange(next.text)
        requestAnimationFrame(() => ta.setSelectionRange(next.text.length, next.text.length))
        return
      }
    }
    if (suggestGhost && e.key === 'Escape') {
      e.preventDefault()
      setDismissed(true)
      return
    }
    if (ghost && e.key === 'Tab' && !e.shiftKey) {
      e.preventDefault()
      onChange(value + ghost)
      setGhost('')
      return
    }
    if (ghost && e.key === 'Escape') {
      e.preventDefault()
      setGhost('')
      return
    }
    // An arrow drops the ghost but still reaches the host (the composer's prompt recall).
    if (ghost && (e.key === 'ArrowUp' || e.key === 'ArrowDown')) setGhost('')
    onKeyDown?.(e)
  }

  return (
    <div className={`smart-ta ${variant}${className ? ` ${className}` : ''}`}>
      <div ref={mirrorRef} className="smart-ta-mirror" style={sharedStyle} aria-hidden>
        <span className="t">{value}</span>
        <span className="g">{shown}</span>
      </div>
      <textarea
        ref={taRef}
        rows={rows}
        value={value}
        placeholder={suggestGhost ? undefined : placeholder}
        aria-label={ariaLabel ?? placeholder}
        autoFocus={autoFocus}
        style={sharedStyle}
        onChange={(e) => { setGhost(''); onChange(e.target.value) }}
        onScroll={syncScroll}
        onBlur={() => { setGhost(''); onBlur?.() }}
        onKeyDown={keyDown}
        onPaste={onPaste}
      />
      {(shown || cycling) && <span className="smart-ta-hint">tab{(suggestGhost || cycling) && suggestions.length > 1 ? ` ${idx % suggestions.length + 1}/${suggestions.length}` : ''}</span>}
    </div>
  )
}

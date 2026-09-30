import { useEffect, useRef, useState } from 'react'
import { api } from '../lib/api'

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
  autoFocus?: boolean
  onBlur?: () => void
  onKeyDown?: (e: React.KeyboardEvent<HTMLTextAreaElement>) => void
}

const DEBOUNCE_MS = 600
const MIN_CHARS = 15

export default function SmartTextarea({ value, onChange, kind, context = '', variant = 'field', sharedStyle, placeholder, autoFocus, onBlur, onKeyDown }: Props): JSX.Element {
  const [ghost, setGhost] = useState('')
  const taRef = useRef<HTMLTextAreaElement>(null)
  const mirrorRef = useRef<HTMLDivElement>(null)
  const seq = useRef(0)

  useEffect(() => {
    setGhost('')
    const mine = ++seq.current
    if (value.trim().length < MIN_CHARS) return
    const t = setTimeout(() => {
      const ta = taRef.current
      if (!ta || document.activeElement !== ta || ta.selectionStart !== value.length || ta.selectionEnd !== value.length) return
      api.assist
        .complete({ kind, before: value, context })
        .then(({ completion }) => { if (seq.current === mine && completion) setGhost(completion) })
        .catch(() => undefined) // ghost text is a nicety; never surface its errors
    }, DEBOUNCE_MS)
    return (): void => clearTimeout(t)
  }, [value, kind, context])

  // The mirror must track the textarea's scroll or the ghost drifts on long texts.
  const syncScroll = (): void => {
    if (mirrorRef.current && taRef.current) mirrorRef.current.scrollTop = taRef.current.scrollTop
  }
  useEffect(syncScroll, [ghost, value])

  const keyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>): void => {
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
    onKeyDown?.(e)
  }

  return (
    <div className={`smart-ta ${variant}`}>
      <div ref={mirrorRef} className="smart-ta-mirror" style={sharedStyle} aria-hidden>
        <span className="t">{value}</span>
        <span className="g">{ghost}</span>
      </div>
      <textarea
        ref={taRef}
        value={value}
        placeholder={placeholder}
        autoFocus={autoFocus}
        style={sharedStyle}
        onChange={(e) => { setGhost(''); onChange(e.target.value) }}
        onScroll={syncScroll}
        onBlur={() => { setGhost(''); onBlur?.() }}
        onKeyDown={keyDown}
      />
      {ghost && <span className="smart-ta-hint">tab</span>}
    </div>
  )
}

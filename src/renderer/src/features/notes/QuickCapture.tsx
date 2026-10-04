import { useEffect, useRef, useState } from 'react'
import { setBase } from '../../lib/api'
import { appendDaily } from './api'

/** The quick-capture window: Enter appends a bullet to today's note and closes, Esc just closes. */
export default function QuickCapture(): JSX.Element {
  const [text, setText] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const ready = useRef<Promise<void> | null>(null)

  useEffect(() => {
    ready.current = window.os.backendStatus().then((s) => { if (s.url) setBase(s.url); else throw new Error(s.error || 'Backend not running') })
    ready.current.catch((e: Error) => setError(e.message))
  }, [])

  const submit = async (): Promise<void> => {
    const t = text.trim()
    if (!t) return window.os.closeSelf()
    setBusy(true)
    try {
      await ready.current
      await appendDaily(t)
      window.os.closeSelf()
    } catch (e) {
      setError((e as Error).message)
      setBusy(false)
    }
  }

  return (
    <div className="quick-capture" style={{ padding: 12, height: '100vh', boxSizing: 'border-box', display: 'flex', flexDirection: 'column', gap: 6 }}>
      <textarea
        autoFocus
        value={text}
        disabled={busy}
        placeholder="Add to today's file. Enter to save, Esc to close."
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Escape') window.os.closeSelf()
          else if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); void submit() }
        }}
        style={{ flex: 1, resize: 'none', font: 'inherit' }}
      />
      {error && <small role="alert" style={{ color: 'var(--danger, #e5484d)' }}>{error}</small>}
    </div>
  )
}

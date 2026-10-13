import { useState } from 'react'
import { X } from '../icons'
import { useStore } from '../store'

const TEXT = 'Memory search is using keywords only. Add an OpenAI or Fireworks key, or pick an embedding model, for meaning-based search.'
const KEY = 'grain.embeddingHintDismissed'

/** One informational line for Settings → Memory while search has no embedding model. */
export default function EmbeddingHint(): JSX.Element | null {
  const keywordOnly = useStore((s) => s.settings.embeddingKeywordOnly)
  return keywordOnly ? <p className="muted small">{TEXT}</p> : null
}

/** The same line as a dismissible app notice, with a way into Settings → Model. The dismissal is remembered. */
export function EmbeddingNotice(): JSX.Element | null {
  const keywordOnly = useStore((s) => s.settings.embeddingKeywordOnly)
  const openSettings = useStore((s) => s.openSettings)
  const [gone, setGone] = useState(() => { try { return localStorage.getItem(KEY) === '1' } catch { return false } })
  if (!keywordOnly || gone) return null
  const dismiss = (): void => { setGone(true); try { localStorage.setItem(KEY, '1') } catch { /* private window: gone for this session */ } }
  return (
    <div className="backend-banner" role="status">
      <span>{TEXT}</span>
      <button type="button" className="link" onClick={() => openSettings('model')}>Open Models settings</button>
      <button type="button" className="toast-close" aria-label="Dismiss" onClick={dismiss}><X size={12} /></button>
    </div>
  )
}

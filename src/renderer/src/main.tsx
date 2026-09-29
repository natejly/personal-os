import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import PopoutSurface from './PopoutSurface'
import { useCanvas } from './canvas/store'
import './styles.css'
import 'highlight.js/styles/github-dark-dimmed.css'

/**
 * Dev serves the query off `${ELECTRON_RENDERER_URL}/?…`, packaged off `loadFile(…, { query })` on a
 * `file://` url. Both land in `location.search`; the href fallback covers one that arrived behind a hash.
 */
const params = (): URLSearchParams => {
  if (window.location.search) return new URLSearchParams(window.location.search)
  const q = window.location.href.indexOf('?')
  return new URLSearchParams(q === -1 ? '' : window.location.href.slice(q + 1))
}

const q = params()
const windowId = q.get('surface') === 'widget' ? q.get('window') : null

/**
 * ⌘W/⌘M are plain menu items so the canvas can claim them, which leaves classic mode with nobody
 * listening until `useCanvas.load()` runs. `loaded` is the handover: once the canvas store is live it
 * owns both actions in either mode, including its own fall-through to closeSelf/minimizeSelf.
 * A pop-out answers for itself in `PopoutSurface`.
 */
if (!windowId) {
  window.os.onMenu((action) => {
    if (useCanvas.getState().loaded) return
    if (action === 'close-window') window.os.closeSelf()
    else if (action === 'minimize-window') window.os.minimizeSelf()
  })
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>{windowId ? <PopoutSurface windowId={windowId} /> : <App />}</React.StrictMode>
)

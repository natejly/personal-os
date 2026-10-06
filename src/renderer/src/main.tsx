import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import PopoutSurface from './PopoutSurface'
import { useCanvas } from './canvas/store'
import QuickAsk from './features/ask/QuickAsk'
import QuickCapture from './features/notes/QuickCapture'
import PrintSurface from './features/notes/PrintSurface'
import RootBoundary from './RootBoundary'
import { installFileDropGuard } from './lib/fileDrop'
import { useStore } from './store'
import { clampZoom } from './lib/zoom'
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

// Before anything mounts, in every window: a file dropped off-target must never load in place of the app.
installFileDropGuard(window)

// Every surface applies the saved zoom as its own page zoom factor once settings load and whenever they change.
useStore.subscribe((s, prev) => {
  if (s.settings.uiZoom !== prev.settings.uiZoom && s.settings.uiZoom !== undefined) void window.os.setZoom(clampZoom(s.settings.uiZoom))
})

const q = params()
const capture = q.get('surface') === 'capture'
const ask = q.get('surface') === 'ask'
const print = q.get('surface') === 'print'
const windowId = q.get('surface') === 'widget' ? q.get('window') : null

/**
 * ⌘W/⌘M are plain menu items so the canvas can claim them, which leaves classic mode with nobody
 * listening until `useCanvas.load()` runs. `loaded` is the handover: once the canvas store is live it
 * owns both actions in either mode, including its own fall-through to closeSelf/minimizeSelf.
 * A pop-out answers for itself in `PopoutSurface`.
 */
if (!windowId && !capture && !ask && !print) {
  window.os.onMenu((action) => {
    if (useCanvas.getState().loaded) return
    if (action === 'close-window') window.os.closeSelf()
    else if (action === 'minimize-window') window.os.minimizeSelf()
  })
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <RootBoundary>{print ? <PrintSurface /> : capture ? <QuickCapture /> : ask ? <QuickAsk /> : windowId ? <PopoutSurface windowId={windowId} /> : <App />}</RootBoundary>
  </React.StrictMode>
)

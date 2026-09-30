import { useCallback, useEffect, useState } from 'react'
import { Pin, PinOff, X } from 'lucide-react'
import type { CanvasWindow } from '@shared/types'
import { api } from './lib/api'
import { useStore } from './store'
import { WIDGETS } from './canvas/registry'
import StatusRing from './canvas/StatusRing'
// The `.popout*` rules live in canvas.css, which only Canvas.tsx would otherwise pull in.
import './styles/canvas.css'

/**
 * The renderer of one detached widget. It knows nothing but its canvas window id and resolves the rest
 * from `GET /windows/{id}` and the registry: no sidebar, no plane, no spaces bar, no modal.
 */
export default function PopoutSurface({ windowId }: { windowId: string }): JSX.Element {
  const [win, setWin] = useState<CanvasWindow | null>(null)
  const [error, setError] = useState('')
  const [focused, setFocused] = useState(() => document.hasFocus())
  const theme = useStore((s) => s.settings.theme)

  const reload = useCallback(async (): Promise<void> => {
    try {
      setWin(await api.windows.get(windowId))
      setError('')
    } catch (e) {
      setWin(null)
      setError((e as Error).message)
    }
  }, [windowId])

  // Widgets read the app store (sessions, todos, models, settings), so a pop-out boots it as well, at
  // the same widest scope canvas mode uses. `init` is also what sets the api base url.
  useEffect(() => {
    const boot = async (): Promise<void> => {
      await useStore.getState().init()
      const s = useStore.getState()
      if (s.backendError) return setError(s.backendError)
      void s.refreshTodos('all', true)
      await reload()
    }
    void boot()
  }, [reload])

  useEffect(() => {
    document.documentElement.dataset.theme = theme
  }, [theme])

  useEffect(() => {
    const on = (): void => setFocused(true)
    const off = (): void => setFocused(false)
    window.addEventListener('focus', on)
    window.addEventListener('blur', off)
    return () => {
      window.removeEventListener('focus', on)
      window.removeEventListener('blur', off)
    }
  }, [])

  const kind = win?.kind
  // §12.7: main has no registry, so a restored pop-out opens on a generic floor and the real minimum
  // is asserted from here, once the kind is known.
  useEffect(() => {
    const def = kind ? WIDGETS[kind] : undefined
    if (!def) return
    void window.os.popout.setMinSize(windowId, def.minSize.w, def.minSize.h)
  }, [kind, windowId])

  useEffect(
    () =>
      window.os.bus.on((m) => {
        if (m.kind === 'canvas-invalidate') return void reload()
        if (m.windowId !== windowId) return
        if (m.kind === 'window-config') setWin((w) => (w ? { ...w, config: { ...w.config, ...(m.data ?? {}) } } : w))
        else if (m.kind === 'window-state') void reload()
      }),
    [windowId, reload]
  )

  const pinned = !!win?.pinned
  const setPinned = useCallback(
    (next: boolean): void => {
      setWin((w) => (w ? { ...w, pinned: next ? 1 : 0 } : w))
      void window.os.popout.setPinned(windowId, next)
      void api.windows.update(windowId, { pinned: next }).catch(() => undefined)
    },
    [windowId]
  )

  // A pop-out has no canvas store, so it answers the window-scoped menu actions itself — otherwise ⌘W
  // would be dead here. Closing is what "return to canvas" means: main persists `state: 'normal'`.
  // While pinned, only the pin toggle answers: the buttons still work, but no shortcut may close,
  // minimize, or return a widget the user deliberately kept on top.
  useEffect(
    () =>
      window.os.onMenu((action) => {
        if (action === 'canvas:pin') return setPinned(!pinned)
        if (pinned) return
        if (action === 'close-window' || action === 'canvas:unpopout') window.os.closeSelf()
        else if (action === 'minimize-window') window.os.minimizeSelf()
      }),
    [pinned, setPinned]
  )

  const onConfig = useCallback(
    (patch: Record<string, unknown>): void => {
      setWin((w) => (w ? { ...w, config: { ...w.config, ...patch } } : w))
      window.os.bus.send({ kind: 'window-config', windowId, data: patch })
      void api.windows.update(windowId, { config: patch }).catch(() => undefined)
    },
    [windowId]
  )

  /** No bus kind carries a title, so the canvas picks a rename up on its next load. */
  const onTitle = useCallback(
    (title: string): void => {
      setWin((w) => (w ? { ...w, title } : w))
      void api.windows.update(windowId, { title }).catch(() => undefined)
    },
    [windowId]
  )

  const def = kind ? WIDGETS[kind] : undefined
  const label = win?.title || def?.label || 'Grain'
  const Body = def?.Component

  // The surface no longer draws a title, so the only place left for it is the OS window title.
  useEffect(() => {
    document.title = label
  }, [label])

  const body = (): JSX.Element => {
    if (error) {
      // A 404 is the deleted window, and its bare detail adds nothing to the sentence above it.
      const gone = /not found/i.test(error)
      return (
        <div className="popout-error">
          <strong>{gone ? 'This window is gone.' : 'This window could not be loaded.'}</strong>
          {!gone && <span>{error}</span>}
        </div>
      )
    }
    if (!win) return <div className="popout-error"><span>Loading…</span></div>
    if (!Body) {
      return (
        <div className="popout-error">
          <strong>{win.kind}</strong>
          <span>No widget for this kind in this build.</span>
        </div>
      )
    }
    return <Body window={win} focused={focused} live onConfig={onConfig} onTitle={onTitle} />
  }

  // No title bar: the surface itself is the OS drag region and `.popout-body` is inset out of it, so
  // the window still moves by anything within 8 px of an edge. The title lives in the OS window title.
  return (
    <div className={focused ? 'popout focused' : 'popout'}>
      <div className="popout-actions">
        {def?.statusful && <StatusRing conversationId={win?.ref_id} />}
        <button className="icon-btn" title={pinned ? 'Stop keeping on top (⌃⌘P)' : 'Keep on top (⌃⌘P)'} onClick={() => setPinned(!pinned)}>
          {pinned ? <PinOff size={13} /> : <Pin size={13} />}
        </button>
        <button className="icon-btn" title="Close (⌘W)" onClick={() => window.os.closeSelf()}><X size={13} /></button>
      </div>
      <div className="popout-body">{body()}</div>
    </div>
  )
}

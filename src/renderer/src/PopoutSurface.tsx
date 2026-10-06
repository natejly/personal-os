import { useCallback, useEffect, useRef, useState } from 'react'
import { Blend, Pin, PinOff, X } from 'lucide-react'
import type { CanvasWindow } from '@shared/types'
import { api } from './lib/api'
import { useStore } from './store'
import { WIDGETS } from './canvas/registry'
import { clampOpacity, MIN_OPACITY, nextOpacity, opacityPercent } from './canvas/opacity'
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
  const [tuning, setTuning] = useState(false)
  const theme = useStore((s) => s.settings.theme)
  /** The slider fires per pixel; the window follows every tick, the backend only once the drag rests. */
  const saveOpacity = useRef<ReturnType<typeof setTimeout> | null>(null)

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
        // main broadcasts these when the tray pins or fades every pop-out at once.
        else if (m.kind === 'window-bounds') {
          const d = m.data ?? {}
          setWin((w) => {
            if (!w) return w
            const next = { ...w }
            if (typeof d.pinned === 'number') next.pinned = d.pinned
            if (typeof d.opacity === 'number') next.opacity = d.opacity
            return next
          })
        } else if (m.kind === 'window-state') void reload()
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

  const opacity = clampOpacity(win?.opacity ?? 1)
  const setOpacity = useCallback(
    (next: number): void => {
      const o = clampOpacity(next)
      setWin((w) => (w ? { ...w, opacity: o } : w))
      void window.os.popout.setOpacity(windowId, o)
      // The canvas keeps a row for this window, so it hears about the change the way a pin does.
      window.os.bus.send({ kind: 'window-bounds', windowId, data: { opacity: o } })
      if (saveOpacity.current) clearTimeout(saveOpacity.current)
      saveOpacity.current = setTimeout(() => {
        saveOpacity.current = null
        void api.windows.update(windowId, { opacity: o }).catch(() => undefined)
      }, 350)
    },
    [windowId]
  )

  useEffect(() => () => {
    if (saveOpacity.current) clearTimeout(saveOpacity.current)
  }, [])

  // The slider is a transient popover: clicking away from the window puts it away.
  useEffect(() => {
    if (!focused) setTuning(false)
  }, [focused])

  // A pop-out has no canvas store, so it answers the window-scoped menu actions itself — otherwise ⌘W
  // would be dead here. Closing is what "return to canvas" means: main persists `state: 'normal'`.
  // While pinned, only the pin toggle answers: the buttons still work, but no shortcut may close,
  // minimize, or return a widget the user deliberately kept on top.
  useEffect(
    () =>
      window.os.onMenu((action) => {
        if (action === 'canvas:pin') return setPinned(!pinned)
        // Transparency is not a way to lose the window, so it answers while pinned as well.
        if (action === 'canvas:opacity:down') return setOpacity(nextOpacity(opacity, 1))
        if (action === 'canvas:opacity:up') return setOpacity(nextOpacity(opacity, -1))
        if (action.startsWith('canvas:opacity:')) {
          const pct = Number(action.slice('canvas:opacity:'.length))
          return Number.isFinite(pct) ? setOpacity(pct / 100) : undefined
        }
        if (pinned) return
        if (action === 'close-window' || action === 'canvas:unpopout') window.os.closeSelf()
        else if (action === 'minimize-window') window.os.minimizeSelf()
      }),
    [pinned, setPinned, opacity, setOpacity]
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
  const pct = opacityPercent(opacity)

  return (
    <div
      className={['popout', focused && 'focused', tuning && 'tuning', opacity < 1 && 'translucent'].filter(Boolean).join(' ')}
    >
      <div className="popout-actions">
        {def?.statusful && <StatusRing conversationId={win?.ref_id} />}
        <button
          className={tuning ? 'icon-btn on' : 'icon-btn'}
          title={opacity < 1 ? `${pct}% opaque · scroll or ⌃⌘[ / ⌃⌘]` : 'Make transparent (⌃⌘[ or scroll here)'}
          aria-expanded={tuning}
          onClick={() => setTuning((t) => !t)}
          // A wheel over the button is the fast path; the slider is for aiming at a level.
          onWheel={(e) => {
            e.preventDefault()
            setOpacity(nextOpacity(opacity, e.deltaY > 0 ? 1 : -1))
          }}
        >
          <Blend size={13} />
        </button>
        <button className="icon-btn" title={pinned ? 'Stop keeping on top (⌃⌘P)' : 'Keep on top (⌃⌘P)'} onClick={() => setPinned(!pinned)}>
          {pinned ? <PinOff size={13} /> : <Pin size={13} />}
        </button>
        <button className="icon-btn" title="Close (⌘W)" onClick={() => window.os.closeSelf()}><X size={13} /></button>
      </div>
      {tuning && (
        <div className="popout-opacity" onPointerDown={(e) => e.stopPropagation()}>
          <input
            type="range"
            min={Math.round(MIN_OPACITY * 100)}
            max={100}
            step={5}
            value={pct}
            autoFocus
            aria-label="Window opacity"
            onChange={(e) => setOpacity(Number(e.target.value) / 100)}
            onKeyDown={(e) => {
              if (e.key === 'Escape') setTuning(false)
            }}
          />
          <button className="popout-opacity-reset" disabled={opacity >= 1} onClick={() => setOpacity(1)}>{pct}%</button>
        </div>
      )}
      <div className="popout-body">{body()}</div>
    </div>
  )
}

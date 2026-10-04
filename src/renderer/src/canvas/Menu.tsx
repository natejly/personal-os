import { useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type MutableRefObject, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { ChevronRight } from 'lucide-react'
import type { Point } from './snapping'

/** Keep a menu this far inside the viewport. */
const MENU_GAP = 8
/** Hover dwell before a submenu opens (or a sibling row closes it), so a diagonal pass does not flicker. */
const HOVER_MS = 120
/** `.win-menu` padding + border: a submenu's first row lines up with the row that opened it. */
const SUB_INSET = 5

export type MenuEntry =
  | {
      kind?: 'item'
      label: string
      icon?: ReactNode
      /** rendered as-is beside the label; must match the accelerator in main's menu when there is one */
      accel?: string
      danger?: boolean
      disabled?: boolean
      run: () => void
    }
  | { kind: 'submenu'; label: string; icon?: ReactNode; items: () => MenuEntry[] | Promise<MenuEntry[]> }
  | { kind: 'separator' }
  | { kind: 'header'; label: string }

const ITEM = '[role="menuitem"]:not(:disabled)'

/** Focus a menu's first row, unless focus is already inside it. */
export function focusFirstItem(menu: HTMLElement | null): void {
  if (menu && !menu.contains(document.activeElement)) menu.querySelector<HTMLElement>(ITEM)?.focus({ preventScroll: true })
}

/** On the role="menu" element: arrows, Home and End move between rows, Tab closes. Anything else inside (a form) keeps its keys. */
export function menuKeyDown(e: ReactKeyboardEvent<HTMLElement>, onClose: () => void): void {
  if (!(e.target as HTMLElement).closest('[role="menuitem"]')) return
  const items = Array.from(e.currentTarget.querySelectorAll<HTMLElement>(ITEM))
  const at = items.indexOf(document.activeElement as HTMLElement)
  const go = (i: number): void => {
    e.preventDefault()
    e.stopPropagation()
    items[(i + items.length) % items.length]?.focus()
  }
  if (e.key === 'ArrowDown') go(at + 1)
  else if (e.key === 'ArrowUp') go(at < 0 ? -1 : at - 1)
  else if (e.key === 'Home') go(0)
  else if (e.key === 'End') go(-1)
  else if (e.key === 'Tab') {
    e.preventDefault()
    onClose()
  }
}

/** A menu took focus when it opened: hand it back to the opener on close, unless something else claimed it. */
export function useReturnFocus(): void {
  // Read during the first render, before the menu moves focus into itself.
  const opener = useRef<Element | null | undefined>(undefined)
  if (opener.current === undefined) opener.current = document.activeElement
  useEffect(() => () => {
    const el = opener.current
    const lost = !document.activeElement || document.activeElement === document.body
    if (lost && el instanceof HTMLElement && el.isConnected) el.focus()
  }, [])
}

const noop = (): void => undefined
const LOADING: MenuEntry[] = [{ label: 'Loading…', disabled: true, run: noop }]
const FAILED: MenuEntry[] = [{ label: "Couldn't load", disabled: true, run: noop }]

/** An open submenu: which row of its parent opened it, where that row is, and its entries (null while loading). */
interface Child {
  /** the open token, so a reopened submenu remounts (and re-measures) */
  id: number
  index: number
  anchor: DOMRect
  entries: MenuEntry[] | null
}

interface LevelProps {
  entries: MenuEntry[]
  /** root: the pointer; submenu: the opening row's rect */
  at: Point | DOMRect
  sub: boolean
  onClose: () => void
  /** the pointer reached this level: cancel the parent's pending hover switch */
  onEnter?: () => void
  /** ArrowLeft in a submenu: close it and refocus the row that opened it */
  onBack?: () => void
  alive: MutableRefObject<boolean>
}

const isRect = (a: Point | DOMRect): a is DOMRect => 'right' in a

/** One level of the menu. An open child renders as this level's sibling, so it escapes the scroll box. */
function Level({ entries, at, sub, onClose, onEnter, onBack, alive }: LevelProps): JSX.Element {
  const box = useRef<HTMLDivElement | null>(null)
  const start = isRect(at) ? { x: at.right, y: at.top - SUB_INSET } : at
  const [pos, setPos] = useState<Point>(start)
  const [child, setOpenChild] = useState<Child | null>(null)
  /** The open row, readable from a hover timer that was armed a render ago. */
  const openIndex = useRef<number | null>(null)
  const setChild = (c: Child | null): void => {
    openIndex.current = c?.index ?? null
    setOpenChild(c)
  }
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  /** Bumped on every open/close, so a slow `items()` cannot land in a submenu that was since replaced. */
  const token = useRef(0)

  const ax = isRect(at) ? at.right : at.x
  const ay = isRect(at) ? at.top : at.y
  useLayoutEffect(() => {
    const el = box.current
    if (!el) return
    const { width, height } = el.getBoundingClientRect()
    let x: number
    if (isRect(at)) {
      // Open to the right of the row; flip to its left edge when that would overflow.
      x = at.right + width > window.innerWidth - MENU_GAP ? at.left - width : at.right
      x = Math.max(MENU_GAP, x)
    } else {
      x = Math.max(MENU_GAP, Math.min(at.x, window.innerWidth - width - MENU_GAP))
    }
    const y0 = isRect(at) ? at.top - SUB_INSET : at.y
    setPos({ x, y: Math.max(MENU_GAP, Math.min(y0, window.innerHeight - height - MENU_GAP)) })
    // `entries` too: a submenu grows when its items load.
  }, [ax, ay, entries])

  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current)
  }, [])

  // The root takes focus when it opens; a submenu only when its row has focus (keyboard or click, not a
  // hover). `entries` too: a lazy submenu's rows arrive after it mounts.
  useEffect(() => {
    if (!sub || document.activeElement?.getAttribute('aria-expanded') === 'true') focusFirstItem(box.current)
  }, [entries])

  const cancel = (): void => {
    if (timer.current) clearTimeout(timer.current)
    timer.current = null
  }

  const open = (index: number, row: HTMLElement): void => {
    cancel()
    const e = entries[index]
    if (!e || e.kind !== 'submenu') return
    if (openIndex.current === index) return
    const t = ++token.current
    const anchor = row.getBoundingClientRect()
    let res: MenuEntry[] | Promise<MenuEntry[]>
    try {
      res = e.items()
    } catch {
      res = FAILED
    }
    if (Array.isArray(res)) return setChild({ id: t, index, anchor, entries: res })
    setChild({ id: t, index, anchor, entries: null })
    res.then(
      (list) => {
        if (alive.current && token.current === t) setChild({ id: t, index, anchor, entries: list })
      },
      () => {
        if (alive.current && token.current === t) setChild({ id: t, index, anchor, entries: FAILED })
      }
    )
  }

  const close = (): void => {
    cancel()
    token.current++
    setChild(null)
  }

  /** Hovering a row: a submenu row opens after the dwell, any other row closes the open child. */
  const hover = (index: number, row: HTMLElement): void => {
    cancel()
    const e = entries[index]
    const isSub = e?.kind === 'submenu'
    if (openIndex.current === index) return
    if (!isSub && openIndex.current === null) return
    timer.current = setTimeout(() => {
      timer.current = null
      if (isSub) open(index, row)
      else close()
    }, HOVER_MS)
  }

  return (
    <>
      <div
        ref={box}
        className={sub ? 'win-menu sub' : 'win-menu'}
        role="menu"
        style={{ left: pos.x, top: pos.y }}
        onMouseEnter={onEnter}
        onKeyDown={(e) => {
          const row = document.activeElement as HTMLElement | null
          if (e.key === 'ArrowRight' && row?.getAttribute('aria-haspopup') === 'menu') {
            e.preventDefault()
            row.click()
          } else if (e.key === 'ArrowLeft' && onBack) {
            e.preventDefault()
            onBack()
          } else menuKeyDown(e, onClose)
        }}
      >
        {entries.map((it, i) => {
          if (it.kind === 'separator') return <div key={i} className="win-menu-sep" role="separator" />
          if (it.kind === 'header') return <div key={i} className="win-menu-head">{it.label}</div>
          if (it.kind === 'submenu') {
            const on = child?.index === i
            return (
              <button
                key={i}
                role="menuitem"
                aria-haspopup="menu"
                aria-expanded={on}
                data-i={i}
                className={on ? 'win-menu-item open' : 'win-menu-item'}
                onMouseEnter={(e) => hover(i, e.currentTarget)}
                onClick={(e) => open(i, e.currentTarget)}
              >
                {it.icon}
                <span className="win-menu-label">{it.label}</span>
                <ChevronRight size={12} className="win-menu-chev" />
              </button>
            )
          }
          return (
            <button
              key={i}
              role="menuitem"
              disabled={it.disabled}
              className={it.danger ? 'win-menu-item danger' : 'win-menu-item'}
              onMouseEnter={(e) => hover(i, e.currentTarget)}
              onClick={() => {
                onClose()
                it.run()
              }}
            >
              {it.icon}
              <span className="win-menu-label">{it.label}</span>
              {it.accel && <kbd>{it.accel}</kbd>}
            </button>
          )
        })}
      </div>
      {child && (
        <Level
          key={child.id}
          entries={child.entries ?? LOADING}
          at={child.anchor}
          sub
          onClose={onClose}
          onEnter={cancel}
          onBack={() => {
            const row = box.current?.querySelector<HTMLElement>(`[data-i="${child.index}"]`)
            close()
            row?.focus()
          }}
          alive={alive}
        />
      )}
    </>
  )
}

/**
 * The canvas's one context menu — window menu, plane menu, add-widget menu — in a body portal because
 * `.win` clips its overflow and the plane is scaled. Renderer UI, not an `Electron.Menu`: it has to sit
 * in the same material as everything else. Submenus load lazily and nest to any depth.
 */
export default function ContextMenu({ at, items, onClose }: { at: Point; items: MenuEntry[]; onClose: () => void }): JSX.Element {
  // A submenu's `items()` may resolve after the menu is gone; its result is then dropped.
  const alive = useRef(true)
  useReturnFocus()
  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
    }
  }, [])

  useEffect(() => {
    // Capture phase: Canvas also listens for Escape on window, and it would clear the selection too.
    const onKey = (e: KeyboardEvent): void => {
      if (e.key !== 'Escape') return
      e.stopPropagation()
      onClose()
    }
    // Scrolling a menu that overflows is the one wheel that must not close it.
    const onWheel = (e: WheelEvent): void => {
      const m = (e.target as Element | null)?.closest?.('.win-menu')
      if (m && m.scrollHeight > m.clientHeight) return
      onClose()
    }
    window.addEventListener('keydown', onKey, true)
    window.addEventListener('wheel', onWheel, { capture: true, passive: true })
    window.addEventListener('resize', onClose)
    return () => {
      window.removeEventListener('keydown', onKey, true)
      window.removeEventListener('wheel', onWheel, true)
      window.removeEventListener('resize', onClose)
    }
  }, [onClose])

  return createPortal(
    <>
      <div className="win-menu-backdrop" onPointerDown={onClose} onContextMenu={(e) => { e.preventDefault(); onClose() }} />
      <Level entries={items} at={at} sub={false} onClose={onClose} alive={alive} />
    </>,
    document.body
  )
}

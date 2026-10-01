import { useLayoutEffect, useRef } from 'react'

/*
 * A draggable divider. Each pane's size lives in one CSS variable on :root (`--<id>`), so the
 * stylesheet keeps owning layout and the handle only moves a number. Sizes persist in localStorage:
 * like the open folders in the Docs tree, they are this window's preference, not data.
 */

const PREFIX = 'grain.pane.'

function stored(id: string): number | null {
  try {
    const v = Number(localStorage.getItem(PREFIX + id))
    return Number.isFinite(v) && v > 0 ? v : null
  } catch {
    return null
  }
}

function save(id: string, v: number): void {
  try { localStorage.setItem(PREFIX + id, String(Math.round(v * 10) / 10)) } catch { /* private window */ }
}

function apply(id: string, v: number, unit: 'px' | '%'): void {
  document.documentElement.style.setProperty(`--${id}`, `${v}${unit}`)
}

type Props = {
  /** Storage key and CSS variable name: `--${id}`. */
  id: string
  defaultSize: number
  min: number
  max: number
  /** '%' sizes are a share of the handle's parent's width. */
  unit?: 'px' | '%'
  /** 'right': the pane sits left of the handle, so dragging right grows it. 'left': the reverse. */
  grows: 'right' | 'left'
  /** Dragging this far under `min` and letting go collapses the pane instead of clamping it. */
  onCollapse?: () => void
  label: string
  className?: string
}

export default function ResizeHandle({ id, defaultSize, min, max, unit = 'px', grows, onCollapse, label, className }: Props): JSX.Element {
  const size = useRef(defaultSize)

  // Before paint, so a remembered width never flashes the default first.
  useLayoutEffect(() => {
    size.current = Math.min(max, Math.max(min, stored(id) ?? defaultSize))
    apply(id, size.current, unit)
  }, [id, defaultSize, min, max, unit])

  const set = (v: number): void => {
    size.current = Math.min(max, Math.max(min, v))
    apply(id, size.current, unit)
  }

  const onPointerDown = (e: React.PointerEvent<HTMLDivElement>): void => {
    if (e.button !== 0) return
    e.preventDefault()
    const el = e.currentTarget
    // Capture keeps the drag ours even when the pointer crosses a <webview> or an iframe, which
    // would otherwise swallow every move event.
    el.setPointerCapture(e.pointerId)
    const startX = e.clientX
    const start = size.current
    const span = unit === '%' ? (el.parentElement?.getBoundingClientRect().width ?? 1) : 1
    const sign = grows === 'right' ? 1 : -1
    let raw = start
    document.body.classList.add('pane-resizing')

    const move = (ev: PointerEvent): void => {
      const dx = (ev.clientX - startX) * sign
      raw = start + (unit === '%' ? (dx / span) * 100 : dx)
      set(raw)
    }
    const up = (): void => {
      el.removeEventListener('pointermove', move)
      el.removeEventListener('pointerup', up)
      el.removeEventListener('pointercancel', up)
      document.body.classList.remove('pane-resizing')
      if (onCollapse && raw < min * 0.6) {
        // The pane goes away at the width it had before this drag, so reopening it is not a sliver.
        set(start)
        onCollapse()
      }
      save(id, size.current)
    }
    el.addEventListener('pointermove', move)
    el.addEventListener('pointerup', up)
    el.addEventListener('pointercancel', up)
  }

  const onKeyDown = (e: React.KeyboardEvent): void => {
    const step = (unit === '%' ? 2 : 16) * (e.shiftKey ? 4 : 1)
    const d = e.key === 'ArrowRight' ? step : e.key === 'ArrowLeft' ? -step : 0
    if (!d) return
    e.preventDefault()
    set(size.current + d * (grows === 'right' ? 1 : -1))
    save(id, size.current)
  }

  const reset = (): void => {
    set(defaultSize)
    save(id, size.current)
  }

  return (
    <div
      className={`resize-handle no-drag ${className ?? ''}`}
      role="separator"
      aria-orientation="vertical"
      aria-label={label}
      aria-valuemin={min}
      aria-valuemax={max}
      tabIndex={0}
      title={`${label}: drag to resize, double-click to reset`}
      onPointerDown={onPointerDown}
      onKeyDown={onKeyDown}
      onDoubleClick={reset}
    />
  )
}

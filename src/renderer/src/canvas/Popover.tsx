import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import '../styles/spaces.css'

/** Keep a fixed popover this far inside the viewport. */
const GAP = 8

/**
 * A classic-side popover: a body portal at z 200/201, so it sits over the canvas chrome and the
 * sidebar's scroll container cannot clip it. Anchored at `at` and re-clamped whenever its content
 * resizes (a list that loads after opening). Escape closes it, in the capture phase so the canvas's
 * own Escape never sees the key.
 */
export function Popover({ at, onClose, className, children }: { at: { x: number; y: number }; onClose: () => void; className?: string; children: ReactNode }): JSX.Element {
  const ref = useRef<HTMLDivElement>(null)
  const [pos, setPos] = useState(at)

  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    const clamp = (): void => {
      const r = el.getBoundingClientRect()
      setPos({
        x: Math.max(GAP, Math.min(at.x, window.innerWidth - r.width - GAP)),
        y: Math.max(GAP, Math.min(at.y, window.innerHeight - r.height - GAP))
      })
    }
    clamp()
    const ro = new ResizeObserver(clamp)
    ro.observe(el)
    window.addEventListener('resize', clamp)
    return () => {
      ro.disconnect()
      window.removeEventListener('resize', clamp)
    }
  }, [at])

  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      if (e.key !== 'Escape') return
      // An inline rename cancels itself on Escape; only then does the next Escape close the popover.
      if ((e.target as HTMLElement | null)?.closest?.('[data-own-escape]')) return
      e.stopPropagation()
      onClose()
    }
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  }, [onClose])

  return createPortal(
    <>
      <div
        className="preset-pop-backdrop no-drag"
        onMouseDown={onClose}
        onContextMenu={(e) => {
          e.preventDefault()
          onClose()
        }}
      />
      <div ref={ref} className={`preset-pop no-drag${className ? ` ${className}` : ''}`} style={{ left: pos.x, top: pos.y }} role="dialog">
        {children}
      </div>
    </>,
    document.body
  )
}

import { useEffect, useRef, useState, type ReactNode } from 'react'

/**
 * A button for actions that permanently delete data. A pointer must hold it for --dur-hold while a
 * fill grows across it; letting go early cancels and the fill springs back. Enter / Space (a click
 * with no pointer, detail 0) cannot hold, so it asks with a native confirm() first.
 */
export function HoldButton({ onConfirm, confirmText, className = '', children, ...rest }: {
  onConfirm: () => void
  /** Asked via confirm() when the button is activated from the keyboard. */
  confirmText: string
  className?: string
  children: ReactNode
  title?: string
  'aria-label'?: string
}): JSX.Element {
  const [holding, setHolding] = useState(false)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const cancel = (): void => {
    if (timer.current) clearTimeout(timer.current)
    timer.current = null
    setHolding(false)
  }
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current) }, [])
  const start = (): void => {
    const ms = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--dur-hold')) || 600
    setHolding(true)
    timer.current = setTimeout(() => { cancel(); onConfirm() }, ms)
  }
  return (
    <button
      type="button"
      className={`hold-btn ${className}${holding ? ' holding' : ''}`}
      onPointerDown={(e) => { if (e.button === 0) start() }}
      onPointerUp={cancel}
      onPointerLeave={cancel}
      onPointerCancel={cancel}
      onClick={(e) => { if (e.detail === 0 && confirm(confirmText)) onConfirm() }}
      {...rest}
    >
      {children}
    </button>
  )
}

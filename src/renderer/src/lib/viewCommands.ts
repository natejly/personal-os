import { useEffect, useRef } from 'react'

/**
 * A palette command aimed at a view that may not be mounted yet. The request is held until the view
 * takes it, on mount or when it is already on screen.
 */
const pending = new Set<string>()
const EVENT = 'grain:view-command'

export function requestViewCommand(name: string): void {
  pending.add(name)
  window.dispatchEvent(new CustomEvent(EVENT, { detail: name }))
}

/** Runs `fn` once per request for `name`, including one made before this view mounted. */
export function useViewCommand(name: string, fn: () => void): void {
  const ref = useRef(fn)
  ref.current = fn
  useEffect(() => {
    const take = (): void => { if (pending.delete(name)) ref.current() }
    take()
    const on = (e: Event): void => { if ((e as CustomEvent<string>).detail === name) take() }
    window.addEventListener(EVENT, on)
    return (): void => window.removeEventListener(EVENT, on)
  }, [name])
}

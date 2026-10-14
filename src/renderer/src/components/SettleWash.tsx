import { useEffect, useRef, useState } from 'react'

/**
 * Drop inside a `position: relative` approval row. When `awaiting` flips to false it plays one short
 * colour wash toward the outcome (green approved, red denied) over the row and then removes itself.
 * Rows that mount already resolved (history, reload) never wash.
 */
export function SettleWash({ awaiting, denied }: { awaiting: boolean; denied: boolean }): JSX.Element | null {
  const was = useRef(awaiting)
  const [on, setOn] = useState(false)
  useEffect(() => {
    if (was.current && !awaiting) {
      setOn(true)
      was.current = awaiting
      const t = setTimeout(() => setOn(false), 700)
      return () => clearTimeout(t)
    }
    was.current = awaiting
  }, [awaiting])
  return on ? <span className={`settle-wash ${denied ? 'deny' : 'allow'}`} aria-hidden="true" /> : null
}

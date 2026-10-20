import { useEffect, useState, type ReactNode } from 'react'

const KEY = 'grain.settings.disclosure.'

function read(id: string): boolean {
  try { return localStorage.getItem(KEY + id) === '1' } catch { return false }
}

/** Fired by the Settings modal before it scrolls to a block (`detail` is the block's `sec-` id), so a disclosure holding it can open first. */
export const SETTINGS_JUMP = 'grain:settings-jump'

/** A collapsed-by-default group of rarely changed rows. Open state is remembered per id. Children are not mounted while closed, unless `keepMounted`: then they stay mounted and are hidden, so half-written input survives a collapse. `secs` are the block ids inside it that a search or link may jump to: a jump to one opens the disclosure. */
export default function Disclosure({ id, title, secs, keepMounted, children }: { id: string; title: ReactNode; secs?: readonly string[]; keepMounted?: boolean; children: ReactNode }): JSX.Element {
  const [open, setOpen] = useState(() => read(id))
  useEffect(() => {
    if (!secs?.length) return
    const on = (e: Event): void => {
      if (!secs.includes((e as CustomEvent<string>).detail)) return
      setOpen(true)
      try { localStorage.setItem(KEY + id, '1') } catch { /* ignore */ }
    }
    window.addEventListener(SETTINGS_JUMP, on)
    return () => window.removeEventListener(SETTINGS_JUMP, on)
  }, [id, secs?.join(',')])  // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <details className="settings-disclosure" data-disclosure={id} open={open}
      onToggle={(e) => {
        const next = (e.currentTarget as HTMLDetailsElement).open
        if (next === open) return
        setOpen(next)
        try { localStorage.setItem(KEY + id, next ? '1' : '0') } catch { /* ignore */ }
      }}>
      <summary>{title}</summary>
      {keepMounted ? <div hidden={!open}>{children}</div> : open && children}
    </details>
  )
}

import { useEffect, useId, useRef, useState, type ReactNode } from 'react'
import { RotateCcw } from '../../icons'
import { scopeLabel, sourceText, valueText } from '../../lib/settingSource'
import { useEffective } from './useEffective'

/** One setting: title on the left (help shows only as its tooltip), its control on the right, and small marks for "modified", where the value came from, and reset. Renders fully without effective data. */
export default function SettingRow({ settingKey, title, help, value, defaultValue, onReset, danger, children }: {
  settingKey?: string; title: ReactNode; help?: ReactNode; value?: unknown; defaultValue?: unknown; onReset?: () => void; danger?: boolean; children: ReactNode
}): JSX.Element {
  const entry = useEffective().entry(settingKey ?? '')
  const comparable = value !== undefined && defaultValue !== undefined
  const modified = comparable ? value !== defaultValue : !!entry && entry.source !== 'default'
  const [open, setOpen] = useState(false)
  const box = useRef<HTMLSpanElement>(null)
  const pop = useId()
  useEffect(() => {
    if (!open) return
    const away = (e: MouseEvent): void => { if (!box.current?.contains(e.target as Node)) setOpen(false) }
    const key = (e: KeyboardEvent): void => { if (e.key === 'Escape') { e.stopPropagation(); setOpen(false) } }
    document.addEventListener('mousedown', away)
    document.addEventListener('keydown', key, true)
    return () => { document.removeEventListener('mousedown', away); document.removeEventListener('keydown', key, true) }
  }, [open])
  const showMeta = modified || !!entry
  return (
    <div className={`setting-row set-row-x${danger ? ' danger' : ''}`} data-setting={settingKey}>
      <span className="toggle-text"><b title={typeof help === 'string' ? help : undefined}>{title}</b></span>
      <span className="set-control">{children}</span>
      {showMeta && (
        <span className="set-meta" ref={box}>
          {modified && <span className="set-modified" role="img" title="Modified from the default" aria-label="Modified from the default" />}
          {entry && (
            <button type="button" className="set-scope" aria-expanded={open} aria-controls={open ? pop : undefined} onClick={() => setOpen((o) => !o)}>{scopeLabel(entry.scope)}</button>
          )}
          {modified && onReset && (
            <button type="button" className="icon-btn set-reset" title="Reset to default" aria-label="Reset to default" onClick={onReset}><RotateCcw size={12} aria-hidden /></button>
          )}
          {open && entry && (
            <div className="set-pop" id={pop} role="dialog" aria-label="Where this value comes from">
              <p>Set by {sourceText(entry.source)}</p>
              {entry.shadowed.length > 0 && (
                <>
                  <p className="muted">Also set, but overridden:</p>
                  <ul>{entry.shadowed.map((s) => <li key={s.source}>{sourceText(s.source)}: <code>{valueText(s.value)}</code></li>)}</ul>
                </>
              )}
            </div>
          )}
        </span>
      )}
    </div>
  )
}

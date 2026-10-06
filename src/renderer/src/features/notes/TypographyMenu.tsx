import { Minus, Plus } from 'lucide-react'
import type { DocTypography } from '@shared/types'
import { FONTS, MEASURE, SIZE } from './typography'

/**
 * Font, size and measure controls, shared by a doc's own picker (DocsView) and the global default
 * (Settings). `value` is what is in effect; `onChange` gets only the key that moved.
 */
export default function TypographyControls({ value, onChange, onReset }: {
  value: DocTypography
  onChange: (patch: DocTypography) => void
  /** Shown as "Use default" when the value is a doc's own override. */
  onReset?: () => void
}): JSX.Element {
  const size = value.size ?? 0
  const measure = value.measure ?? 0
  const step = (key: 'size' | 'measure', dir: 1 | -1): void => {
    const r = key === 'size' ? SIZE : MEASURE
    const cur = value[key] ?? (key === 'size' ? 15 : 72)
    onChange({ [key]: Math.max(r.min, Math.min(r.max, cur + dir * r.step)) })
  }
  return (
    <div className="doc-type">
      <div className="seg" role="group" aria-label="Font">
        {FONTS.map((f) => (
          <button key={f.id} type="button" className={value.font === f.id ? 'on' : ''} aria-pressed={value.font === f.id}
            style={{ fontFamily: f.stack }} onClick={() => onChange({ font: f.id })}>{f.label}</button>
        ))}
      </div>
      <div className="doc-type-row">
        <span>Size</span>
        <button type="button" className="icon-btn ghost" aria-label="Smaller text" onClick={() => step('size', -1)}><Minus size={12} /></button>
        <b>{size ? `${size}px` : 'Auto'}</b>
        <button type="button" className="icon-btn ghost" aria-label="Larger text" onClick={() => step('size', 1)}><Plus size={12} /></button>
      </div>
      <div className="doc-type-row">
        <span>Width</span>
        <button type="button" className="icon-btn ghost" aria-label="Narrower line" onClick={() => step('measure', -1)}><Minus size={12} /></button>
        <b>{measure ? `${measure} ch` : 'Auto'}</b>
        <button type="button" className="icon-btn ghost" aria-label="Wider line" onClick={() => step('measure', 1)}><Plus size={12} /></button>
      </div>
      {onReset && <button type="button" className="ghost-btn xs" onClick={onReset}>Use default</button>}
    </div>
  )
}

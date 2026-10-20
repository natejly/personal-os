import { Minus, Plus } from '../../icons'
import type { DocTypography } from '@shared/types'
import SettingRow from '../../components/settings/SettingRow'
import { FONTS, MEASURE, SIZE } from './typography'

/**
 * Font, size and measure controls, shared by a doc's own picker (DocsView) and the global default
 * (Settings). `value` is what is in effect; `onChange` gets only the key that moved.
 */
export default function TypographyControls({ value, onChange, onReset, rows }: {
  value: DocTypography
  onChange: (patch: DocTypography) => void
  /** Shown as "Use default" when the value is a doc's own override. */
  onReset?: () => void
  /** Settings layout: a labelled row each for font, size and width, in place of the menu's stack. */
  rows?: boolean
}): JSX.Element {
  const size = value.size ?? 0
  const measure = value.measure ?? 0
  const step = (key: 'size' | 'measure', dir: 1 | -1): void => {
    const r = key === 'size' ? SIZE : MEASURE
    const cur = value[key] ?? (key === 'size' ? 15 : 72)
    onChange({ [key]: Math.max(r.min, Math.min(r.max, cur + dir * r.step)) })
  }
  if (rows) {
    const stepper = (key: 'size' | 'measure', label: string, less: string, more: string, text: string): JSX.Element => (
      <SettingRow title={label}>
        <button type="button" className="icon-btn ghost" aria-label={less} onClick={() => step(key, -1)}><Minus size={12} /></button>
        <b className="doc-type-val">{text}</b>
        <button type="button" className="icon-btn ghost" aria-label={more} onClick={() => step(key, 1)}><Plus size={12} /></button>
      </SettingRow>
    )
    return (
      <>
        <div className="setting-row doc-type-font">
          <span className="toggle-text"><b>Default file font</b></span>
          <div className="seg" role="group" aria-label="Font">
            {FONTS.map((f) => (
              <button key={f.id} type="button" className={value.font === f.id ? 'on' : ''} aria-pressed={value.font === f.id}
                style={{ fontFamily: f.stack }} onClick={() => onChange({ font: f.id })}>{f.label}</button>
            ))}
          </div>
        </div>
        {stepper('size', 'Size', 'Smaller text', 'Larger text', size ? `${size}px` : 'Auto')}
        {stepper('measure', 'Width', 'Narrower line', 'Wider line', measure ? `${measure} ch` : 'Auto')}
        {onReset && <SettingRow title="Reset"><button type="button" className="ghost-btn xs" onClick={onReset}>Use default</button></SettingRow>}
      </>
    )
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

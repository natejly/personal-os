import { useEffect, useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from 'react'
import { createPortal } from 'react-dom'
import { Check, ChevronDown, RotateCcw } from 'lucide-react'
import type { Effort } from '@shared/types'
import { useStore } from '../store'

const EFFORTS: { id: Effort; label: string }[] = [
  { id: 'default', label: 'Default' },
  { id: 'low', label: 'Low' },
  { id: 'medium', label: 'Medium' },
  { id: 'high', label: 'High' }
]

/** What the trigger shows after the model name: only the parameters that are not at their default. */
export function variantSuffix(effort: Effort, fast: boolean): string {
  const parts: string[] = []
  if (effort !== 'default') parts.push(EFFORTS.find((e) => e.id === effort)?.label ?? effort)
  if (fast) parts.push('Fast')
  return parts.join(' · ')
}

interface ModelMenuProps {
  model: string
  effort: Effort
  fast: boolean
  onModel: (model: string) => void
  onEffort: (effort: Effort) => void
  onFast: (fast: boolean) => void
  /** Composer footers open upward; a header control opens downward. */
  placement?: 'up' | 'down'
}

/**
 * Model, reasoning effort, and fast mode in one control, laid out the way Cursor's picker is:
 * a trigger with the model and its variant, a searchable model list, and an Edit panel for
 * the effort choices and the Fast switch.
 */
export default function ModelMenu({ model, effort, fast, onModel, onEffort, onFast, placement = 'up' }: ModelMenuProps): JSX.Element {
  const models = useStore((s) => s.models)
  const modelsError = useStore((s) => s.modelsError)
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [hi, setHi] = useState(0)
  const [editing, setEditing] = useState<string | null>(null)
  const [box, setBox] = useState<{ top: number; left: number; subLeft: number; subTop: number } | null>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const popRef = useRef<HTMLDivElement>(null)
  const subRef = useRef<HTMLDivElement>(null)
  const searchRef = useRef<HTMLInputElement>(null)

  const options = useMemo(() => {
    const list = models.some((m) => m.id === model) || !model ? models : [{ id: model }, ...models]
    const q = query.trim().toLowerCase()
    return q ? list.filter((m) => m.id.toLowerCase().includes(q)) : list
  }, [models, model, query])

  const suffix = variantSuffix(effort, fast)
  const dirty = effort !== 'default' || fast

  useEffect(() => {
    if (!open) return
    setQuery('')
    setEditing(null)
    const t = window.setTimeout(() => searchRef.current?.focus(), 0)
    return () => window.clearTimeout(t)
  }, [open])

  useEffect(() => {
    if (!open || query.trim()) return
    const i = options.findIndex((m) => m.id === model)
    setHi(i < 0 ? 0 : i)
  }, [open, query, model, options])

  useEffect(() => {
    if (!open) return
    const onDoc = (e: MouseEvent): void => {
      const n = e.target as Node
      if (triggerRef.current?.contains(n) || popRef.current?.contains(n) || subRef.current?.contains(n)) return
      setOpen(false)
    }
    const onKey = (e: KeyboardEvent): void => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', onDoc)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDoc)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  useLayoutEffect(() => {
    if (!open) { setBox(null); return }
    const place = (e?: Event): void => {
      if (e && (popRef.current?.contains(e.target as Node) || subRef.current?.contains(e.target as Node))) return
      const t = triggerRef.current
      const p = popRef.current
      if (!t || !p) return
      const tr = t.getBoundingClientRect()
      const pr = p.getBoundingClientRect()
      const gap = 6
      let top = placement === 'up' ? tr.top - gap - pr.height : tr.bottom + gap
      if (top < 8) top = tr.bottom + gap
      if (top + pr.height > window.innerHeight - 8) top = Math.max(8, tr.top - gap - pr.height)
      let left = tr.left
      if (left + pr.width > window.innerWidth - 8) left = Math.max(8, window.innerWidth - 8 - pr.width)
      const subW = 220
      let subLeft = left + pr.width + gap
      if (subLeft + subW > window.innerWidth - 8) subLeft = Math.max(8, left - gap - subW)
      const next = { top, left, subLeft, subTop: top }
      setBox((prev) => prev && prev.top === next.top && prev.left === next.left && prev.subLeft === next.subLeft && prev.subTop === next.subTop ? prev : next)
    }
    place()
    window.addEventListener('resize', place)
    window.addEventListener('scroll', place, true)
    return () => {
      window.removeEventListener('resize', place)
      window.removeEventListener('scroll', place, true)
    }
  }, [open, editing, query, placement, options.length])

  const pick = (id: string): void => {
    onModel(id)
    setOpen(false)
  }

  const applyTo = (id: string, fn: () => void): void => {
    if (id !== model) onModel(id)
    fn()
  }

  const onSearchKey = (e: ReactKeyboardEvent): void => {
    if (e.key === 'ArrowDown') { e.preventDefault(); setHi((i) => Math.min(options.length - 1, i + 1)) }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setHi((i) => Math.max(0, i - 1)) }
    else if (e.key === 'Enter') {
      e.preventDefault()
      const row = options[hi]
      if (row) pick(row.id)
    }
  }

  const label = suffix ? `${model}, ${suffix}` : model

  return (
    <div className="model-menu">
      <button
        ref={triggerRef}
        type="button"
        className="model-menu-trigger"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-label={`Model ${label}`}
        title={modelsError ?? label}
        onClick={() => setOpen((v) => !v)}
      >
        <span className="model-menu-name">{model || 'Select model'}</span>
        {suffix && <span className="model-menu-suffix">{suffix}</span>}
        <ChevronDown size={12} className="model-menu-chevron" />
      </button>
      {open && createPortal(
        <>
          <div
            ref={popRef}
            className="model-menu-pop"
            role="dialog"
            aria-label="Models"
            style={{ top: box?.top ?? -9999, left: box?.left ?? 0, visibility: box ? 'visible' : 'hidden' }}
          >
            <input
              ref={searchRef}
              className="model-menu-search"
              placeholder="Search models"
              aria-label="Search models"
              value={query}
              onChange={(e) => { setQuery(e.target.value); setHi(0) }}
              onKeyDown={onSearchKey}
            />
            {modelsError && <p className="model-menu-error">{modelsError}</p>}
            <div className="model-menu-list" role="listbox" aria-label="Models">
              {options.length === 0 && <p className="model-menu-empty">No models match.</p>}
              {options.map((m, i) => {
                const selected = m.id === model
                const on = editing === m.id
                return (
                  <div
                    key={m.id}
                    role="option"
                    aria-selected={selected}
                    className={`model-menu-row${selected ? ' selected' : ''}${i === hi ? ' hi' : ''}${on ? ' editing' : ''}`}
                    onMouseEnter={() => setHi(i)}
                    onClick={() => pick(m.id)}
                  >
                    <span className="model-menu-row-name">{m.id}</span>
                    <span className="model-menu-row-end">
                      {selected && <Check size={14} className="model-menu-check" aria-hidden />}
                      <button
                        type="button"
                        className="model-menu-edit"
                        aria-label={`Edit parameters for ${m.id}`}
                        aria-expanded={on}
                        onClick={(e) => { e.stopPropagation(); setEditing(on ? null : m.id) }}
                      >
                        Edit
                      </button>
                    </span>
                  </div>
                )
              })}
            </div>
          </div>
          {editing && (
            <div
              ref={subRef}
              className="model-menu-params"
              role="dialog"
              aria-label={`${editing} parameters`}
              style={{ top: box?.subTop ?? -9999, left: box?.subLeft ?? 0, visibility: box ? 'visible' : 'hidden' }}
            >
              <div className="model-menu-param-title">{editing}</div>
              <div className="model-menu-section">Effort</div>
              {EFFORTS.map((e) => (
                <button
                  key={e.id}
                  type="button"
                  role="menuitemradio"
                  aria-checked={effort === e.id}
                  className="model-menu-choice"
                  onClick={() => applyTo(editing, () => onEffort(e.id))}
                >
                  <span>{e.label}</span>
                  {effort === e.id && <Check size={14} />}
                </button>
              ))}
              <div className="model-menu-section">Options</div>
              <button
                type="button"
                role="switch"
                aria-checked={fast}
                className="model-menu-switch"
                title="Faster responses. Sends priority processing when the provider supports it."
                onClick={() => applyTo(editing, () => onFast(!fast))}
              >
                <span>Fast</span>
                <span className={`model-menu-track${fast ? ' on' : ''}`} aria-hidden><span /></span>
              </button>
              {dirty && (
                <button
                  type="button"
                  className="model-menu-reset"
                  onClick={() => applyTo(editing, () => { onEffort('default'); onFast(false) })}
                >
                  <RotateCcw size={13} /> Restore defaults
                </button>
              )}
            </div>
          )}
        </>,
        document.body
      )}
    </div>
  )
}

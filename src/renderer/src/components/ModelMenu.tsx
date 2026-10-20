import { Fragment, useEffect, useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from 'react'
import { createPortal } from 'react-dom'
import { Check, RotateCcw } from '../icons'
import { DEFAULT_EFFORT, type Effort } from '@shared/types'
import { modelChoices, modelLabel, showsEffort } from '../lib/modelLabel'
import { pickerVisible } from '../lib/pickerModels'
import { useStore } from '../store'

const EFFORTS: { id: Effort; label: string }[] = [
  { id: 'default', label: 'Model default' },
  { id: 'low', label: 'Low' },
  { id: 'medium', label: 'Medium' },
  { id: 'high', label: 'High' },
  { id: 'xhigh', label: 'Extra high' },
  { id: 'max', label: 'Max' }
]

/** What the trigger shows after the model name. Effort has its own dropdown, so only Fast lands here. */
export function variantSuffix(fast: boolean): string {
  return fast ? 'Fast' : ''
}

interface ModelMenuProps {
  model: string
  effort: Effort
  fast: boolean
  /** One change, one request: Restore defaults sends effort and fast together. */
  onChange: (change: { model?: string; effort?: Effort; fast?: boolean }) => void
  /** Composer footers open upward; a header control opens downward. */
  placement?: 'up' | 'down'
}

/**
 * Model, effort, and fast mode. The model list shows the model name, not the
 * provider path. Effort is the dropdown beside the model; Edit holds Fast and Restore defaults.
 */
export default function ModelMenu({ model, effort, fast, onChange, placement = 'up' }: ModelMenuProps): JSX.Element {
  const models = useStore((s) => s.models)
  const fastModel = useStore((s) => s.settings.fastModel)
  const pickerModels = useStore((s) => s.settings.pickerModels)
  const providers = useStore((s) => s.settings.providers)
  const names = useMemo(() => Object.fromEntries((providers ?? []).map((p) => [p.id, p.name])), [providers])
  const modelsError = useStore((s) => s.modelsError)
  const loadModels = useStore((s) => s.loadModels)
  const [retrying, setRetrying] = useState(false)
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
    // Embedding and similar models are not chat choices; the current model stays visible whatever its kind.
    const visible = pickerVisible(models, pickerModels)
    const ids = visible.map((m) => m.id)
    if (model && model !== 'auto' && !ids.includes(model)) ids.unshift(model)
    const owner = new Map(models.map((m) => [m.id, m.provider ?? '']))
    // Grouped by provider in the order they were added. The sort is stable, so names stay alphabetical inside a group.
    const order = (providers ?? []).map((p) => p.id)
    const rank = (g: string): number => { const i = order.indexOf(g); return i < 0 ? order.length : i }
    const rows = modelChoices(ids, query, model)
      .map((id) => ({ id, label: modelLabel(id), group: owner.get(id) ?? '' }))
      .sort((a, b) => rank(a.group) - rank(b.group))
    // Auto sits first once a fast model is set (or when the chat is already on it).
    return (fastModel || model === 'auto') && 'auto'.includes(query.trim().toLowerCase()) ? [{ id: 'auto', label: 'Auto', group: '' }, ...rows] : rows
  }, [models, model, query, fastModel, pickerModels, providers])
  // A header per provider only when more than one has rows.
  const grouped = new Set(options.filter((o) => o.id !== 'auto').map((o) => o.group)).size > 1

  const suffix = variantSuffix(fast)
  const effortShown = showsEffort(models, model)
  const editingEffortShown = editing ? showsEffort(models, editing) : true
  // A hidden control cannot be the reason for "Restore defaults".
  const dirty = (editingEffortShown && effort !== DEFAULT_EFFORT) || fast

  useEffect(() => {
    if (!open) return
    setQuery('')
    setEditing(null)
    if (modelsError || models.length === 0) void loadModels()
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
    const row = popRef.current?.querySelectorAll<HTMLElement>('.model-menu-row')[hi]
    const list = row?.parentElement
    if (!row || !list) return
    const top = row.offsetTop - list.offsetTop
    const bottom = top + row.offsetHeight
    if (top < list.scrollTop) list.scrollTop = top
    else if (bottom > list.scrollTop + list.clientHeight) list.scrollTop = bottom - list.clientHeight
  }, [open, hi, options])

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
      const margin = 8
      let top = placement === 'up' ? tr.top - gap - pr.height : tr.bottom + gap
      if (top < margin) top = tr.bottom + gap
      if (top + pr.height > window.innerHeight - margin) top = Math.max(margin, tr.top - gap - pr.height)
      if (top < margin) top = margin
      let left = tr.left
      if (left + pr.width > window.innerWidth - margin) left = Math.max(margin, window.innerWidth - margin - pr.width)
      const subW = 220
      let subLeft = left + pr.width + gap
      if (subLeft + subW > window.innerWidth - margin) subLeft = Math.max(margin, left - gap - subW)
      const subH = subRef.current?.getBoundingClientRect().height ?? 0
      let subTop = top
      if (subH > 0 && subTop + subH > window.innerHeight - margin) subTop = Math.max(margin, window.innerHeight - margin - subH)
      const next = { top, left, subLeft, subTop }
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
    onChange({ model: id })
    setOpen(false)
  }

  const applyTo = (id: string, change: { effort?: Effort; fast?: boolean }): void => {
    onChange({ ...(id !== model ? { model: id } : {}), ...change })
  }

  const retry = (): void => {
    setRetrying(true)
    void loadModels().finally(() => setRetrying(false))
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

  const shown = model ? modelLabel(model) : 'Select model'
  const effortLabel = effortShown && effort !== 'default' ? (EFFORTS.find((e) => e.id === effort)?.label ?? '') : ''
  const label = [shown, suffix, effortLabel].filter(Boolean).join(', ')

  return (
    <div className="model-menu">
      <button
        ref={triggerRef}
        type="button"
        className="composer-ctl model-menu-trigger"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-label={`Model ${label}`}
        title={modelsError ?? (model && model !== shown ? `${label} — ${model}` : label)}
        onClick={() => setOpen((v) => !v)}
      >
        <span className="model-menu-name">{shown}</span>
        {suffix && <span className="model-menu-suffix">{suffix}</span>}
        {effortLabel && <span className="model-menu-suffix">{effortLabel}</span>}
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
            {modelsError && (
              <p className="model-menu-error">
                {modelsError}{' '}
                <button type="button" className="model-menu-retry" disabled={retrying} onClick={retry}>Retry</button>
              </p>
            )}
            <div className="model-menu-list" role="listbox" aria-label="Models">
              {options.length === 0 && <p className="model-menu-empty">No models match.</p>}
              {options.map((m, i) => {
                const selected = m.id === model
                const on = editing === m.id
                return (
                  <Fragment key={m.id}>
                  {grouped && m.group && m.group !== options[i - 1]?.group && <div className="model-menu-section">{names[m.group] ?? m.group}</div>}
                  <div
                    role="option"
                    aria-selected={selected}
                    className={`model-menu-row${selected ? ' selected' : ''}${i === hi ? ' hi' : ''}${on ? ' editing' : ''}`}
                    onMouseEnter={() => setHi(i)}
                    onClick={() => pick(m.id)}
                  >
                    <span className="model-menu-row-name" title={m.id === m.label ? undefined : m.id}>{m.label}</span>
                    <span className="model-menu-row-end">
                      {selected && <Check size={14} className="model-menu-check" aria-hidden />}
                      {m.id !== 'auto' && <button
                        type="button"
                        className="model-menu-edit"
                        aria-label={`Edit parameters for ${m.label}`}
                        aria-expanded={on}
                        onClick={(e) => { e.stopPropagation(); setEditing(on ? null : m.id) }}
                      >
                        Edit
                      </button>}
                    </span>
                  </div>
                  </Fragment>
                )
              })}
            </div>
            {effortShown && (
              <label className="model-menu-effort">
                <span>Effort</span>
                <select
                  className="chat-control"
                  aria-label="Effort level"
                  title="Effort level"
                  value={effort}
                  onChange={(e) => onChange({ effort: e.target.value as Effort })}
                >
                  {EFFORTS.map((e) => <option key={e.id} value={e.id}>{e.label}</option>)}
                </select>
              </label>
            )}
          </div>
          {editing && (
            <div
              ref={subRef}
              className="model-menu-params"
              role="dialog"
              aria-label={`${editing} parameters`}
              style={{ top: box?.subTop ?? -9999, left: box?.subLeft ?? 0, visibility: box ? 'visible' : 'hidden' }}
            >
              <div className="model-menu-param-title">{modelLabel(editing)}</div>
              <div className="model-menu-section">Options</div>
              <button
                type="button"
                role="switch"
                aria-checked={fast}
                className="model-menu-switch"
                title="Faster responses. Sends priority processing when the provider supports it."
                onClick={() => applyTo(editing, { fast: !fast })}
              >
                <span>Fast</span>
                <span className={`model-menu-track${fast ? ' on' : ''}`} aria-hidden><span /></span>
              </button>
              {dirty && (
                <button
                  type="button"
                  className="model-menu-reset"
                  onClick={() => applyTo(editing, { ...(editingEffortShown ? { effort: DEFAULT_EFFORT } : {}), fast: false })}
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

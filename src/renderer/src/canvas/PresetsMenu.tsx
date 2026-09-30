import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { LayoutTemplate, Pencil, Trash2 } from 'lucide-react'
import type { CanvasPreset } from '@shared/types'
import { usePresets } from './presets'
import { useCanvas } from './store'
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

/** Name input (defaults to the space's name) + Save. Enter submits, Escape → onDone. */
export function SavePresetForm({ canvasId, onDone }: { canvasId: string; onDone: () => void }): JSX.Element {
  const [name, setName] = useState(() => useCanvas.getState().canvases[canvasId]?.name ?? '')
  const [busy, setBusy] = useState(false)

  const submit = async (): Promise<void> => {
    if (busy) return
    setBusy(true)
    // A blank name is fine: the server falls back to the space's name.
    const p = await usePresets.getState().save(canvasId, name)
    if (p) onDone()
    else setBusy(false)
  }

  return (
    <form
      className="preset-form"
      onSubmit={(e) => {
        e.preventDefault()
        void submit()
      }}
    >
      <input
        autoFocus
        placeholder="Preset name"
        value={name}
        onChange={(e) => setName(e.target.value)}
        onFocus={(e) => e.currentTarget.select()}
        onKeyDown={(e) => {
          if (e.key !== 'Escape') return
          e.stopPropagation()
          onDone()
        }}
      />
      <button type="submit" className="primary-btn" disabled={busy}>Save</button>
    </form>
  )
}

/** One preset: instantiate on click, rename and delete on hover. */
function PresetRow({ preset, onPicked }: { preset: CanvasPreset; onPicked: () => void }): JSX.Element {
  const [editing, setEditing] = useState<string | null>(null)
  const count = preset.windows.length

  const commit = (): void => {
    const next = (editing ?? '').trim()
    setEditing(null)
    if (next && next !== preset.name) void usePresets.getState().rename(preset.id, next)
  }

  return (
    <div className="preset-row" title={`${preset.name} · ${count} window${count === 1 ? '' : 's'}`}>
      {editing === null ? (
        <button
          className="preset-name"
          onClick={() => {
            onPicked()
            void usePresets.getState().instantiate(preset.id)
          }}
        >
          {preset.name}
        </button>
      ) : (
        <input
          autoFocus
          data-own-escape
          value={editing}
          onChange={(e) => setEditing(e.target.value)}
          onBlur={commit}
          onKeyDown={(e) => {
            if (e.key === 'Enter') commit()
            if (e.key === 'Escape') {
              e.stopPropagation()
              setEditing(null)
            }
          }}
        />
      )}
      <span className="count">{count}</span>
      <button className="icon-btn ghost xs" title="Rename preset" onClick={() => setEditing(preset.name)}><Pencil size={11} /></button>
      <button
        className="icon-btn ghost xs danger"
        title="Delete preset"
        onClick={() => {
          if (confirm(`Delete preset "${preset.name}"?`)) void usePresets.getState().remove(preset.id)
        }}
      >
        <Trash2 size={11} />
      </button>
    </div>
  )
}

/** icon-btn ghost sm, <LayoutTemplate size={14}/>, title "Presets". Popover: optional save form + "New space from preset" list. */
export function PresetsButton({ canvasId }: { canvasId: string | null }): JSX.Element {
  // Remembers the space it was opened for: a space switch (⌃N, ⌥⌘→) while it is open closes it, so
  // the save form can never store one space under a name seeded from another.
  const [opened, setOpened] = useState<{ x: number; y: number; canvasId: string | null } | null>(null)
  const stale = !!opened && opened.canvasId !== canvasId
  if (stale) setOpened(null)
  const at = opened && !stale ? opened : null
  const presets = usePresets((s) => s.presets)
  const loaded = usePresets((s) => s.loaded)
  const close = (): void => setOpened(null)

  return (
    <>
      <button
        className={`icon-btn ghost sm${at ? ' on' : ''}`}
        title="Presets"
        onClick={(e) => {
          e.stopPropagation()
          const r = e.currentTarget.getBoundingClientRect()
          setOpened({ x: r.left, y: r.bottom + 4, canvasId })
          void usePresets.getState().load()
        }}
      >
        <LayoutTemplate size={14} />
      </button>
      {at && (
        <Popover at={at} onClose={close}>
          {canvasId && (
            <>
              <h4>Save this space</h4>
              <SavePresetForm key={canvasId} canvasId={canvasId} onDone={close} />
            </>
          )}
          <h4>New space from preset</h4>
          {presets.map((p) => <PresetRow key={p.id} preset={p} onPicked={close} />)}
          {loaded && presets.length === 0 && <p className="empty-hint">No presets yet. Save a space to reuse its layout.</p>}
        </Popover>
      )}
    </>
  )
}

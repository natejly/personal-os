import { useState, type DragEvent } from 'react'
import type { DragKind, DragPayload } from '@shared/types'

/** Custom MIME, so the browser's own drag types never collide with one of ours. Contract §7. */
export const DRAG_MIME = 'application/x-personal-os'

const KINDS: ReadonlySet<string> = new Set<DragKind>([
  'conversation', 'todo', 'document', 'memory', 'project', 'widget', 'note', 'file', 'nav', 'doc', 'desk', 'workflow', 'workflow_run'
])

/** Writes the payload plus a `text/plain` mirror of the label, so a drag also drops into a textarea. */
export const writeDrag = (dt: DataTransfer, payload: DragPayload): void => {
  dt.setData(DRAG_MIME, JSON.stringify(payload))
  dt.setData('text/plain', payload.label)
  dt.effectAllowed = 'copy'
}

/** Everything a drag source needs: `<div {...dragProps(payload)}>`. */
export const dragProps = (payload: DragPayload): { draggable: true; onDragStart: (e: DragEvent<HTMLElement>) => void } => ({
  draggable: true,
  onDragStart: (e) => {
    e.stopPropagation()
    writeDrag(e.dataTransfer, payload)
  }
})

/**
 * Foreign drags — files, text, another app entirely — all land here, so every failure is a `null`
 * and never a throw. An unknown `kind` is foreign too: only the frozen `DragKind` union gets through.
 */
export const readDrag = (dt: DataTransfer): DragPayload | null => {
  let raw = ''
  try {
    raw = dt.getData(DRAG_MIME)
  } catch {
    return null
  }
  if (!raw) return null
  try {
    const p = JSON.parse(raw) as DragPayload
    if (!p || typeof p !== 'object' || !KINDS.has(p.kind) || typeof p.id !== 'string') return null
    return { ...p, label: typeof p.label === 'string' ? p.label : '' }
  } catch {
    return null
  }
}

/** `dragover` runs in protected mode where the payload is unreadable, so acceptance goes by `types`. */
export const hasDrag = (dt: DataTransfer): boolean => dt.types.includes(DRAG_MIME)
export const hasFiles = (dt: DataTransfer): boolean => dt.types.includes('Files')

/** `payload` is null for an OS file drop; read the files off `e.dataTransfer.files`. */
export type DropHandler = (payload: DragPayload | null, e: DragEvent<HTMLElement>) => void

export interface DropContext {
  /** true while an accepted drag is over the target: put `drop-over` on the widget root */
  over: boolean
  handlers: {
    onDragOver: (e: DragEvent<HTMLElement>) => void
    onDragLeave: (e: DragEvent<HTMLElement>) => void
    onDrop: (e: DragEvent<HTMLElement>) => void
  }
}

/**
 * A widget's drop target. `accepts` is its `WidgetDef.accepts`, and a payload of any other kind falls
 * through untouched — the handlers stop propagation only for a drag this target wants, so the plane
 * keeps its own drop behaviour for everything else.
 */
export const useDropTarget = (accepts: DragKind[] | undefined, onDrop: DropHandler): DropContext => {
  const [over, setOver] = useState(false)
  const kinds = accepts ?? []
  const wants = (dt: DataTransfer): boolean => (kinds.length > 0 && hasDrag(dt)) || (kinds.includes('file') && hasFiles(dt))
  return {
    over,
    handlers: {
      onDragOver: (e) => {
        if (!wants(e.dataTransfer)) return
        e.preventDefault()
        e.stopPropagation()
        e.dataTransfer.dropEffect = 'copy'
        if (!over) setOver(true)
      },
      // dragleave also fires crossing into a child, which must not clear the highlight.
      onDragLeave: (e) => {
        const to = e.relatedTarget as Node | null
        if (!to || !e.currentTarget.contains(to)) setOver(false)
      },
      onDrop: (e) => {
        if (!wants(e.dataTransfer)) return
        setOver(false)
        const p = readDrag(e.dataTransfer)
        if (!p) {
          if (!kinds.includes('file') || !e.dataTransfer.files.length) return
        } else if (!kinds.includes(p.kind)) {
          return
        }
        e.preventDefault()
        e.stopPropagation()
        onDrop(p, e)
      }
    }
  }
}

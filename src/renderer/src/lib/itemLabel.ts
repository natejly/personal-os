/**
 * Human names for the ids a tool call carries. A call that acts on an email, event, task or file by id is shown as
 * "Re: offer · Jack Smith · Oct 11", never the id. The name comes from what earlier tool results in this session
 * already returned for that id, else from the backend's read-only /labels lookup; a miss keeps the id.
 */
import { useEffect, useState } from 'react'
import type { ToolEvent } from '@shared/types'
import { req } from './api'

export type ItemKind = 'message' | 'event' | 'task' | 'todo' | 'doc'
export interface Target { kind: ItemKind; id: string; key: string; calendarId?: string }

/** tool -> [kind, argument holding the id]. Mirrors labels.TARGETS in the backend. */
const TARGETS: Record<string, [ItemKind, string]> = {
  gmail_read: ['message', 'message_id'], gmail_modify: ['message', 'message_id'],
  gmail_send: ['message', 'reply_to_message_id'], gmail_draft: ['message', 'reply_to_message_id'],
  calendar_get: ['event', 'event_id'], calendar_update: ['event', 'event_id'],
  calendar_delete: ['event', 'event_id'], calendar_respond: ['event', 'event_id'],
  google_tasks_complete: ['task', 'task_id'],
  todo_update: ['todo', 'id'], todo_delete: ['todo', 'id'],
  doc_read: ['doc', 'doc'], doc_edit: ['doc', 'doc'], doc_delete: ['doc', 'doc'],
  doc_comments: ['doc', 'doc'], doc_comment_reply: ['doc', 'doc']
}

export function targetOf(name: string, args: Record<string, unknown> | null | undefined): Target | null {
  const t = TARGETS[name]
  const id = t && args?.[t[1]]
  if (!t || typeof id !== 'string' || !id.trim()) return null
  const cal = args?.calendar_id
  return { kind: t[0], id, key: t[1], calendarId: typeof cal === 'string' ? cal : undefined }
}

const cacheKey = (kind: string, id: string): string => `${kind}:${id}`
const KNOWN = new Map<string, string>()

const sender = (from: string): string => /^\s*"?([^"<]+?)"?\s*</.exec(from)?.[1]?.trim() || from.replace(/[<>]/g, '').trim()
const shortDate = (iso: unknown): string => {
  const d = new Date(String(iso ?? ''))
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}
const when = (start: unknown, end: unknown): string => {
  const s = new Date(String(start ?? ''))
  if (Number.isNaN(s.getTime())) return ''
  const day = s.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' })
  if (String(start).length === 10) return `${day} (all day)`
  const t = (d: Date): string => d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })
  const e = new Date(String(end ?? ''))
  return Number.isNaN(e.getTime()) || e.toDateString() !== s.toDateString() ? `${day}, ${t(s)}` : `${day}, ${t(s)} – ${t(e)}`
}
const join = (...p: Array<string | false | undefined>): string => p.filter(Boolean).join(' · ')

/** Every row in a tool result that names itself next to its id: mail (subject), events (summary + start), tasks and todos (title), files (doc_id + title). */
export function learnLabels(events: ToolEvent[]): void {
  const walk = (v: unknown, depth: number): void => {
    if (depth > 4 || !v || typeof v !== 'object') return
    if (Array.isArray(v)) { for (const x of v.slice(0, 200)) walk(x, depth + 1); return }
    const o = v as Record<string, unknown>
    const str = (k: string): string => (typeof o[k] === 'string' ? (o[k] as string) : '')
    if (str('doc_id') && str('title')) KNOWN.set(cacheKey('doc', str('doc_id')), str('title'))
    else if (str('id') && str('subject')) KNOWN.set(cacheKey('message', str('id')), join(str('subject'), str('from') && sender(str('from')), shortDate(o.date)))
    else if (str('id') && str('summary') && o.start) KNOWN.set(cacheKey('event', str('id')), join(str('summary'), when(o.start, o.end)))
    else if (str('id') && str('title') && ('due' in o || 'status' in o || 'done' in o)) {
      const label = join(str('title'), str('due') && `due ${shortDate(str('due'))}`)
      KNOWN.set(cacheKey('task', str('id')), label)
      KNOWN.set(cacheKey('todo', str('id')), label)
    }
    for (const x of Object.values(o)) if (x && typeof x === 'object') walk(x, depth + 1)
  }
  for (const e of events) {
    if (e.error || !e.result_preview || !/^\s*[[{]/.test(e.result_preview)) continue
    try { walk(JSON.parse(e.result_preview), 0) } catch { /* a truncated preview: nothing to learn */ }
  }
}

/** What is already known for a target, without asking the backend. */
export const knownLabel = (t: Target): string | undefined => KNOWN.get(cacheKey(t.kind, t.id))

const ASKED = new Map<string, Promise<string | null>>()
function ask(t: Target): Promise<string | null> {
  const k = cacheKey(t.kind, t.id)
  let p = ASKED.get(k)
  if (!p) {
    const q = `/labels?kind=${t.kind}&id=${encodeURIComponent(t.id)}${t.calendarId ? `&calendar_id=${encodeURIComponent(t.calendarId)}` : ''}`
    p = req<{ label: string | null }>(q).then((r) => r.label).catch(() => null)
    p.then((l) => { if (l) KNOWN.set(k, l); else ASKED.delete(k) })
    ASKED.set(k, p)
  }
  return p
}

/** The human name of the item a call acts on, or null while unknown (callers then show what they showed before). */
export function useItemLabel(name: string, args: Record<string, unknown> | null | undefined): string | null {
  const t = targetOf(name, args)
  const [label, setLabel] = useState<string | null>(() => (t ? knownLabel(t) ?? null : null))
  const sig = t ? cacheKey(t.kind, t.id) : ''
  useEffect(() => {
    if (!t) { setLabel(null); return }
    const known = knownLabel(t)
    if (known) { setLabel(known); return }
    let live = true
    void ask(t).then((l) => { if (live) setLabel(l) })
    return () => { live = false }
  }, [sig])  // eslint-disable-line react-hooks/exhaustive-deps
  return label
}

/** The arguments with the id swapped for its name, for the labelled argument list. */
export function withLabel(args: Record<string, unknown>, name: string, label: string | null): Record<string, unknown> {
  const t = label ? targetOf(name, args) : null
  return t ? { ...args, [t.key]: label } : args
}

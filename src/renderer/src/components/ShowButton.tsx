import { createContext, useContext, useState } from 'react'
import { Check, LayoutDashboard, PanelRight } from '../icons'
import type { ShowItem } from '@shared/types'
import { useStore } from '../store'
import { fromFence } from '../lib/showPanel'
import { api } from '../lib/api'
import { parseJsonLoose } from '../lib/chartRepair'

/** The conversation a rendered reply belongs to, so a fenced block in it can be promoted to that chat's side panel. */
export const ShowCtx = createContext<string | null>(null)

/** "Open in panel" on a fenced block's header. Nothing outside a chat (a doc preview) gets the button. */
export default function OpenInPanel({ kind, source }: { kind: Exclude<ShowItem['kind'], 'file' | 'dashboard'>; source: string }): JSX.Element | null {
  const conversationId = useContext(ShowCtx)
  const openShow = useStore((s) => s.openShow)
  if (!conversationId) return null
  return (
    <button className="icon-btn ghost" title="Open in side panel" aria-label="Open in side panel" onClick={() => openShow(conversationId, fromFence(kind, source))}>
      <PanelRight size={12} />
    </button>
  )
}

/** "Save dashboard" on a fenced block's header: asks for a title inline (window.prompt does not work in Electron), then saves it as a live dashboard. */
export function SaveDashboard({ kind, source }: { kind: 'chart' | 'interactive' | 'html'; source: string }): JSX.Element | null {
  const conversationId = useContext(ShowCtx)
  const [title, setTitle] = useState<string | null>(null)
  const [state, setState] = useState<'' | 'saving' | 'saved' | 'error'>('')
  if (!conversationId) return null
  const save = (): void => {
    const t = (title ?? '').trim()
    if (!t) return
    let body: Parameters<typeof api.savedDashboards.create>[0]
    try {
      const project_id = useStore.getState().sessions[conversationId]?.conversation?.project_id ?? null
      body = kind === 'html' ? { title: t, format: 'html', mode: 'live', html: source, project_id } : { title: t, format: 'spec', mode: 'live', views: [{ kind, spec: parseJsonLoose(source) as Record<string, unknown> }], project_id }
    } catch { setState('error'); return }
    setState('saving')
    api.savedDashboards.create(body).then(() => { setState('saved'); setTitle(null); setTimeout(() => setState(''), 2500) }).catch(() => setState('error'))
  }
  if (title !== null) {
    return (
      <span className="art-fence-actions">
        <input className="show-pick" autoFocus aria-label="Dashboard title" placeholder="Dashboard title" value={title}
          onChange={(e) => setTitle(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') save(); if (e.key === 'Escape') setTitle(null) }} />
        <button className="link small" disabled={!title.trim() || state === 'saving'} onClick={save}>Save</button>
      </span>
    )
  }
  return state === 'saved'
    ? <span className="muted small"><Check size={12} /> Saved to Dashboards</span>
    : (
      <button className="icon-btn ghost" title={state === 'error' ? 'Could not save, try again' : 'Save dashboard'} aria-label="Save dashboard" onClick={() => { setState(''); setTitle('') }}>
        <LayoutDashboard size={12} />
      </button>
    )
}

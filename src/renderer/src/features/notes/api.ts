import type { FullDoc } from '@shared/types'
import { req, json, getBase, getToken } from '../../lib/api'
import { isoDate } from './dates'

export interface Backlink {
  id: string
  title: string
  folder: string
  project_id: string | null
  /** The line that holds the link, clipped by the server. */
  snippet: string
  updated_at: number
}

/**
 * Today's daily note, created on first ask. The date is the renderer's LOCAL day: the backend runs on
 * the same machine but should not guess a timezone, and a note opened at 11pm belongs to that day.
 */
export const daily = (date: Date | string = new Date()): Promise<{ doc: FullDoc; created: boolean }> =>
  req('/docs/daily', { method: 'POST', body: json({ date: typeof date === 'string' ? date : isoDate(date) }) })

export const backlinks = (id: string): Promise<Backlink[]> => req(`/docs/${encodeURIComponent(id)}/backlinks`)

/** Store a pasted image under the doc and get back the relative URL to put in `![](url)`. */
export const uploadDocAsset = (id: string, file: File): Promise<{ url: string }> => {
  const fd = new FormData()
  fd.append('file', file, file.name || 'image')
  return req(`/docs/${encodeURIComponent(id)}/assets`, { method: 'POST', body: fd })
}

export const linkTitle = (url: string): Promise<{ title: string | null }> =>
  req('/docs/link-title', { method: 'POST', body: json({ url }) })

/** An authed GET of a backend path as an object URL (the caller revokes it); an <img> cannot send the app token. */
export async function fetchBlobUrl(path: string): Promise<string> {
  const t = getToken()
  const r = await fetch(`${getBase()}${path}`, { headers: t ? { 'X-Personal-OS-Token': t } : {} })
  if (!r.ok) throw new Error(`${r.status}`)
  return URL.createObjectURL(await r.blob())
}

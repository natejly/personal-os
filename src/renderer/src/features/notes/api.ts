import type { FullDoc } from '@shared/types'
import { req, json } from '../../lib/api'
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

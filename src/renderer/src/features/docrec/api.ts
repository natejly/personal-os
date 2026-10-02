import { api, json, req } from '../../lib/api'
import type { DocRecording, DocRecordingMode, DocRevision, FullMeeting } from '@shared/types'

/**
 * Typed wrappers for the doc-recording routes, plus the existing meeting calls the doc UI reuses.
 * A doc recording is a `meetings` row with a `doc_id`, so everything past creation (stop, pause,
 * segments, actions, delete) is the unchanged meetings API.
 */

export interface SummarizeBody {
  template?: string
  focus?: string
  /** Re-run even though a summary already exists. */
  force?: boolean
}
export interface SummarizeResult {
  meeting: FullMeeting
  /** The proposed doc revision; null when generation failed. */
  revision: DocRevision | null
  error: string | null
}

export const docRecApi = {
  /** Creates a recording linked to the doc and starts it. 409 carries the blockers, and a refused
   *  start leaves no row behind. */
  start: (docId: string, body: { mode?: DocRecordingMode; template?: string; title?: string } = {}) =>
    req<FullMeeting>(`/docs/${docId}/recordings`, { method: 'POST', body: json(body) }),
  list: (docId: string) => req<DocRecording[]>(`/docs/${docId}/recordings`),
  /** A linked row that is not started, as the target for an audio import. */
  createLinked: (docId: string, title: string, mode: DocRecordingMode = 'record') =>
    req<FullMeeting>('/meetings', { method: 'POST', body: json({ title, doc_id: docId, doc_mode: mode }) }),
  summarize: (meetingId: string, body: SummarizeBody = {}) =>
    req<SummarizeResult>(`/meetings/${meetingId}/summarize`, { method: 'POST', body: json(body) }),
  /** The existing import: 202, and progress arrives through the segments poll. */
  importAudio: api.meetings.importAudio,
  // Re-exports of the meeting calls this feature uses, so components import from one place.
  getDoc: api.docs.get,
  get: api.meetings.get,
  stop: api.meetings.stop,
  pause: api.meetings.pause,
  resume: api.meetings.resume,
  segments: api.meetings.segments,
  patch: api.meetings.patch,
  del: api.meetings.del,
  deleteAudio: api.meetings.deleteAudio,
  retranscribe: api.meetings.retranscribe,
  actions: api.meetings.actions,
  addTodos: api.meetings.addTodos,
  status: api.meetings.status
}

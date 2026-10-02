import { create } from 'zustand'
import type { DocRecording, DocRecordingMode, FullMeeting, MeetingActionItem, MeetingSegment, RecordingEvent } from '@shared/types'
import { consentResume, useStore } from '../../store'
import { fetchSegmentPages } from '../../lib/transcript'
import { docRecApi, type SummarizeBody } from './api'
import { forgetDictation } from './dictation'
import { usePreview } from './preview'
import { startRefusal, type BlockerAction } from './blockers'
import { blockInsertText, recordingBlockLabel, recordingBlockLine } from './recordingBlock'
import { foldSegments, isSettled, liveDoc, needsFullReload, settleDone } from './segments'

/**
 * Doc recordings: the recordings of each doc, the one being read, its transcript, and the live
 * state of whichever recording is running.
 *
 * Why a store of its own: the main store's meeting slice has ONE active-meeting slot and its
 * actions switch the view to Meetings. A recording that belongs to a note must not move the user
 * off the note. What IS shared is the recorder's truth: `meetingStatus.active` in the main store
 * says whether anything is recording and which doc it belongs to, so this store derives its live
 * state from there rather than keeping a second copy that could disagree.
 *
 * Two sources keep the transcript current, and neither is trusted alone. The app-wide `recording`
 * event is fast but a stream can drop; the 2 s poll is slow but always there. A clip's text arrives
 * by UPDATE, so a `?since=` poll never redelivers it once its row has been seen unfinished: the poll
 * reloads the whole tail while any held row is unsettled (see `needsFullReload`).
 */

const POLL_MS = 2000
/** After Stop, how long to keep looking for the late transcript and the summary before giving up. */
const SETTLE_MS = 3 * 60 * 1000

/** A stopped (or imported) recording whose transcript and summary are still being produced. */
export interface Settling {
  meetingId: string
  docId: string
  until: number
  /** A summary is expected: record mode with summaries on. Dictation never makes one. */
  wantSummary: boolean
  /** A `summary` event (success or failure) or a row that moved off `none` has been seen. */
  summarySeen: boolean
}

export interface Notice { text: string; action: BlockerAction }

interface DocRecState {
  recordings: Record<string, DocRecording[]>
  /** Which recording each doc is showing; null until one is picked. */
  selectedId: Record<string, string | null>
  segments: Record<string, MeetingSegment[]>
  meetings: Record<string, FullMeeting>
  actions: Record<string, MeetingActionItem[]>
  summarizing: Record<string, boolean>
  summaryError: Record<string, string | null>
  settling: Settling | null
  /** A start, stop or import request is in flight. */
  busy: boolean
  /** Why the last start was refused, in plain words. */
  notice: Notice | null

  load: (docId: string) => Promise<void>
  select: (docId: string, meetingId: string | null) => Promise<void>
  start: (docId: string, mode: DocRecordingMode, opts?: { template?: string; title?: string }) => Promise<void>
  stop: () => Promise<void>
  pause: () => Promise<void>
  resume: () => Promise<void>
  summarize: (meetingId: string, opts?: SummarizeBody) => Promise<void>
  remove: (meetingId: string) => Promise<void>
  rename: (meetingId: string, title: string) => Promise<void>
  deleteAudio: (meetingId: string) => Promise<void>
  retranscribe: (meetingId: string) => Promise<void>
  importAudio: (docId: string, file: File) => Promise<void>
  addTodos: (meetingId: string, ids?: string[]) => Promise<void>
  handleEvent: (ev: RecordingEvent) => void
  dismissNotice: () => void
  /** Start the poll if anything needs it; it stops itself when nothing does. */
  kick: () => void
}

/** The doc whose panel is open, so the poll refreshes its list even when nothing is live. */
let currentDoc: string | null = null
let timer: ReturnType<typeof setInterval> | null = null
let ticking = false
/** Per-recording `?since=` cursor for the cheap incremental poll. */
const cursors = new Map<string, number>()
/** Recordings a `summary` event has been seen for. The event can land before Stop's response does,
 *  i.e. before `beginSettling`, so it is remembered here rather than only on `settling`. */
const summaryEvents = new Set<string>()

/** The open editor's way to take a line at its caret (DocsView registers it); null when none is mounted. */
type BlockSink = (docId: string, make: (before: string, after: string) => string) => void
let blockSink: BlockSink | null = null
export const setRecordingBlockSink = (s: BlockSink | null): void => { blockSink = s }

const message = (e: unknown): string => (e instanceof Error ? e.message : String(e))

export const useDocRec = create<DocRecState>((set, get) => {
  const toast = (text: string, kind?: 'info' | 'error'): void => useStore.getState().toast(text, kind)

  const patchMap = <K extends keyof DocRecState>(key: K, id: string, value: DocRecState[K] extends Record<string, infer V> ? V : never): void =>
    set((st) => ({ [key]: { ...(st[key] as Record<string, unknown>), [id]: value } } as unknown as Partial<DocRecState>))

  /** The row's own count, so a held tail that is short of it is reloaded. */
  const countOf = (docId: string | null, meetingId: string): number | null => {
    const row = docId ? get().recordings[docId]?.find((r) => r.id === meetingId) : undefined
    return row ? row.segment_count : null
  }

  const refreshList = async (docId: string): Promise<void> => {
    try {
      const rows = await docRecApi.list(docId)
      set((st) => ({ recordings: { ...st.recordings, [docId]: rows } }))
    } catch { /* the list is supplementary; the next tick retries */ }
  }

  const refreshMeeting = async (meetingId: string): Promise<FullMeeting | null> => {
    try {
      const m = await docRecApi.get(meetingId)
      patchMap('meetings', meetingId, m)
      return m
    } catch {
      return null
    }
  }

  const loadActions = async (meetingId: string): Promise<void> => {
    try { patchMap('actions', meetingId, await docRecApi.actions(meetingId)) } catch { /* shown empty */ }
  }

  /**
   * Bring one recording's held transcript up to date: the whole tail while anything held is
   * unsettled (or short of the row's count), else only what is past the cursor.
   */
  const syncSegments = async (docId: string | null, meetingId: string): Promise<void> => {
    const held = get().segments[meetingId] ?? []
    try {
      if (held.length === 0 || needsFullReload(held, countOf(docId, meetingId))) {
        const page = await fetchSegmentPages((since, limit) => docRecApi.segments(meetingId, since, limit))
        cursors.set(meetingId, page.cursor)
        set((st) => ({ segments: { ...st.segments, [meetingId]: foldSegments(st.segments[meetingId] ?? [], page.segments) } }))
        return
      }
      const rows = await docRecApi.segments(meetingId, cursors.get(meetingId) ?? 0, 200)
      if (rows.length === 0) return
      cursors.set(meetingId, rows.reduce((n, r) => Math.max(n, r.cursor ?? 0), cursors.get(meetingId) ?? 0))
      set((st) => ({ segments: { ...st.segments, [meetingId]: foldSegments(st.segments[meetingId] ?? [], rows) } }))
    } catch { /* a flaky poll is not worth a toast */ }
  }

  /**
   * Make a freshly proposed summary visible without a manual reload. Only `pending` is adopted into
   * the open doc: its content may carry keystrokes the user made since, and a proposal never
   * changes the content (accepting it is what appends). The draft is flushed first so the server
   * and the editor agree before the revision is reviewed against them.
   */
  const refreshDocAfterSummary = async (docId: string): Promise<void> => {
    const main = useStore.getState()
    try {
      await main.flushDoc()
      const fresh = await docRecApi.getDoc(docId)
      useStore.setState((st) => (st.activeDoc?.id === docId ? { activeDoc: { ...st.activeDoc, pending: fresh.pending } } : {}))
      await Promise.all([main.refreshDocRevisions(docId), main.refreshDocsPending()])
    } catch { /* the badge catches up on the next refresh */ }
  }

  const finishSettling = async (s: Settling): Promise<void> => {
    set({ settling: null })
    await Promise.all([refreshList(s.docId), refreshMeeting(s.meetingId), loadActions(s.meetingId), syncSegments(s.docId, s.meetingId)])
    await refreshDocAfterSummary(s.docId)
  }

  const beginSettling = (meetingId: string, docId: string, mode: DocRecordingMode | null): void => {
    const wantSummary = mode !== 'dictate' && useStore.getState().meetingStatus?.config.enhanceOnStop !== false
    set({ settling: { meetingId, docId, until: Date.now() + SETTLE_MS, wantSummary, summarySeen: summaryEvents.has(meetingId) } })
    get().kick()
  }

  const tick = async (): Promise<void> => {
    if (ticking) return
    ticking = true
    try {
      const main = useStore.getState()
      const settling = get().settling
      // The recorder's own status is only refreshed by the main store while something is live in
      // ITS view; here it is the source of the live bar, so it is re-read on every tick.
      if (liveDoc(main.meetingStatus) || settling) await main.refreshMeetingStatus()
      const live = liveDoc(useStore.getState().meetingStatus)

      const docs = new Set<string>()
      if (currentDoc) docs.add(currentDoc)
      if (live) docs.add(live.docId)
      if (settling) docs.add(settling.docId)
      await Promise.all([...docs].map(refreshList))

      const ids = new Map<string, string | null>()
      if (currentDoc) {
        const sel = get().selectedId[currentDoc]
        if (sel) ids.set(sel, currentDoc)
      }
      if (live) ids.set(live.meetingId, live.docId)
      if (settling) ids.set(settling.meetingId, settling.docId)
      await Promise.all([...ids].map(([m, d]) => syncSegments(d, m)))

      if (settling) {
        const row = get().recordings[settling.docId]?.find((r) => r.id === settling.meetingId)
        const m = await refreshMeeting(settling.meetingId)
        const held = get().segments[settling.meetingId] ?? []
        const summarySeen = get().settling?.summarySeen || (row !== undefined && row.summary_state !== 'none')
        if (settleDone({
          stillLive: live?.meetingId === settling.meetingId,
          held,
          rowCount: row ? row.segment_count : null,
          meetingStatus: m?.status ?? null,
          meetingError: m?.error ?? '',
          wantSummary: settling.wantSummary,
          summarySeen
        }) || Date.now() > settling.until) {
          await finishSettling(settling)
        }
      }

      // Stop ticking once nothing is live, settling, or still waiting on a clip's text.
      const sel = currentDoc ? get().selectedId[currentDoc] : null
      const unsettled = sel ? (get().segments[sel] ?? []).some((s) => !isSettled(s)) : false
      if (!liveDoc(useStore.getState().meetingStatus) && !get().settling && !unsettled && timer) {
        clearInterval(timer)
        timer = null
      }
    } finally {
      ticking = false
    }
  }

  const loading = new Map<string, Promise<void>>()
  const loadNow = async (docId: string): Promise<void> => {
    currentDoc = docId
    await refreshList(docId)
    const rows = get().recordings[docId] ?? []
    const live = liveDoc(useStore.getState().meetingStatus)
    const sel = get().selectedId[docId]
    // Land on the live recording, else keep the user's pick, else the newest.
    const pick = live?.docId === docId ? live.meetingId : sel && rows.some((r) => r.id === sel) ? sel : rows[0]?.id ?? null
    await get().select(docId, pick)
    get().kick()
  }

  return {
    recordings: {},
    selectedId: {},
    segments: {},
    meetings: {},
    actions: {},
    summarizing: {},
    summaryError: {},
    settling: null,
    busy: false,
    notice: null,

    kick: () => {
      if (!timer) timer = setInterval(() => void tick(), POLL_MS)
    },

    dismissNotice: () => set({ notice: null }),

    load: (docId) => {
      // The button, the bar and the panel all mount this for the same doc at once; one fetch serves them.
      const live = loading.get(docId)
      if (live) return live
      const p = loadNow(docId).finally(() => loading.delete(docId))
      loading.set(docId, p)
      return p
    },

    select: async (docId, meetingId) => {
      set((st) => ({ selectedId: { ...st.selectedId, [docId]: meetingId } }))
      if (!meetingId) return
      // Opening a recording reloads its tail from zero: the cursor cannot be trusted for rows that
      // changed by UPDATE, and a reopened recording may have been retranscribed.
      cursors.delete(meetingId)
      await Promise.all([refreshMeeting(meetingId), loadActions(meetingId), syncSegments(docId, meetingId)])
    },

    start: async (docId, mode, opts) => {
      if (get().busy) return
      set({ busy: true, notice: null })
      try {
        // The draft goes first so what was typed before Record is on the server, not in a buffer.
        await useStore.getState().flushDoc()
        const m = await docRecApi.start(docId, { mode, template: opts?.template, title: opts?.title })
        cursors.delete(m.id)
        set((st) => ({
          segments: { ...st.segments, [m.id]: [] },
          meetings: { ...st.meetings, [m.id]: m },
          selectedId: { ...st.selectedId, [docId]: m.id }
        }))
        currentDoc = docId
        // The anchor for this recording's transcript and summary; dictation types text instead.
        if (mode === 'record') {
          const line = recordingBlockLine(m.id, recordingBlockLabel(m))
          blockSink?.(docId, (before, after) => blockInsertText(line, before, after))
        }
        await Promise.all([useStore.getState().refreshMeetingStatus(), refreshList(docId)])
        get().kick()
      } catch (e) {
        const refusal = startRefusal(message(e))
        if (refusal.action === 'consent') {
          // Park the start, and let the existing consent modal resume it as a doc recording.
          consentResume.run = () => void get().start(docId, mode, opts)
          useStore.getState().setMeetingConsentOpen(true)
        } else {
          set({ notice: { text: refusal.text, action: refusal.action } })
          toast(refusal.text, 'error')
        }
      } finally {
        set({ busy: false })
      }
    },

    stop: async () => {
      const live = liveDoc(useStore.getState().meetingStatus)
      if (!live || get().busy) return
      set({ busy: true })
      try {
        // Blocks while the transcription backlog drains, so the button stays disabled meanwhile.
        const m = await docRecApi.stop(live.meetingId)
        patchMap('meetings', live.meetingId, m)
        beginSettling(live.meetingId, live.docId, live.mode)
        await Promise.all([useStore.getState().refreshMeetingStatus(), refreshList(live.docId), syncSegments(live.docId, live.meetingId)])
      } catch (e) {
        toast(message(e), 'error')
        void useStore.getState().refreshMeetingStatus()
      } finally {
        set({ busy: false })
      }
    },

    pause: async () => {
      const live = liveDoc(useStore.getState().meetingStatus)
      if (!live) return
      try {
        await docRecApi.pause(live.meetingId)
        await useStore.getState().refreshMeetingStatus()
      } catch (e) { toast(message(e), 'error') }
    },

    resume: async () => {
      const live = liveDoc(useStore.getState().meetingStatus)
      if (!live) return
      try {
        await docRecApi.resume(live.meetingId)
        await useStore.getState().refreshMeetingStatus()
      } catch (e) { toast(message(e), 'error') }
    },

    summarize: async (meetingId, opts = {}) => {
      if (get().summarizing[meetingId]) return
      const docId = get().meetings[meetingId]?.doc_id ?? currentDoc
      patchMap('summarizing', meetingId, true)
      patchMap('summaryError', meetingId, null)
      try {
        // Whatever was typed goes to the server first: the proposal appends to the doc as it stands.
        await useStore.getState().flushDoc()
        const res = await docRecApi.summarize(meetingId, opts)
        patchMap('meetings', meetingId, res.meeting)
        patchMap('summaryError', meetingId, res.error)
        if (res.error) toast(`Summary failed: ${res.error}`, 'error')
        if (docId) {
          await refreshList(docId)
          await refreshDocAfterSummary(docId)
        }
      } catch (e) {
        patchMap('summaryError', meetingId, message(e))
        toast(message(e), 'error')
      } finally {
        patchMap('summarizing', meetingId, false)
      }
    },

    remove: async (meetingId) => {
      const docId = get().meetings[meetingId]?.doc_id ?? currentDoc
      try {
        await docRecApi.del(meetingId)
        cursors.delete(meetingId)
        forgetDictation(meetingId)
        set((st) => {
          const { [meetingId]: _s, ...segments } = st.segments
          const { [meetingId]: _m, ...meetings } = st.meetings
          const next = { segments, meetings } as Partial<DocRecState>
          if (docId && st.selectedId[docId] === meetingId) next.selectedId = { ...st.selectedId, [docId]: null }
          return next
        })
        if (docId) {
          await refreshList(docId)
          const rows = get().recordings[docId] ?? []
          if (!get().selectedId[docId] && rows[0]) await get().select(docId, rows[0].id)
          await refreshDocAfterSummary(docId)
        }
      } catch (e) { toast(message(e), 'error') }
    },

    rename: async (meetingId, title) => {
      const t = title.trim()
      if (!t) return
      try {
        const m = await docRecApi.patch(meetingId, { title: t })
        patchMap('meetings', meetingId, m)
        if (m.doc_id) await refreshList(m.doc_id)
      } catch (e) { toast(message(e), 'error') }
    },

    deleteAudio: async (meetingId) => {
      try { patchMap('meetings', meetingId, await docRecApi.deleteAudio(meetingId)) } catch (e) { toast(message(e), 'error') }
    },

    retranscribe: async (meetingId) => {
      set({ busy: true })
      try {
        const r = await docRecApi.retranscribe(meetingId)
        patchMap('meetings', meetingId, r.meeting)
        cursors.delete(meetingId)
        await syncSegments(r.meeting.doc_id, meetingId)
        get().kick()
      } catch (e) { toast(message(e), 'error') } finally { set({ busy: false }) }
    },

    importAudio: async (docId, file) => {
      if (get().busy) return
      set({ busy: true })
      try {
        await useStore.getState().flushDoc()
        const title = useStore.getState().activeDoc?.id === docId ? useStore.getState().activeDoc?.title ?? file.name : file.name
        const row = await docRecApi.createLinked(docId, title)
        await docRecApi.importAudio(row.id, file)
        set((st) => ({ selectedId: { ...st.selectedId, [docId]: row.id }, segments: { ...st.segments, [row.id]: [] } }))
        currentDoc = docId
        await refreshList(docId)
        beginSettling(row.id, docId, 'record')
      } catch (e) {
        toast(message(e), 'error')
      } finally {
        set({ busy: false })
      }
    },

    addTodos: async (meetingId, ids = []) => {
      try {
        patchMap('actions', meetingId, await docRecApi.addTodos(meetingId, ids))
        void useStore.getState().refreshTodos()
        toast('Added to your todos')
      } catch (e) { toast(message(e), 'error') }
    },

    handleEvent: (ev) => {
      // Ordinary meetings are the Meetings view's business; only doc-linked recordings land here.
      if (!ev.doc_id) return
      const docId = ev.doc_id
      if (ev.kind === 'segment' && ev.segment) {
        const seg = ev.segment
        usePreview.getState().settle(ev.meeting_id, seg.t_end)
        set((st) => ({ segments: { ...st.segments, [ev.meeting_id]: foldSegments(st.segments[ev.meeting_id] ?? [], [seg]) } }))
        return
      }
      if (ev.kind === 'status') {
        void useStore.getState().refreshMeetingStatus()
        void refreshList(docId)
        get().kick()
        return
      }
      if (ev.kind === 'summary') {
        summaryEvents.add(ev.meeting_id)
        const cur = get().settling
        if (cur && cur.meetingId === ev.meeting_id) set({ settling: { ...cur, summarySeen: true } })
        patchMap('summaryError', ev.meeting_id, ev.error ?? null)
        void Promise.all([refreshList(docId), refreshMeeting(ev.meeting_id), loadActions(ev.meeting_id)])
          .then(() => refreshDocAfterSummary(docId))
      }
    }
  }
})

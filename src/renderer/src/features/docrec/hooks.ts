import { useEffect, useRef, useState } from 'react'
import type { MeetingStatusInfo } from '@shared/types'
import { useStore } from '../../store'
import { useDocRec } from './store'
import { dictationDrained, dictationText, dictationsFor, forgetDictation, planDictation, trackDictation } from './dictation'
import { liveDoc } from './segments'
import { diffTouchedLines } from './noteMarks'
import { api } from '../../lib/api'

/** How long after a dictation stops its last clips can still arrive and be typed. */
const DICTATION_TAIL_MS = 120_000

/**
 * Keep a doc's recordings loaded and the poll running while it is on screen. Mount once per doc
 * view (the record button and the panel both call it; the store de-duplicates).
 */
export function useDocRecSync(docId: string): void {
  const liveId = useStore((s) => liveDoc(s.meetingStatus)?.meetingId ?? '')
  useEffect(() => {
    void useStore.getState().refreshMeetingStatus()
    void useDocRec.getState().load(docId)
  }, [docId])
  // A recording that starts or ends from somewhere else (another window, the Meetings view) still
  // needs the poll, so the live bar and the transcript follow it.
  useEffect(() => {
    if (liveId) useDocRec.getState().kick()
  }, [liveId])
}

/**
 * Elapsed seconds that advance every second between polls. The poll only reports `elapsed_ms`
 * every 2 s, so the number is anchored to each report and extended locally; a paused recorder does
 * not extend it.
 */
export function useElapsed(active: NonNullable<MeetingStatusInfo['active']> | null): number {
  const anchor = useRef({ ms: 0, at: Date.now() })
  const [, force] = useState(0)
  if (active && anchor.current.ms !== active.elapsed_ms) anchor.current = { ms: active.elapsed_ms, at: Date.now() }
  const running = !!active && !active.paused
  useEffect(() => {
    if (!running) return
    const t = setInterval(() => force((n) => n + 1), 1000)
    return () => clearInterval(t)
  }, [running])
  if (!active) return 0
  return (anchor.current.ms + (active.paused ? 0 : Date.now() - anchor.current.at)) / 1000
}

/**
 * Type each finalized dictation clip at the caret, once.
 *
 * `insert` receives the text to type (already spaced and capitalised for the caret) and returns
 * whether it went in; false (no editor mounted, preview-only) leaves the clip to be typed later.
 * `getBefore` should return the text before the caret, a few dozen characters is enough: without it
 * the hook can only remember what it typed itself, and assumes the first clip starts a line.
 *
 * What was typed is tracked per recording outside the component (see `trackDictation`), so a clip
 * is typed exactly once even though events and the poll deliver the same row, and clips said while
 * another doc was open, the Docs view was closed or no editor was mounted are typed, in order,
 * when this doc's editor is available again. A clip is marked typed only after `insert` ran, and
 * never goes into a doc other than the one being dictated into.
 */
export function useDictation(docId: string, insert: (text: string) => boolean, getBefore?: () => string): void {
  const insertRef = useRef(insert)
  const beforeRef = useRef(getBefore)
  useEffect(() => {
    insertRef.current = insert
    beforeRef.current = getBefore
  })

  useEffect(() => {
    let tail = ''

    const run = (): void => {
      const live = liveDoc(useStore.getState().meetingStatus)
      // The recording's identity: first seen live as a dictation into some doc, remembered from then on.
      if (live && live.mode === 'dictate') trackDictation(live.meetingId, live.docId)
      const editorHere = useStore.getState().activeDoc?.id === docId
      for (const [meetingId, s] of dictationsFor(docId)) {
        const isLive = live?.meetingId === meetingId
        s.endedAt = isLive ? 0 : s.endedAt || Date.now()
        const segs = useDocRec.getState().segments[meetingId] ?? []
        const { ready, skip } = planDictation(segs, s.typed, editorHere)
        for (const id of skip) s.typed.add(id)
        let before = beforeRef.current ? beforeRef.current() : tail
        for (const c of ready) {
          const text = dictationText(c.text, before)
          if (text && !insertRef.current(text)) break
          // Marked only now that the text is in the editor, so a refused insert is retried.
          s.typed.add(c.id)
          before = (before + text).slice(-80)
        }
        tail = before
        // Done once it has ended, nothing is still being transcribed and everything held was typed;
        // the tail window is the backstop for a recording whose last clips never arrive.
        const settling = useDocRec.getState().settling?.meetingId === meetingId
        if (!isLive && ((!settling && dictationDrained(segs, s.typed)) || Date.now() - s.endedAt > DICTATION_TAIL_MS)) {
          forgetDictation(meetingId)
        }
      }
    }

    const offDoc = useDocRec.subscribe(run)
    const offMain = useStore.subscribe((s, prev) => {
      if (s.meetingStatus !== prev.meetingStatus || s.activeDoc?.id !== prev.activeDoc?.id) run()
    })
    // The editor appearing (preview to split, a doc finishing loading) is not a store change.
    const retry = setInterval(run, 1000)
    run()
    return () => { offDoc(); offMain(); clearInterval(retry) }
  }, [docId])
}

/**
 * While a record-mode recording of this doc is live, post when each typed line was written (see
 * `diffTouchedLines`), debounced. Dictation and idle docs post nothing. The first body seen for a
 * recording is the baseline, so opening a doc never marks its existing lines.
 */
export function useNoteMarks(docId: string, body: string): void {
  const status = useStore((s) => s.meetingStatus)
  const a = status?.active
  const here = a && a.doc_id === docId && (a.doc_mode ?? 'record') === 'record' ? a : null
  const elapsed = useElapsed(here)
  const elapsedRef = useRef(0)
  elapsedRef.current = elapsed
  const meetingId = here?.meeting_id ?? ''
  const prev = useRef<{ id: string; body: string } | null>(null)
  const pending = useRef(new Map<string, number>())
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const flush = (id: string): void => {
    if (timer.current) { clearTimeout(timer.current); timer.current = null }
    const marks = [...pending.current].map(([line, at]) => ({ line, t: at }))
    pending.current.clear()
    if (marks.length) api.meetings.putNoteMarks(id, marks).catch(() => { /* marks are best effort */ })
  }
  // Leaving a recording (it stopped, or another doc opened) posts what is buffered and drops the map.
  useEffect(() => () => { if (meetingId) flush(meetingId) }, [meetingId]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!meetingId) { prev.current = null; return }
    if (!prev.current || prev.current.id !== meetingId) { prev.current = { id: meetingId, body }; return }
    const t = Math.round(elapsedRef.current * 10) / 10
    for (const line of diffTouchedLines(prev.current.body, body)) pending.current.set(line, t)
    prev.current.body = body
    if (!pending.current.size || timer.current) return
    timer.current = setTimeout(() => flush(meetingId), 2500)
  }, [body, meetingId])
}

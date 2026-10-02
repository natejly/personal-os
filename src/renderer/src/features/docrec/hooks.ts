import { useEffect, useRef, useState } from 'react'
import type { MeetingStatusInfo } from '@shared/types'
import { useStore } from '../../store'
import { useDocRec } from './store'
import { dictationText, readyForInsert } from './dictation'
import { liveDoc } from './segments'

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
 * `insert` receives the text to type (already spaced and capitalised for the caret). `getBefore`
 * should return the text before the caret, a few dozen characters is enough: without it the hook
 * can only remember what it typed itself, and assumes the first clip starts a line.
 *
 * Exactly once per clip, in order: events and the poll deliver the same row, so each clip id is
 * recorded as seen before it is typed. A clip is never typed past an earlier unfinished one. Clips
 * that began before the hook mounted are never typed, so opening a doc cannot replay an old
 * dictation into it.
 */
export function useDictation(docId: string, insert: (text: string) => void, getBefore?: () => string): void {
  const insertRef = useRef(insert)
  const beforeRef = useRef(getBefore)
  useEffect(() => {
    insertRef.current = insert
    beforeRef.current = getBefore
  })

  useEffect(() => {
    const mountedAt = Date.now()
    const seen = new Set<string>()
    let following: string | null = null
    let endedAt = 0
    let tail = ''

    const run = (): void => {
      const live = liveDoc(useStore.getState().meetingStatus)
      if (live && live.docId === docId && live.mode === 'dictate') {
        following = live.meetingId
        endedAt = 0
      } else if (following) {
        endedAt = endedAt || Date.now()
        if (Date.now() - endedAt > DICTATION_TAIL_MS) following = null
      }
      if (!following) return
      const segs = useDocRec.getState().segments[following] ?? []
      // Anything that started before this mount predates the hook; a missing start counts too.
      for (const s of segs) if (!(s.started_at * 1000 >= mountedAt)) seen.add(s.id)
      const { ready, consumed } = readyForInsert(segs, seen)
      // Marked seen BEFORE typing: if `insert` throws, the clip is not retyped by the next delivery.
      for (const id of consumed) seen.add(id)
      if (ready.length === 0) return
      let before = beforeRef.current ? beforeRef.current() : tail
      for (const s of ready) {
        const text = dictationText(s.text, before)
        if (!text) continue
        insertRef.current(text)
        before = (before + text).slice(-80)
      }
      tail = before
    }

    const offDoc = useDocRec.subscribe(run)
    const offMain = useStore.subscribe((s, prev) => { if (s.meetingStatus !== prev.meetingStatus) run() })
    run()
    return () => { offDoc(); offMain() }
  }, [docId])
}

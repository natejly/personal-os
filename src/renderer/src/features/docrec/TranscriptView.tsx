import { useEffect, useMemo, useRef, useState } from 'react'
import { ArrowDown, Play, Pause, ChevronDown, ChevronUp, Copy, Download, RefreshCw, Search, X } from 'lucide-react'
import type { FullMeeting, MeetingSegment } from '@shared/types'
import { formatOffset, mergeSegments, speakerLabel } from '../../lib/transcript'
import { exportFilename, formatTranscript, type ExportFormat } from './exportText'
import { api } from '../../lib/api'
import { playableSegment } from './segments'
import { findMatches, splitRuns, stepMatch } from './search'

/**
 * The transcript of one recording: timestamped, speaker-labelled lines that follow the live edge
 * until the reader scrolls away, with find, copy and export.
 *
 * Props are plain data so the same pane can serve a live recording and a finished one.
 */
export interface TranscriptViewProps {
  title: string
  segments: MeetingSegment[]
  /** The full meeting, for attendees and speaker names; labels fall back to You and Them without it. */
  meeting: FullMeeting | null
  /** The recording is running, so the pane follows the newest line. */
  live: boolean
  /** The row's own clip count, so a held tail that is short of it says so. */
  segmentCount: number
  /** Retranscribe the failed clips; omit to hide the control. */
  onRetranscribe?: () => void
  busy?: boolean
}

/** Within this many px of the bottom counts as "at the live edge". */
const EDGE_PX = 28

function download(name: string, body: string, mime: string): void {
  const url = URL.createObjectURL(new Blob([body], { type: mime }))
  const a = document.createElement('a')
  a.href = url
  a.download = name
  a.click()
  URL.revokeObjectURL(url)
}

export default function TranscriptView({ title, segments, meeting, live, segmentCount, onRetranscribe, busy }: TranscriptViewProps): JSX.Element {
  const scroller = useRef<HTMLDivElement>(null)
  const [stick, setStick] = useState(true)
  const [finding, setFinding] = useState(false)
  const [query, setQuery] = useState('')
  const [active, setActive] = useState(-1)
  const [copied, setCopied] = useState(false)
  // One clip plays at a time; `playing` is the line id so its button can show Pause.
  const player = useRef<{ el: HTMLAudioElement; url: string; seg: string } | null>(null)
  const [playing, setPlaying] = useState<string | null>(null)
  const [rate, setRate] = useState(1)
  const stopPlayer = (): void => {
    const p = player.current
    if (p) { p.el.pause(); URL.revokeObjectURL(p.url) }
    player.current = null
    setPlaying(null)
  }
  useEffect(() => stopPlayer, []) // eslint-disable-line react-hooks/exhaustive-deps
  const play = async (lineId: string, segId: string): Promise<void> => {
    const was = playing
    stopPlayer()
    if (was === lineId) return
    try {
      const url = await api.meetings.segmentAudio(segments[0]?.meeting_id ?? meeting?.id ?? '', segId)
      const el = new Audio(url)
      el.playbackRate = rate
      el.onended = stopPlayer
      player.current = { el, url, seg: segId }
      setPlaying(lineId)
      await el.play()
    } catch { stopPlayer() }
  }
  const cycleRate = (): void => {
    const next = rate === 1 ? 1.5 : rate === 1.5 ? 2 : 1
    setRate(next)
    if (player.current) player.current.el.playbackRate = next
  }
  const keep = !!meeting?.keep_audio

  const lines = useMemo(() => mergeSegments(segments), [segments])
  const who = (l: (typeof lines)[number]): string =>
    speakerLabel(l.channel, l.speaker, meeting?.attendees ?? [], meeting?.speaker_names ?? {})
  const texts = useMemo(() => lines.map((l) => l.text || 'still transcribing…'), [lines])
  const matches = useMemo(() => (finding ? findMatches(texts, query) : []), [finding, texts, query])
  const failed = segments.filter((s) => s.state === 'failed').length

  // Follow the live edge only while the reader is at it; scrolling up is the way to stop following.
  useEffect(() => {
    const el = scroller.current
    if (el && live && stick) el.scrollTop = el.scrollHeight
  }, [lines, live, stick])

  // A new query starts from its first match.
  useEffect(() => { setActive(matches.length ? 0 : -1) }, [query, finding]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (active < 0) return
    scroller.current?.querySelector('[data-active-hit="1"]')?.scrollIntoView({ block: 'center' })
  }, [active, matches.length])

  const onScroll = (): void => {
    const el = scroller.current
    if (!el) return
    setStick(el.scrollHeight - el.scrollTop - el.clientHeight <= EDGE_PX)
  }
  const jump = (): void => {
    const el = scroller.current
    if (el) el.scrollTop = el.scrollHeight
    setStick(true)
  }
  const step = (dir: 1 | -1): void => setActive((a) => stepMatch(matches.length, a, dir))

  const exportLines = lines.map((l) => ({ t_start: l.t_start, who: who(l), text: l.text }))
  const copyAll = async (): Promise<void> => {
    try {
      await navigator.clipboard.writeText(formatTranscript(exportLines, 'txt', title))
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch { /* clipboard can be refused; nothing else to do */ }
  }
  const save = (fmt: ExportFormat): void =>
    download(exportFilename(title, fmt), formatTranscript(exportLines, fmt, title), fmt === 'md' ? 'text/markdown' : 'text/plain')

  return (
    <div className="dr-transcript">
      <div className="dr-tools">
        <button className={`icon-btn ghost ${finding ? 'on' : ''}`} title="Find in transcript" aria-label="Find in transcript"
          onClick={() => setFinding((f) => !f)}><Search size={14} /></button>
        <button className="icon-btn ghost" title={copied ? 'Copied' : 'Copy transcript'} aria-label="Copy transcript"
          disabled={lines.length === 0} onClick={() => void copyAll()}><Copy size={14} /></button>
        <button className="ghost-btn dr-small" disabled={lines.length === 0} onClick={() => save('txt')}><Download size={12} /> .txt</button>
        <button className="ghost-btn dr-small" disabled={lines.length === 0} onClick={() => save('md')}><Download size={12} /> .md</button>
        {keep && (
          <button className="ghost-btn dr-small" onClick={cycleRate} title="Playback speed" aria-label="Playback speed">{rate}x</button>
        )}
        {failed > 0 && onRetranscribe && (
          <button className="ghost-btn dr-small" disabled={busy} onClick={onRetranscribe}
            title="Transcribe the clips that failed again">
            <RefreshCw size={12} className={busy ? 'spin' : ''} /> Retry {failed} failed
          </button>
        )}
      </div>

      {finding && (
        <div className="dr-find">
          <input autoFocus value={query} placeholder="Find" aria-label="Find in transcript"
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') step(e.shiftKey ? -1 : 1)
              if (e.key === 'Escape') { setFinding(false); setQuery('') }
            }} />
          <span className="dr-find-count">{query.trim() ? (matches.length ? `${active + 1} of ${matches.length}` : 'No matches') : ''}</span>
          <button className="icon-btn ghost" aria-label="Previous match" disabled={!matches.length} onClick={() => step(-1)}><ChevronUp size={14} /></button>
          <button className="icon-btn ghost" aria-label="Next match" disabled={!matches.length} onClick={() => step(1)}><ChevronDown size={14} /></button>
          <button className="icon-btn ghost" aria-label="Close find" onClick={() => { setFinding(false); setQuery('') }}><X size={14} /></button>
        </div>
      )}

      {/* Never a silent cap: a held tail short of the row's own count says so. */}
      {segments.length < segmentCount && <p className="dr-note">{segments.length} of {segmentCount} clips loaded</p>}

      <div className="dr-lines" ref={scroller} onScroll={onScroll}>
        {lines.length === 0 && (
          <p className="dr-empty">
            {live ? 'Nothing transcribed yet. Clips close on a timer, so the first words take a moment.' : 'No speech was transcribed.'}
          </p>
        )}
        {lines.map((l, i) => (
          <div key={l.id} className={`dr-line ${l.pending ? 'pending' : ''}`}>
            <div className="dr-line-head">
              <span className={`dr-who ${l.channel === 'mic' ? '' : 'them'}`}>{who(l)}</span>
              {(() => {
                const seg = playableSegment(l.ids, segments, keep)
                return seg
                  ? <button className="dr-at dr-at-play" title="Play from here" aria-label={`Play from ${formatOffset(l.t_start)}`}
                      onClick={() => void play(l.id, seg)}>
                      {playing === l.id ? <Pause size={10} /> : <Play size={10} />} {formatOffset(l.t_start)}
                    </button>
                  : <span className="dr-at" title={keep ? 'This clip has no audio' : 'Audio was not kept'}>{formatOffset(l.t_start)}</span>
              })()}
            </div>
            <p className="dr-line-text">
              {splitRuns(texts[i], matches, i, active).map((r, k) =>
                r.hit
                  ? <mark key={k} className={r.active ? 'on' : ''} data-active-hit={r.active ? '1' : undefined}>{r.text}</mark>
                  : <span key={k}>{r.text}</span>)}
            </p>
          </div>
        ))}
      </div>

      {live && !stick && (
        <button className="dr-jump" onClick={jump}><ArrowDown size={12} /> Jump to live</button>
      )}
    </div>
  )
}

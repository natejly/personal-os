import { useEffect, useRef, useState } from 'react'
import { AudioLines, ChevronDown, FileAudio, Mic, Square, X } from 'lucide-react'
import { useStore } from '../../store'
import MeetingConsentModal from '../../components/MeetingConsentModal'
import { useDocRec } from './store'
import { useDocRecSync } from './hooks'
import { recordAvailability } from './segments'
import '../../styles/docrec.css'

/**
 * The toolbar control for recording into a doc.
 *
 * Idle: Record (record and summarize) with a menu for dictation and audio import. Live on this
 * doc: Stop. Live somewhere else: disabled, with the reason. It also mounts the one-time consent
 * modal, because the consent flow opens from here and the Docs view does not otherwise host it.
 */
export default function DocRecordButton({ docId }: { docId: string }): JSX.Element {
  useDocRecSync(docId)
  const status = useStore((s) => s.meetingStatus)
  const consentOpen = useStore((s) => s.meetingConsentOpen)
  const busy = useDocRec((s) => s.busy)
  const notice = useDocRec((s) => s.notice)
  const [menu, setMenu] = useState(false)
  const [keepAudio, setKeepAudio] = useState(false)
  const root = useRef<HTMLDivElement>(null)
  const file = useRef<HTMLInputElement>(null)
  const avail = recordAvailability(status, docId)

  useEffect(() => {
    if (!menu) return
    const away = (e: MouseEvent): void => { if (!root.current?.contains(e.target as Node)) setMenu(false) }
    const esc = (e: KeyboardEvent): void => { if (e.key === 'Escape') setMenu(false) }
    document.addEventListener('mousedown', away)
    document.addEventListener('keydown', esc)
    return () => { document.removeEventListener('mousedown', away); document.removeEventListener('keydown', esc) }
  }, [menu])

  const start = (mode: 'record' | 'dictate'): void => { setMenu(false); void useDocRec.getState().start(docId, mode, keepAudio ? { keep_audio: true } : undefined) }

  return (
    <div className="dr-record" ref={root}>
      {avail.kind === 'live-here' && (
        <button className="ghost-btn danger dr-stop" disabled={busy} onClick={() => void useDocRec.getState().stop()}
          title="Stop recording">
          <Square size={13} /> {busy ? 'Stopping' : 'Stop'}
        </button>
      )}
      {avail.kind === 'live-elsewhere' && (
        <button className="ghost-btn" disabled title={avail.reason}><Mic size={13} /> Record</button>
      )}
      {avail.kind === 'idle' && (
        <div className="dr-split">
          <button className="ghost-btn" disabled={busy} onClick={() => start('record')}
            title="Record and summarize: keeps a transcript and proposes a summary for you to review">
            <Mic size={13} /> Record
          </button>
          <button className="ghost-btn dr-caret" disabled={busy} aria-label="Recording options" aria-haspopup="menu" aria-expanded={menu}
            onClick={() => setMenu((m) => !m)}><ChevronDown size={12} /></button>
        </div>
      )}

      {menu && (
        <div className="dr-menu" role="menu">
          <button role="menuitem" onClick={() => start('record')}><Mic size={13} /> Record and summarize</button>
          <button role="menuitem" onClick={() => start('dictate')}><AudioLines size={13} /> Dictate into note</button>
          <label className="dr-keep" title="Keep this recording's audio so a timestamp can play it back. Off by default: the audio stays on this Mac until you delete it.">
            <input type="checkbox" checked={keepAudio} onChange={(e) => setKeepAudio(e.target.checked)} /> Keep audio for playback
          </label>
          <button role="menuitem" onClick={() => { setMenu(false); file.current?.click() }}><FileAudio size={13} /> Import audio file</button>
        </div>
      )}

      <input ref={file} type="file" accept="audio/*,video/mp4,.m4a,.mp3,.wav,.aac,.flac,.ogg,.webm" hidden
        onChange={(e) => {
          const f = e.target.files?.[0]
          e.target.value = ''
          if (f) void useDocRec.getState().importAudio(docId, f)
        }} />

      {notice && (
        <div className="dr-notice" role="alert">
          <span>{notice.text}</span>
          {notice.action === 'settings' && (
            <button className="ghost-btn dr-small" onClick={() => { useStore.getState().openSettings('meetings'); useDocRec.getState().dismissNotice() }}>
              Open settings
            </button>
          )}
          <button className="icon-btn ghost" aria-label="Dismiss" onClick={() => useDocRec.getState().dismissNotice()}><X size={12} /></button>
        </div>
      )}

      {consentOpen && <MeetingConsentModal />}
    </div>
  )
}

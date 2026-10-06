import { useEffect, useRef } from 'react'
import { useStore } from '../../store'
import { useDocRec } from './store'
import { chordDown, chordFsm, chordUp, DEFAULT_CHORD, initialChord, parseChord, type ChordState } from './chord'

/** Push-to-talk / tap-to-latch dictation into the open doc, on the configured chord (settings.dictationChord). */
export function useDictationChord(docId: string, canDictate: boolean, dictating: boolean): void {
  const spec = useStore((s) => s.settings.dictationChord) || DEFAULT_CHORD
  const live = useRef({ docId, canDictate, dictating })
  live.current = { docId, canDictate, dictating }

  useEffect(() => {
    const chord = parseChord(spec)
    if (!chord) return
    let st: ChordState = initialChord
    const feed = (ev: 'down' | 'up'): boolean => {
      const l = live.current
      // A latched recording that was stopped by hand (button, menu) is no longer latched.
      if (st.latched && !l.dictating) st = initialChord
      const [next, action] = chordFsm(st, ev, Date.now(), l.canDictate && !!l.docId)
      st = next
      if (action === 'start') void useDocRec.getState().start(l.docId, 'dictate')
      else if (action === 'stop') void useDocRec.getState().stop()
      return action !== null || next.held
    }
    const down = (e: KeyboardEvent): void => { if (chordDown(e, chord) && feed('down')) e.preventDefault() }
    const up = (e: KeyboardEvent): void => { if (chordUp(e, chord)) feed('up') }
    window.addEventListener('keydown', down)
    window.addEventListener('keyup', up)
    return () => { window.removeEventListener('keydown', down); window.removeEventListener('keyup', up) }
  }, [spec])
}

/**
 * Hold-to-talk for dictation. A chord held longer than TAP_MS records only while held (push-to-talk);
 * a quick tap latches recording on, and the next press of the chord stops it. Pure: the mic button
 * feeds it key events and does what the returned action says.
 */

import { shortcut } from '@shared/shortcuts'

export const DEFAULT_CHORD = shortcut('dictation').keys
/** A press shorter than this is a tap and latches dictation on. */
export const TAP_MS = 250

export interface Chord { ctrl: boolean; alt: boolean; meta: boolean; shift: boolean; key: string }

/** "Control+Alt+D" -> flags + key; null when there is no non-modifier key. Case-insensitive. */
export function parseChord(spec: string): Chord | null {
  const c: Chord = { ctrl: false, alt: false, meta: false, shift: false, key: '' }
  for (const raw of spec.split('+')) {
    const p = raw.trim().toLowerCase()
    if (!p) continue
    if (p === 'control' || p === 'ctrl') c.ctrl = true
    else if (p === 'alt' || p === 'option') c.alt = true
    else if (p === 'command' || p === 'cmd' || p === 'meta' || p === 'super') c.meta = true
    else if (p === 'shift') c.shift = true
    else if (c.key) return null
    else c.key = p
  }
  return c.key ? c : null
}

interface KeyLike { key: string; code?: string; ctrlKey: boolean; altKey: boolean; metaKey: boolean; shiftKey: boolean }

/** Letter keys are matched by physical `code` too: Option+D types a symbol, so `key` alone would miss it. */
const sameKey = (e: KeyLike, c: Chord): boolean =>
  e.key.toLowerCase() === c.key || (c.key.length === 1 && e.code === `Key${c.key.toUpperCase()}`)

export const chordDown = (e: KeyLike, c: Chord): boolean =>
  sameKey(e, c) && e.ctrlKey === c.ctrl && e.altKey === c.alt && e.metaKey === c.meta && e.shiftKey === c.shift

/** Releasing the key or any modifier of the chord ends the press. */
export const chordUp = (e: KeyLike, c: Chord): boolean =>
  sameKey(e, c) ||
  (e.key === 'Control' && c.ctrl) || (e.key === 'Alt' && c.alt) || (e.key === 'Meta' && c.meta) || (e.key === 'Shift' && c.shift)

export interface ChordState {
  /** Chord currently held (and acted on, or deliberately ignored). */
  held: boolean
  at: number
  /** Recording stays on after a tap, until the next press. */
  latched: boolean
  /** This press only stopped a latched recording; its release does nothing. */
  swallow: boolean
}

export const initialChord: ChordState = { held: false, at: 0, latched: false, swallow: false }

export type ChordAction = 'start' | 'stop' | null

export function chordFsm(s: ChordState, ev: 'down' | 'up', t: number, canDictate: boolean): [ChordState, ChordAction] {
  if (ev === 'down') {
    if (s.held) return [s, null] // key repeat
    if (s.latched) return [{ held: true, at: t, latched: false, swallow: true }, 'stop']
    if (!canDictate) return [s, null]
    return [{ held: true, at: t, latched: false, swallow: false }, 'start']
  }
  if (!s.held) return [s, null] // a keyup with no matching keydown
  if (s.swallow) return [{ ...s, held: false, swallow: false }, null]
  if (t - s.at < TAP_MS) return [{ ...s, held: false, latched: true }, null]
  return [{ ...s, held: false, latched: false }, 'stop']
}

import { useEffect, useRef, useState } from 'react'
import { Blobatar } from 'blobatar/react'
import { happy, idle, sad, scared, sleepy, surprised, thinking, unsure, type Expression } from 'blobatar/expression'
import 'blobatar/motion.css'
import { useStore } from '../store'
import { mascotId, type MascotId } from '../lib/appearance'

export type FaceMood = 'idle' | 'thinking' | 'unsure' | 'surprised' | 'scared' | 'sad' | 'sleepy' | 'happy'

/** Status words used across replies, desks, subagents and job runs, read as a pose. Unknown words stay idle. */
export const MOOD: Record<string, FaceMood> = {
  streaming: 'thinking', running: 'thinking', working: 'thinking', planning: 'thinking',
  awaiting_plan: 'unsure', blocked: 'unsure',
  needs_approval: 'surprised', 'needs-approval': 'surprised', awaiting_approval: 'surprised',
  paused: 'sleepy', queued: 'sleepy', stopped: 'sleepy',
  interrupted: 'scared', timed_out: 'scared', cancelled: 'sleepy',
  error: 'sad', failed: 'sad',
  review: 'happy', done: 'happy', completed: 'happy', partial: 'unsure',
  waiting_approval: 'surprised'
}

/** Each pose as a blob expression. */
const BLOB_EXPRESSION: Record<FaceMood, Expression> = { idle, thinking, unsure, surprised, scared, sad, sleepy, happy }

const LIVE = new Set(['streaming', 'running', 'working', 'planning'])
/** How a live run ended decides the one-shot reaction: a droop for these, a hop for a clean finish, nothing for the rest (approval, paused, stopped). */
const BAD = new Set(['error', 'failed', 'interrupted', 'timed_out'])
const CLEAN = new Set(['idle', 'done', 'completed', 'review'])
/** How long the eyes stay lowered after a failed run. */
const DROOP_MS = 1400

/** Every face is drawn this much larger than the size its caller asks for. */
export const FACE_SCALE = 1.25

/**
 * The same name always draws the same creature, so an agent is recognisable wherever it shows up.
 * Live ones move all the time; the rest only move on hover, which keeps a long transcript quiet.
 * `hue`/`tone` lock the color so a family of faces differs by shape alone.
 * With the mascot off, `decor` faces (avatars beside a name) draw nothing, so the caller's box collapses;
 * the rest become a small status dot in the same box, because there the face is what shows progress.
 */
export default function Face(props: FaceProps): JSX.Element | null {
  return <FaceView {...props} mascot={useStore((s) => mascotId(s.settings.mascot))} />
}

export interface FaceProps { name: string; status?: string; size?: number | 'fill'; title?: string; hue?: number; tone?: number; send?: boolean; calm?: boolean; decor?: boolean }

/** `Face` with the mascot choice passed in, which is what draws. */
export function FaceView({ name, status, size = 26, title, hue, tone, send, calm, decor, mascot }: FaceProps & { mascot: MascotId }): JSX.Element | null {
  const fx = useReaction(status, send, calm)
  const mood = (status && MOOD[status]) || 'idle'
  const live = !!status && LIVE.has(status)
  const px = size === 'fill' ? undefined : Math.round(size * FACE_SCALE)
  const className = fx ? `face ${fx}` : 'face'
  if (mascot === 'off') {
    if (decor) return null
    return <span className="face face-dot" style={{ width: px, height: px }} data-mood={mood} data-live={live || undefined} role={title ? 'img' : undefined} aria-label={title} title={title} />
  }
  return <Blobatar className={className} name={name} size={px} title={title} hue={hue} tone={tone} expression={BLOB_EXPRESSION[mood]} animate={live ? 'always' : 'hover'} />
}

/**
 * One-shot reactions read off the status the caller already passes, as a class for styles.css (`.face.fx-*`).
 * Starting a run squishes, a clean finish hops, a failed one droops (eyes lowered for a moment, then back).
 * A run the user stopped (`calm`, latched until it ends) gets no reaction. `send` squishes once on mount, for the face that appears the instant the user sends. The class name alternates
 * between two spellings so the same reaction twice in a row restarts its CSS animation without a reflow.
 */
function useReaction(status: string | undefined, send: boolean | undefined, calm: boolean | undefined): string {
  const prev = useRef<string | undefined>(status)
  const stopped = useRef(false)
  const [fx, setFx] = useState<{ kind: string; n: number } | null>(send ? { kind: 'squish', n: 0 } : null)
  const [droop, setDroop] = useState(false)
  useEffect(() => {
    if (calm) stopped.current = true
    const was = prev.current
    prev.current = status
    const live = !!status && LIVE.has(status)
    const wasLive = !!was && LIVE.has(was)
    if (live === wasLive) return
    const quiet = !live && stopped.current
    stopped.current = false
    if (quiet) return
    const kind = live ? 'squish' : status && BAD.has(status) ? 'droop' : !status || CLEAN.has(status) ? 'hop' : ''
    if (kind) setFx((f) => ({ kind, n: (f?.n ?? 0) + 1 }))
  }, [status, calm])
  useEffect(() => {
    if (fx?.kind !== 'droop') return
    setDroop(true)
    const t = setTimeout(() => setDroop(false), DROOP_MS)
    return () => clearTimeout(t)
  }, [fx])
  return [fx && `fx-${fx.kind}-${fx.n % 2}`, droop && 'fx-down'].filter(Boolean).join(' ')
}

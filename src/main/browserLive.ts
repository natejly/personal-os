/** Pacing for the live view of an agent browser session. Electron-free so it can be tested with a fake clock. */

export const FRAME_EVERY_MS = 1_500
/** After the last activity, keep capturing this long to catch late page renders. */
export const TAIL_MS = 5_000

export type Pace = 'capture' | 'idle' | 'none'

/** capture: grab frames on the cadence; idle: quiet, say so once; none: nobody is watching or the session is over. */
export function paceFor(o: { now: number; lastActivity: number; busy: boolean; subscribed: boolean; ended: boolean }): Pace {
  if (!o.subscribed || o.ended) return 'none'
  return o.busy || o.now - o.lastActivity < TAIL_MS ? 'capture' : 'idle'
}

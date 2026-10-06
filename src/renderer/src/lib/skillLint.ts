/** How long the skill editor waits after the last keystroke before asking the backend to lint. */
export const LINT_DELAY_MS = 400

/**
 * Call `fn` once typing pauses for `ms`, and hand only the newest call's result to `onResult`:
 * a slow reply to an older draft never overwrites the findings for what is on screen now.
 */
export function debounceLatest<A, R>(fn: (a: A) => Promise<R>, ms: number, onResult: (r: R | null) => void): { call: (a: A) => void; cancel: () => void } {
  let timer: ReturnType<typeof setTimeout> | undefined
  let seq = 0
  const cancel = (): void => { if (timer !== undefined) clearTimeout(timer); seq++ }
  const call = (a: A): void => {
    cancel()
    const mine = seq
    timer = setTimeout(() => {
      fn(a).then((r) => { if (mine === seq) onResult(r) }, () => { if (mine === seq) onResult(null) })
    }, ms)
  }
  return { call, cancel }
}

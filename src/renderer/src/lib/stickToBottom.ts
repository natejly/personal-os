import { useCallback, useEffect, useLayoutEffect, useRef, useState, type RefObject } from 'react'

/** Scrolling up by more than this releases follow; getting back within ATTACH_PX re-attaches. */
export const RELEASE_PX = 24
export const ATTACH_PX = 80

/**
 * Whether the scroller should keep following the bottom. `distance` is scrollHeight - scrollTop -
 * clientHeight. Content growth never lowers scrollTop, so it cannot release; a shrink clamp lands
 * at distance 0 and stays attached.
 */
export function nextStick(prev: boolean, m: { top: number; prevTop: number; distance: number }): boolean {
  const up = m.top < m.prevTop - 1
  if (up && m.distance > RELEASE_PX) return false
  if (!up && m.distance < ATTACH_PX) return true
  if (up && m.distance <= RELEASE_PX) return true
  return prev
}

/** True when something between the event target and the scroller can itself still scroll up. */
function innerScrollsUp(target: EventTarget | null, root: HTMLElement): boolean {
  for (let n = target instanceof HTMLElement ? target : null; n && n !== root; n = n.parentElement) {
    const oy = getComputedStyle(n).overflowY
    if ((oy === 'auto' || oy === 'scroll') && n.scrollHeight > n.clientHeight && n.scrollTop > 0) return true
  }
  return false
}

/**
 * Follow-the-bottom for a transcript. Refs are the source of truth; state mirrors them only on a
 * flip, so a streamed token does not re-render the view. `resetKey` (the conversation id) re-sticks,
 * and so does a new trailing user message (the user's own send). `unseen` is how many rows were
 * appended since follow was released (`rows` is the transcript length): growth inside a row that is
 * still streaming shows the pill but adds no count, or the number would climb once per frame.
 */
export function useStickToBottom(
  scrollRef: RefObject<HTMLDivElement>,
  opts: { resetKey: unknown; tailUserId: string | null; rows?: number }
): { stick: boolean; unseen: number; jump: () => void; release: () => void } {
  const stickRef = useRef(true)
  const prevTop = useRef(0)
  const rows = opts.rows ?? 0
  const rowsRef = useRef(rows)
  const seenRows = useRef(rows)
  const [stick, setStick] = useState(true)
  const [unseen, setUnseen] = useState(0)

  const setUnseenOnFlip = useCallback((n: number) => setUnseen((p) => (p === n ? p : n)), [])
  // Re-attaching marks everything on screen as seen; releasing starts the count from here.
  const setStickBoth = useCallback((v: boolean) => {
    stickRef.current = v
    if (v) { seenRows.current = rowsRef.current; setUnseenOnFlip(0) }
    setStick((p) => (p === v ? p : v))
  }, [setUnseenOnFlip])

  rowsRef.current = rows
  useEffect(() => {
    if (stickRef.current) seenRows.current = rows
    else setUnseenOnFlip(Math.max(0, rows - seenRows.current))
  }, [rows, setUnseenOnFlip])

  const toBottom = useCallback(() => {
    const el = scrollRef.current
    if (!el) return
    el.scrollTop = el.scrollHeight
    prevTop.current = el.scrollTop
  }, [scrollRef])

  const jump = useCallback(() => {
    setStickBoth(true)
    toBottom()
  }, [setStickBoth, toBottom])

  // A different chat starts at its last message with follow on, whatever the previous one was doing.
  useLayoutEffect(() => { jump() }, [opts.resetKey, jump])
  useLayoutEffect(() => { if (opts.tailUserId) jump() }, [opts.tailUserId, jump])

  useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    const inner = el.querySelector('.messages-inner')
    const release = (): void => setStickBoth(false)
    const onScroll = (): void => {
      const distance = el.scrollHeight - el.scrollTop - el.clientHeight
      const next = nextStick(stickRef.current, { top: el.scrollTop, prevTop: prevTop.current, distance })
      prevTop.current = el.scrollTop
      if (next !== stickRef.current) setStickBoth(next)
    }
    const onWheel = (e: WheelEvent): void => {
      if (e.deltaY < 0 && el.scrollTop > 0 && !innerScrollsUp(e.target, el)) release()
    }
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'PageUp' || e.key === 'ArrowUp' || e.key === 'Home') release()
    }
    // Runs once per frame before paint, so following needs no rAF of its own. The scroller itself is
    // observed too: it shrinks when the composer grows or the plan panel appears.
    const ro = new ResizeObserver(() => { if (stickRef.current) toBottom() })
    ro.observe(el)
    if (inner) ro.observe(inner)
    el.addEventListener('scroll', onScroll, { passive: true })
    el.addEventListener('wheel', onWheel, { passive: true })
    el.addEventListener('keydown', onKey)
    return () => {
      ro.disconnect()
      el.removeEventListener('scroll', onScroll)
      el.removeEventListener('wheel', onWheel)
      el.removeEventListener('keydown', onKey)
    }
  }, [scrollRef, opts.resetKey, setStickBoth, toBottom])

  const release = useCallback(() => setStickBoth(false), [setStickBoth])
  return { stick, unseen, jump, release }
}

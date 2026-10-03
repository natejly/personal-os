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
 * and so does a new trailing user message (the user's own send).
 */
export function useStickToBottom(
  scrollRef: RefObject<HTMLDivElement>,
  opts: { resetKey: unknown; tailUserId: string | null }
): { stick: boolean; unseen: number; jump: () => void } {
  const stickRef = useRef(true)
  const prevTop = useRef(0)
  const unseenRef = useRef(0)
  const [stick, setStick] = useState(true)
  const [unseen, setUnseen] = useState(0)

  const setStickBoth = useCallback((v: boolean) => {
    stickRef.current = v
    setStick((p) => (p === v ? p : v))
  }, [])
  const setUnseenBoth = useCallback((n: number) => {
    unseenRef.current = n
    setUnseen((p) => (p === n ? p : n))
  }, [])

  const toBottom = useCallback(() => {
    const el = scrollRef.current
    if (!el) return
    el.scrollTop = el.scrollHeight
    prevTop.current = el.scrollTop
  }, [scrollRef])

  const jump = useCallback(() => {
    setStickBoth(true)
    setUnseenBoth(0)
    toBottom()
  }, [setStickBoth, setUnseenBoth, toBottom])

  // A different chat starts at its last message with follow on, whatever the previous one was doing.
  useLayoutEffect(() => { jump() }, [opts.resetKey, jump])
  useLayoutEffect(() => { if (opts.tailUserId) jump() }, [opts.tailUserId, jump])

  useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    const inner = el.querySelector('.messages-inner')
    let innerH = inner ? inner.getBoundingClientRect().height : 0
    const release = (): void => setStickBoth(false)
    const onScroll = (): void => {
      const distance = el.scrollHeight - el.scrollTop - el.clientHeight
      const next = nextStick(stickRef.current, { top: el.scrollTop, prevTop: prevTop.current, distance })
      prevTop.current = el.scrollTop
      if (next !== stickRef.current) setStickBoth(next)
      if (next && unseenRef.current) setUnseenBoth(0)
    }
    const onWheel = (e: WheelEvent): void => {
      if (e.deltaY < 0 && el.scrollTop > 0 && !innerScrollsUp(e.target, el)) release()
    }
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'PageUp' || e.key === 'ArrowUp' || e.key === 'Home') release()
    }
    // Runs once per frame before paint, so following needs no rAF of its own.
    const ro = new ResizeObserver(() => {
      const h = inner ? inner.getBoundingClientRect().height : 0
      if (stickRef.current) toBottom()
      else if (h > innerH) setUnseenBoth(unseenRef.current + 1)
      innerH = h
    })
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
  }, [scrollRef, opts.resetKey, setStickBoth, setUnseenBoth, toBottom])

  return { stick, unseen, jump }
}

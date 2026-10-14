import { useLayoutEffect, useRef, type RefObject } from 'react'

const SELECTED = '.active, .on, [aria-pressed="true"], [aria-current="page"]'

/** Slides one highlight between the children of a `.pill-group`: measures the selected child and
 *  writes --pill-x/y/w/h on the container (styles/press.css draws it). The first placement, and any
 *  re-placement after a resize, is instant; only a later change of selection animates. */
export function useSlidingPill<T extends HTMLElement>(): RefObject<T> {
  const ref = useRef<T>(null)
  const observed = useRef<T | null>(null)
  const ro = useRef<ResizeObserver | null>(null)

  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    const place = (): void => {
      const a = Array.from(el.children).find((c) => c.matches(SELECTED)) as HTMLElement | undefined
      const s = el.style
      if (!a || !a.offsetWidth) return void s.setProperty('--pill-o', '0')
      s.setProperty('--pill-x', `${a.offsetLeft}px`)
      s.setProperty('--pill-y', `${a.offsetTop}px`)
      s.setProperty('--pill-w', `${a.offsetWidth}px`)
      s.setProperty('--pill-h', `${a.offsetHeight}px`)
      s.setProperty('--pill-o', '1')
    }
    const arm = (): void => { requestAnimationFrame(() => { if (el.isConnected) el.dataset.pill = 'ready' }) }
    place()
    if (!el.dataset.pill) arm()
    if (observed.current !== el) {
      ro.current?.disconnect()
      observed.current = el
      ro.current = new ResizeObserver(() => { delete el.dataset.pill; place(); arm() })
      ro.current.observe(el)
    }
  })
  useLayoutEffect(() => () => ro.current?.disconnect(), [])
  return ref
}

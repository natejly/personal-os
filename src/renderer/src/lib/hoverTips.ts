/**
 * Hover hints drawn by the app instead of the OS. Native `title` tooltips are unreliable in the
 * frameless, vibrant window (they need key focus and never show inside drag regions), so one
 * delegated listener shows the element's `title`, or the aria-label (or icon-button text) of a button that has none.
 * The title moves to `data-tip` while hovered so the OS does not draw a second tooltip.
 */
const DELAY = 450

/** Text to show for an element: title wins; buttons fall back to their aria-label, or the visible text of an icon button. */
export const tipText = (el: Element): string => {
  const t = el.getAttribute('title')
  if (t) return t.trim()
  if (!el.matches('button, [role="button"]')) return ''
  const label = (el.getAttribute('aria-label') ?? '').trim()
  if (label) return label
  // an icon beside visible text still gets its name on hover
  const text = (el.textContent ?? '').trim()
  return el.querySelector('svg') && text.length <= 40 ? text : ''
}

/** Park the native title in `data-tip` while hovered, and put it back afterwards. */
export const lift = (el: Element) => {
  const t = el.getAttribute('title')
  if (t === null) return
  el.setAttribute('data-tip', t)
  el.removeAttribute('title')
}
export const restore = (el: Element) => {
  const t = el.getAttribute('data-tip')
  if (t === null) return
  // a title React set in the meantime wins over the parked one
  if (el.getAttribute('title') === null) el.setAttribute('title', t)
  el.removeAttribute('data-tip')
}

export function installHoverTips(doc: Document = document): () => void {
  let box: HTMLDivElement | null = null
  let timer: ReturnType<typeof setTimeout> | undefined
  let current: Element | null = null

  const hide = () => {
    clearTimeout(timer)
    if (current) restore(current)
    box?.remove()
    box = null
    current = null
  }
  const show = (el: Element, text: string) => {
    box?.remove()
    box = doc.createElement('div')
    box.className = 'hover-tip'
    box.setAttribute('role', 'tooltip')
    box.textContent = text
    doc.body.appendChild(box)
    const r = el.getBoundingClientRect()
    const w = box.offsetWidth
    const left = Math.max(6, Math.min(r.left + r.width / 2 - w / 2, window.innerWidth - w - 6))
    const below = r.bottom + 6 + box.offsetHeight < window.innerHeight
    box.style.left = `${left}px`
    box.style.top = `${below ? r.bottom + 6 : r.top - 6 - box.offsetHeight}px`
  }
  const over = (e: Event) => {
    const el = (e.target as Element | null)?.closest?.('[title], button[aria-label], [role="button"][aria-label]')
    if (!el || el === current) return
    hide()
    const text = tipText(el)
    if (!text) return
    lift(el)
    current = el
    timer = setTimeout(() => show(el, text), DELAY)
  }
  const out = (e: Event) => {
    const to = (e as MouseEvent).relatedTarget as Node | null
    if (current && to && current.contains(to)) return
    hide()
  }
  doc.addEventListener('mouseover', over, true)
  doc.addEventListener('mouseout', out, true)
  doc.addEventListener('mousedown', hide, true)
  doc.addEventListener('keydown', hide, true)
  doc.addEventListener('scroll', hide, true)
  return () => {
    hide()
    doc.removeEventListener('mouseover', over, true)
    doc.removeEventListener('mouseout', out, true)
    doc.removeEventListener('mousedown', hide, true)
    doc.removeEventListener('keydown', hide, true)
    doc.removeEventListener('scroll', hide, true)
  }
}

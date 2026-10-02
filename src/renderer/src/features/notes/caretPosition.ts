/**
 * Where is the caret on screen? The editor already paints a pixel-identical mirror behind the
 * textarea, and the mirror's text content equals the textarea's value character for character (the
 * highlighter only wraps text in spans). So the caret's position is the position of a collapsed Range
 * in the mirror: no off-screen clone, no dependency.
 */

/** Which text node, and the offset inside it, holds character `index` of the concatenated text. */
export function textPosition(lengths: number[], index: number): { node: number; offset: number } | null {
  if (lengths.length === 0) return null
  let rest = Math.max(0, index)
  for (let i = 0; i < lengths.length; i++) {
    // `<=` keeps the caret at the end of a node rather than the start of the next: the same pixel,
    // except across a line break, where the end of the earlier line is right.
    if (rest <= lengths[i]) return { node: i, offset: rest }
    rest -= lengths[i]
  }
  return { node: lengths.length - 1, offset: lengths[lengths.length - 1] }
}

export interface CaretRect { top: number; left: number; height: number }

/** Viewport rect of the caret at `index` inside `mirror`, or null when it cannot be measured. */
export function measureCaret(mirror: HTMLElement, index: number): CaretRect | null {
  const nodes: Text[] = []
  const walker = document.createTreeWalker(mirror, NodeFilter.SHOW_TEXT)
  for (let n = walker.nextNode(); n; n = walker.nextNode()) nodes.push(n as Text)
  const pos = textPosition(nodes.map((n) => n.length), index)
  if (!pos) return null
  const node = nodes[pos.node]
  const range = document.createRange()
  range.setStart(node, pos.offset)
  range.collapse(true)
  let r: DOMRect | undefined = range.getClientRects()[0]
  if (!r || (r.height === 0 && r.width === 0)) {
    // A collapsed range can report nothing (empty line, node edge). Borrow a neighbouring character.
    if (pos.offset > 0) {
      range.setStart(node, pos.offset - 1)
      range.setEnd(node, pos.offset)
      const b = range.getClientRects()[0]
      if (b) r = new DOMRect(b.right, b.top, 0, b.height)
    } else if (node.length > 0) {
      range.setStart(node, 0)
      range.setEnd(node, 1)
      const b = range.getClientRects()[0]
      if (b) r = new DOMRect(b.left, b.top, 0, b.height)
    }
  }
  return r ? { top: r.top, left: r.left, height: r.height } : null
}

/**
 * Put a popup of `size` under the caret, flipping above it when the room below is short and sliding
 * left when it would cross the right edge. `anchor` and `bounds` share one coordinate space.
 */
export function placePopup(
  anchor: CaretRect, size: { w: number; h: number }, bounds: { w: number; h: number }, gap = 4
): { top: number; left: number; flipped: boolean } {
  const below = anchor.top + anchor.height + gap
  const fitsBelow = below + size.h <= bounds.h
  const above = anchor.top - gap - size.h
  const flipped = !fitsBelow && above >= 0
  let top = flipped ? above : below
  top = Math.max(0, Math.min(top, Math.max(0, bounds.h - size.h)))
  const left = Math.max(0, Math.min(anchor.left, bounds.w - size.w))
  return { top, left, flipped }
}

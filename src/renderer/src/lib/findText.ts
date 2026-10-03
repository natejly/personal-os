export interface TextMatch { startSeg: number; startOff: number; endSeg: number; endOff: number }

/**
 * Find `query` (literal, case-insensitive) in the concatenation of `segments`, mapping every hit back to
 * (segment, offset) pairs so a match may begin in one text node and end in another. Case folding is done by
 * the regex flag rather than lowercasing, which can change a string's length and shift every offset.
 */
export function findMatches(segments: string[], query: string, cap = 2000): TextMatch[] {
  if (!query.trim()) return []
  const re = new RegExp(query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'gi')
  const starts: number[] = []
  let total = 0
  for (const s of segments) { starts.push(total); total += s.length }
  const text = segments.join('')
  // The segment holding position `pos`; an end position is looked up by its final character, so a match
  // ending exactly on a boundary stays in the earlier segment.
  const locate = (pos: number, isEnd: boolean): [number, number] => {
    const p = isEnd ? pos - 1 : pos
    let lo = 0
    let hi = segments.length - 1
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1
      if (starts[mid] <= p) lo = mid
      else hi = mid - 1
    }
    return [lo, pos - starts[lo]]
  }
  const out: TextMatch[] = []
  let m: RegExpExecArray | null
  while (out.length < cap && (m = re.exec(text))) {
    if (m[0].length === 0) { re.lastIndex++; continue }
    const [startSeg, startOff] = locate(m.index, false)
    const [endSeg, endOff] = locate(m.index + m[0].length, true)
    out.push({ startSeg, startOff, endSeg, endOff })
  }
  return out
}

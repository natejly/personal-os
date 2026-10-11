/**
 * Row windowing for the diff view. A whole-file rewrite of a long doc can be tens of thousands of
 * rows; building every one stalls the editor, so only the rows near the viewport are rendered and
 * two spacers stand in for the rest. Pure, so the arithmetic is testable without a DOM.
 */

/** One diff row's height in the unified/split body: 11.5px at line-height 1.5. */
export const DIFF_ROW_HEIGHT = 17

export interface RowWindow {
  /** First row to render. */
  start: number
  /** One past the last row to render. */
  end: number
  /** Spacer height standing in for the rows above `start`. */
  padTop: number
  /** Spacer height standing in for the rows from `end` down. */
  padBottom: number
}

/**
 * The slice of `total` rows to render for a viewport at `scrollTop`, plus `overscan` rows either
 * side so a fast scroll does not show blank space. The pads are the only thing sized by
 * `DIFF_ROW_HEIGHT`; a wrapped row is taller than one line, and the overscan absorbs that drift.
 */
export function rowWindow(total: number, scrollTop: number, viewport: number, overscan = 20): RowWindow {
  if (total <= 0) return { start: 0, end: 0, padTop: 0, padBottom: 0 }
  const top = Math.max(0, scrollTop)
  const height = Math.max(0, viewport)
  const first = Math.max(0, Math.floor(top / DIFF_ROW_HEIGHT) - overscan)
  const last = Math.min(total, Math.ceil((top + height) / DIFF_ROW_HEIGHT) + overscan)
  const end = Math.max(first + 1, last)
  return { start: first, end, padTop: first * DIFF_ROW_HEIGHT, padBottom: (total - end) * DIFF_ROW_HEIGHT }
}

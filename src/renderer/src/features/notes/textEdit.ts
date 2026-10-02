/**
 * Programmatic edits to a textarea that keep the native undo stack.
 *
 * Chromium records an edit for ⌘Z only when it comes from the editing commands (typing, paste,
 * `execCommand('insertText')`). Assigning `.value` or calling `setRangeText` replaces the text behind
 * the undo stack's back, so the next ⌘Z skips or discards it. We therefore select the range to change
 * and run `insertText` over it, and fall back to `setRangeText` only if the command is refused.
 * (Reasoned from documented Chromium behaviour; not exercised at runtime in this build.)
 */

/** The smallest single replacement that turns `a` into `b`: keep the common head and tail. */
export function diffRange(a: string, b: string): { start: number; end: number; text: string } {
  let head = 0
  const max = Math.min(a.length, b.length)
  while (head < max && a[head] === b[head]) head++
  let tail = 0
  while (tail < max - head && a[a.length - 1 - tail] === b[b.length - 1 - tail]) tail++
  return { start: head, end: a.length - tail, text: b.slice(head, b.length - tail) }
}

/** Replace [start, end) of the textarea and leave the selection at [selStart, selEnd] (default: after the text). */
export function replaceInTextarea(
  el: HTMLTextAreaElement, start: number, end: number, text: string, selStart?: number, selEnd?: number
): void {
  if (document.activeElement !== el) el.focus()
  el.setSelectionRange(start, end)
  let ok = false
  try {
    ok = text === ''
      ? (start === end ? true : document.execCommand('delete'))
      : document.execCommand('insertText', false, text)
  } catch { ok = false }
  if (!ok) {
    el.setRangeText(text, start, end, 'end')
    // setRangeText fires nothing; the host's onChange listens for `input`.
    el.dispatchEvent(new Event('input', { bubbles: true }))
  }
  const s = selStart ?? start + text.length
  el.setSelectionRange(s, selEnd ?? s)
}

/**
 * Insert at [start, end) without ever moving focus, for edits the user did not ask for at this moment
 * (dictation). When the textarea is the active element the normal undoable path is already focus-safe.
 * When it is not, the user is typing somewhere else, so the text goes in with `setRangeText` and a
 * dispatched `input` event: that loses the native undo entry for this one insert, which is the price
 * of leaving their focus and keystrokes alone. The textarea keeps its own selection while unfocused,
 * so `start`/`end` are the last known caret.
 */
export function insertWithoutFocus(el: HTMLTextAreaElement, start: number, end: number, text: string): void {
  if (document.activeElement === el) {
    replaceInTextarea(el, start, end, text)
    return
  }
  el.setRangeText(text, start, end, 'end')
  el.dispatchEvent(new Event('input', { bubbles: true }))
}

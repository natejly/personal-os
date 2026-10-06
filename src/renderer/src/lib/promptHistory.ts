/**
 * Up/Down recall of earlier prompts in the composer. Pure: the composer owns the state and the keys.
 *
 * The list is this conversation's user messages, newest first, with consecutive repeats collapsed.
 * The session's messages are already the active rows (superseded answers and inactive regenerate
 * variants never reach the renderer), so role is the only filter. Index -1 is the draft that was in
 * the box when recall began; Down past the newest prompt puts it back unchanged.
 */

export interface Recall {
  /** Position in the prompt list; -1 is the draft. */
  index: number
  /** The box's text when recall started. */
  draft: string
  /** What recall last put in the box: a different value means the user edited it. */
  shown: string
}

export function promptList(messages: ReadonlyArray<{ role: string; content: string }>): string[] {
  const out: string[] = []
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i]
    if (m.role !== 'user' || !m.content.trim()) continue
    if (out[out.length - 1] !== m.content) out.push(m.content)
  }
  return out
}

/**
 * Whether ArrowUp recalls rather than moving the caret: the selection is collapsed, and the box is
 * empty, the caret is at its very start, or it still holds an unedited recalled prompt with the caret
 * on its first line.
 */
export function canRecallUp(value: string, selStart: number, selEnd: number, recall: Recall | null): boolean {
  if (selStart !== selEnd) return false
  if (!value || selStart === 0) return true
  return !!recall && value === recall.shown && !value.slice(0, selStart).includes('\n')
}

/** ArrowDown steps forward only during recall, with the caret on the last line of an unedited prompt. */
export function canRecallDown(value: string, selStart: number, selEnd: number, recall: Recall | null): boolean {
  return !!recall && selStart === selEnd && value === recall.shown && !value.slice(selEnd).includes('\n')
}

/**
 * One step. Returns the next recall state (null once back on the draft) and the text to show, or
 * null when there is nowhere to go (Up at the oldest prompt, an empty history).
 */
export function step(list: string[], recall: Recall | null, text: string, dir: 'up' | 'down'): { recall: Recall | null; text: string } | null {
  const cur = recall ?? { index: -1, draft: text, shown: text }
  const index = cur.index + (dir === 'up' ? 1 : -1)
  if (index >= list.length || index < -1) return null
  if (index === -1) return { recall: null, text: cur.draft }
  return { recall: { index, draft: cur.draft, shown: list[index] }, text: list[index] }
}

/**
 * What a key does to recall. Escape only leaves recall when no reply is streaming: mid-reply it
 * stops the reply, and that must win. A modified arrow is caret movement, never recall.
 */
export function recallKey(
  key: string,
  ctx: { value: string; selStart: number; selEnd: number; streaming: boolean; modified: boolean },
  recall: Recall | null
): 'up' | 'down' | 'exit' | null {
  if (ctx.modified) return null
  if (key === 'ArrowUp') return canRecallUp(ctx.value, ctx.selStart, ctx.selEnd, recall) ? 'up' : null
  if (key === 'ArrowDown') return canRecallDown(ctx.value, ctx.selStart, ctx.selEnd, recall) ? 'down' : null
  if (key === 'Escape' && recall && !ctx.streaming) return 'exit'
  return null
}

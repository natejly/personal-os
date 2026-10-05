/** Pure pieces of the quick-ask bar: the message it sends and the title its chat gets. */

/** Clipboard text beyond this is cut, so a stray huge copy cannot blow the message limit. */
export const CLIP_MAX = 8000

/** The prompt, with the clipboard (when attached) as a fenced quote ahead of it. */
export function quickAskMessage(prompt: string, clipboard?: string | null): string {
  const p = prompt.trim()
  const clip = (clipboard ?? '').trim().slice(0, CLIP_MAX)
  if (!clip) return p
  // A fence longer than any backtick run inside the clip, so the quote cannot be closed early.
  const longest = Math.max(2, ...(clip.match(/`+/g) ?? []).map((r) => r.length))
  const fence = '`'.repeat(longest + 1)
  return `${fence}\n${clip}\n${fence}\n\n${p}`
}

/** First line of the prompt, shortened at a word boundary. */
export function quickAskTitle(prompt: string, max = 60): string {
  const line = prompt.trim().split('\n')[0].replace(/\s+/g, ' ')
  if (line.length <= max) return line || 'Quick ask'
  const cut = line.slice(0, max - 1)
  const sp = cut.lastIndexOf(' ')
  return `${(sp > max / 2 ? cut.slice(0, sp) : cut).trimEnd()}…`
}

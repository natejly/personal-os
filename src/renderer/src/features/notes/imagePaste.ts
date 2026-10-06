export const PENDING_ALT = 'describing image...'

/** The markdown inserted at the caret the moment the image is stored; the alt is swapped when the description arrives. */
export const pendingImage = (url: string): string => `![${PENDING_ALT}](${url})`

/** Replace the pending alt of the image at `url`, only if the text there is still ours. Null when it was edited away. */
export function withAlt(text: string, url: string, alt: string): { start: number; end: number; text: string } | null {
  const at = text.indexOf(pendingImage(url))
  if (at < 0) return null
  return { start: at + 2, end: at + 2 + PENDING_ALT.length, text: alt.replace(/[[\]\n]/g, ' ').trim() || 'image' }
}

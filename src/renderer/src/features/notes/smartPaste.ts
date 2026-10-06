/**
 * Pasting a URL over selected text makes a link of the selection: `[text](url)`. With nothing selected the URL
 * is its own label, `[url](url)`, which the editor later swaps for the page title. Null leaves the paste alone.
 */
export function linkFromPaste(selection: string, clipboard: string): string | null {
  const url = clipboard.trim()
  if (/\n/.test(selection)) return null
  if (!/^https?:\/\/\S+$/i.test(url)) return null
  // A bare ")" would end the link early.
  return `[${selection.trim() ? selection : url}](${url.replace(/\(/g, '%28').replace(/\)/g, '%29')})`
}

export const IMAGE_MIMES = ['image/png', 'image/jpeg', 'image/gif', 'image/webp']
export const IMAGE_MAX_BYTES = 8 * 1024 * 1024

/** The first pasted/dropped image, or why it was refused. Null when there is no image file at all. */
export function pickImage(files: ArrayLike<{ type: string; size: number }> | null | undefined): { ok: true; index: number } | { ok: false; reason: string } | null {
  const list = Array.from(files ?? [])
  const i = list.findIndex((f) => f.type.startsWith('image/'))
  if (i < 0) return null
  const f = list[i]
  if (!IMAGE_MIMES.includes(f.type)) return { ok: false, reason: 'Only png, jpeg, gif and webp images can be added.' }
  if (f.size > IMAGE_MAX_BYTES) return { ok: false, reason: 'Images are limited to 8 MB.' }
  return { ok: true, index: i }
}

/** Swap the label of the `[url](url)` link that starts at `at` for the page title, only if the text there is still ours. */
export function withTitle(text: string, at: number, url: string, title: string): { start: number; end: number; text: string } | null {
  const clean = title.replace(/[[\]\n]/g, ' ').trim()
  if (!clean || text.slice(at, at + url.length + 2) !== `[${url}]`) return null
  return { start: at + 1, end: at + 1 + url.length, text: clean }
}

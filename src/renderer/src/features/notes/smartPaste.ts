/** Pasting a URL over selected text makes a link of the selection: `[text](url)`. Null leaves the paste alone. */
export function linkFromPaste(selection: string, clipboard: string): string | null {
  const url = clipboard.trim()
  if (!selection.trim() || /\n/.test(selection)) return null
  if (!/^https?:\/\/\S+$/i.test(url)) return null
  // A bare ")" would end the link early.
  return `[${selection}](${url.replace(/\(/g, '%28').replace(/\)/g, '%29')})`
}

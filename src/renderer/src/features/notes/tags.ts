/** `#tag` extraction. Mirrored by `extract_tags` in backend/personal_os/docs.py; keep the two in step. */

export const TAG_BODY = '[\\p{L}][\\p{L}\\p{N}_/-]*'
const TAG = new RegExp(`(?:^|\\s)#(${TAG_BODY})`, 'gu')

/** Distinct lowercase tags in first-seen order. Fences, `$$` blocks, headings and inline code are skipped. */
export function extractTags(src: string): string[] {
  const seen = new Set<string>()
  let fence: string | null = null
  let math = false
  for (const line of src.split('\n')) {
    if (fence !== null) {
      if (line.trimStart().startsWith(fence)) fence = null
      continue
    }
    const open = /^\s*(```+|~~~+)/.exec(line)
    if (open) { fence = open[1].slice(0, 3); continue }
    if (math) { if (line.includes('$$')) math = false; continue }
    if (/^\s*\$\$\s*$/.test(line)) { math = true; continue }
    if (/^\s{0,3}#{1,6}(\s|$)/.test(line)) continue
    for (const m of line.replace(/`[^`\n]*`/g, ' ').matchAll(TAG)) {
      const t = m[1].replace(/[-/]+$/, '').toLowerCase()
      if (t) seen.add(t)
    }
  }
  return [...seen]
}

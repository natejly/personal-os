/** Pure helpers for the artifact card's "Open in space" action and the viewer's SVG export. */

export function openInSpaceArgs(ref: { id: string; title?: string }): { kind: 'artifact'; refId: string; title: string } {
  return { kind: 'artifact', refId: ref.id, title: (ref.title ?? '').trim() }
}

/** True when the source is a single <svg> element and nothing else (an xml prolog or comments aside).
 *  ponytail: nested <svg> counts as "not svg only"; fine, those just get no SVG button. */
export function isSvgOnly(code: string): boolean {
  const s = code.replace(/^\s*<\?xml[^>]*\?>/i, '').replace(/<!--[\s\S]*?-->/g, '').trim()
  return /^<svg[\s>]/i.test(s) && /<\/svg>$/i.test(s) && (s.match(/<svg[\s>]/gi) ?? []).length === 1
}

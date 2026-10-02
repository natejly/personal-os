/** A filename is data in the message the user sends. It must stay on one line, inside quotes. */
export function uploadContextNote(names: string[]): string {
  const shown = names
    .map((n) => n.replace(/[\u0000-\u001f\u007f\u2028\u2029"]/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 180))
    .filter(Boolean)
    .map((n) => `"${n}"`)
  if (!shown.length) return 'I uploaded files for context. Read them with search_documents before answering.'
  return `I uploaded these files for context: ${shown.join(', ')}. Read them with search_documents before answering.`
}

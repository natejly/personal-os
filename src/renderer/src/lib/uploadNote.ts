/** What one upload came back as: the server says whether it could read anything out of the file. */
export interface UploadOutcome {
  name: string
  /** False when the stored text is empty or only the no-text marker; a chat cannot read such a file. */
  readable: boolean
  reason?: string | null
}

/** A filename is data in the message the user sends. It must stay on one line, inside quotes. */
export function uploadContextNote(names: string[]): string {
  const shown = names
    .map((n) => n.replace(/[\u0000-\u001f\u007f\u2028\u2029"]/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 180))
    .filter(Boolean)
    .map((n) => `"${n}"`)
  if (!shown.length) return 'I uploaded files for context. Read them with search_documents before answering.'
  return `I uploaded these files for context: ${shown.join(', ')}. Read them with search_documents before answering.`
}

/** The toast for one stored upload: a plain "Uploaded" only when the assistant can actually read it. */
export function uploadToast(r: UploadOutcome): { text: string; kind: 'info' | 'error' } {
  if (r.readable) return { text: `Uploaded ${r.name}`, kind: 'info' }
  return { text: `${r.name} was saved, but it has no readable text. The assistant cannot see what is in it.`, kind: 'error' }
}

/**
 * What a batch of uploads puts in the composer and on screen. The note names only the readable
 * files; when none are, there is no note, so the model is not sent to read a file that is empty.
 */
export function uploadNote(results: UploadOutcome[]): { note: string | null; toasts: { text: string; kind: 'info' | 'error' }[] } {
  const readable = results.filter((r) => r.readable).map((r) => r.name)
  return { note: readable.length ? uploadContextNote(readable) : null, toasts: results.map(uploadToast) }
}

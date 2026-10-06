import type { Attachment } from '@shared/types'

/** Largest file one upload may be. Mirrors MAX_UPLOAD_MB in backend/personal_os/limits.py, which enforces it (413). */
export const MAX_UPLOAD_MB = 50
export const MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024

/** The refusal for a file over the cap, worded like the server's 413; null when it may be sent. */
export function uploadTooBig(size: number | null | undefined): string | null {
  return typeof size === 'number' && size > MAX_UPLOAD_BYTES ? `Files must be ${MAX_UPLOAD_MB} MB or smaller` : null
}

/** What one upload came back as: the server says whether it could read anything out of the file. */
export interface UploadOutcome extends Attachment {
  /** False when the stored text is empty or only the no-text marker; a chat cannot read such a file. */
  readable: boolean
  reason?: string | null
}

/** The toast for one stored upload: a plain "Uploaded" only when the assistant can actually read it. */
export function uploadToast(r: UploadOutcome): { text: string; kind: 'info' | 'error' } {
  if (r.readable) return { text: `Uploaded ${r.name}`, kind: 'info' }
  return { text: `${r.name} was saved, but it has no readable text. The assistant cannot see what is in it.`, kind: 'error' }
}

/**
 * What a batch of uploads leaves on the composer and on screen. Only the readable files become
 * attachments of the next send; an unreadable one is stored, warned about, and not attached, so
 * the model is never handed a file that is empty.
 */
export function uploadNote(results: UploadOutcome[]): { files: Attachment[]; toasts: { text: string; kind: 'info' | 'error' }[] } {
  const files = results.filter((r) => r.readable).map(({ id, name, mime, size }) => ({ id, name, mime, size }))
  return { files, toasts: results.map(uploadToast) }
}

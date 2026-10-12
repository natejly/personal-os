/** A failed request. `status` is the HTTP code for 'http'; the other kinds never reached a response. */
export class ApiError extends Error {
  status: number | null
  kind: 'http' | 'timeout' | 'stalled'
  constructor(message: string, opts?: { status?: number | null; kind?: ApiError['kind'] }) {
    super(message)
    this.name = 'ApiError'
    this.status = opts?.status ?? null
    this.kind = opts?.kind ?? 'http'
  }
}

/** The message for a failed response: FastAPI's `detail` when it is text, its field errors in a line when it is a list, else words for the status. */
export function detailText(detail: unknown, status: number, statusText = ''): string {
  if (typeof detail === 'string' && detail) return detail
  if (Array.isArray(detail) && detail.length) {
    return detail.map((d: { loc?: unknown[]; msg?: string }) => {
      const field = Array.isArray(d?.loc) ? d.loc.filter((x) => x !== 'body' && x !== 'query').join('.') : ''
      return field ? `${field}: ${d?.msg ?? 'invalid'}` : String(d?.msg ?? 'invalid')
    }).join('; ')
  }
  if (status === 404) return 'Not found.'
  if (status === 401 || status === 403) return 'Not allowed.'
  if (status >= 500) return 'Something went wrong in the backend. Try again.'
  return statusText ? `Request failed: ${statusText}.` : 'The request failed.'
}

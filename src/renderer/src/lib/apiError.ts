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

export type ErrorActionKind = 'retry' | 'settings' | 'models' | 'compact'

/** The one button an error class earns. Filtered or malformed requests get none: retrying the same text cannot help. */
export function errorAction(kind: string | null | undefined): { label: string; action: ErrorActionKind } | null {
  switch (kind) {
    case 'rate_limit':
    case 'overloaded':
    case 'server':
    case 'transport':
      return { label: 'Retry', action: 'retry' }
    case 'quota':
    case 'auth':
    case 'not_found':
      return { label: 'Open Settings', action: 'settings' }
    case 'unsupported_param':
      return { label: 'Pick a model', action: 'models' }
    case 'overflow':
      return { label: 'Compact and retry', action: 'compact' }
    default:
      return null
  }
}

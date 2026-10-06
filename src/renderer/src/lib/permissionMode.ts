/** The one global switch for how Grain handles risky actions. Pure: no store import. */
export type PermissionMode = 'auto' | 'manual' | 'allow_all'

export const MODES: { id: PermissionMode; label: string; pill: string; description: string }[] = [
  { id: 'auto', label: 'Auto', pill: 'Auto', description: 'Auto — Grain checks each risky action with a second AI first and only asks you when unsure (recommended)' },
  { id: 'manual', label: 'Manual', pill: 'Manual', description: 'Manual — Grain asks before any risky action, including writes to system areas like /etc and /Library' },
  { id: 'allow_all', label: 'Allow everything', pill: 'Allow everything', description: 'Allow everything — runs everything except denied calls, with no checks or questions. Grain\'s own data and app stay off limits; credential stores and writes right after untrusted content still ask (dangerous)' }
]

/** The mode in effect; a missing or unknown value is Auto. */
export function modeOf(s: { permissionMode?: unknown } | null | undefined): PermissionMode {
  const m = s?.permissionMode
  return m === 'manual' || m === 'allow_all' ? m : 'auto'
}

/** Only widening to Allow everything needs a second click. */
export const needsConfirm = (from: PermissionMode, to: PermissionMode): boolean => to === 'allow_all' && from !== 'allow_all'

export const pillLabel = (m: PermissionMode): string => MODES.find((x) => x.id === m)?.pill ?? 'Auto'

/** The one global switch for how Grain handles risky actions. Pure: no store import. */
export type PermissionMode = 'auto' | 'manual' | 'allow_all'

export const MODES: { id: PermissionMode; label: string; pill: string; description: string }[] = [
  { id: 'auto', label: 'Auto', pill: 'Auto', description: 'Auto — a second AI checks each risky action and you are asked only when it is unsure (recommended)' },
  { id: 'manual', label: 'Manual', pill: 'Manual', description: 'Manual — you are asked before every risky action' },
  { id: 'allow_all', label: 'Allow everything', pill: 'Allow everything', description: 'Allow everything — runs without checks or questions. Still asks only for Grain\'s own data and app, passwords and keys, permanent deletes, disk wipes, force-pushes, and sending email (dangerous)' }
]

/** The mode in effect; a missing or unknown value is Auto. */
export function modeOf(s: { permissionMode?: unknown } | null | undefined): PermissionMode {
  const m = s?.permissionMode
  return m === 'manual' || m === 'allow_all' ? m : 'auto'
}

/** Only widening to Allow everything needs a second click. */
export const needsConfirm = (from: PermissionMode, to: PermissionMode): boolean => to === 'allow_all' && from !== 'allow_all'

export const pillLabel = (m: PermissionMode): string => MODES.find((x) => x.id === m)?.pill ?? 'Auto'

/** Hover text and accessible name for the composer pill. Under Allow everything the red pill is the only cue, so it says so. */
export const pillTitle = (m: PermissionMode): string =>
  m === 'allow_all'
    ? 'Dangerously allow all is on: Grain acts without asking. It still asks before permanent deletes, disk wipes, force-pushes and sending email. Click to change it in Settings.'
    : `Permission mode: ${pillLabel(m)}. Click to change it in Settings.`

/** Second composer pill, independent of the mode: shown only while "Allow all domains and MCP servers" is on. */
export const ALL_CONNECTIONS_LABEL = 'All domains + MCP'
export const allConnectionsTitle = 'All domains and MCP servers are allowed: fetching, browsing, shell network and every connector tool run without a host list or per-tool approval. Click to change it in Settings.'
export const allConnectionsOn = (s: { allowAllConnections?: unknown } | null | undefined): boolean => s?.allowAllConnections === true

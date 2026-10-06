import type { Settings } from '@shared/types'

/**
 * Pure rules behind the Cowork settings section: what a hostname entry may look like, and how the three-way
 * network choice maps onto the backend flags. Kept out of the component so both directions are tested.
 */

/** Why `raw` cannot be an allowed host, or null when it can. Hostnames only: the proxy matches a name, never an address or pattern. */
export function hostError(raw: string): string | null {
  const h = raw.trim()
  if (!h) return 'Enter a hostname such as pypi.org.'
  if (/^[a-z][a-z0-9+.-]*:\/\//i.test(h)) return 'Leave out the scheme (https://) and give just the hostname.'
  if (/[/?#]/.test(h)) return 'Give just the hostname, without a path.'
  if (h.includes('*')) return 'Wildcards are not supported. A hostname also allows its subdomains.'
  if (/\s/.test(h)) return 'A hostname has no spaces.'
  if (h.includes(':') || h.includes('@')) return 'Leave out the port and any user name.'
  if (/^[\d.]+$/.test(h)) return 'IP addresses are not allowed. Use a hostname.'
  if (!/^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$/i.test(h.replace(/^\./, ''))) return 'That is not a valid hostname.'
  return null
}

/** Lower-cased, leading dot dropped: the form that is stored. */
export const normalizeHost = (raw: string): string => raw.trim().toLowerCase().replace(/^\./, '')

export type NetworkMode = 'off' | 'registries' | 'open'

type NetSettings = Pick<Settings, 'shellNetwork' | 'shellRegistryAccess' | 'shellAllowedDomains'>

/** Settings -> the choice shown. `shellNetwork` wins: open is open whatever the registry flag says. Missing flags read as the backend defaults (registries on). */
export function networkMode(s: NetSettings): NetworkMode {
  if (s.shellNetwork === true) return 'open'
  if (s.shellRegistryAccess === false && !(s.shellAllowedDomains ?? []).length) return 'off'
  return 'registries'
}

/** The choice -> the settings it writes. Off clears the host list too: with no registries and no hosts nothing is reachable. */
export function networkPatch(mode: NetworkMode, hosts: string[] = []): NetSettings {
  if (mode === 'off') return { shellNetwork: false, shellRegistryAccess: false, shellAllowedDomains: [] }
  if (mode === 'open') return { shellNetwork: true, shellRegistryAccess: true, shellAllowedDomains: hosts }
  return { shellNetwork: false, shellRegistryAccess: true, shellAllowedDomains: hosts }
}

export type SandboxNetMode = 'off' | 'proxy' | 'open'

/** The stored sandboxNetwork value as the choice shown. A stored true (the old on/off switch) is open; anything unknown is off. */
export function sandboxNetMode(v: Settings['sandboxNetwork']): SandboxNetMode {
  if (v === true) return 'open'
  return v === 'proxy' || v === 'open' ? v : 'off'
}

/** Limits the backend enforces (`NUMERIC_SETTING_RANGES`); keys it leaves out are only non-negative. */
const RANGES: Record<string, [number, number]> = { browserMaxTabs: [1, 12] }

/** Clamp a typed number into its range; blank or NaN falls back to `fallback`. */
export function clampSetting(key: string, raw: string, fallback: number): number {
  const n = Number(raw)
  if (raw.trim() === '' || !Number.isFinite(n)) return fallback
  const [lo, hi] = RANGES[key] ?? [0, Infinity]
  return Math.min(hi, Math.max(lo, n))
}

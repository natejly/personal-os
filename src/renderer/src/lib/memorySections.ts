import type { Memory } from '@shared/types'

/** Mirrors the backend profile: pinned rows and standing preferences/instructions, pins first. */
const isProfile = (m: Memory): boolean => !!m.pinned || m.kind === 'preference' || m.kind === 'instruction'
const when = (m: Memory): number => m.valid_from ?? m.created_at

/** Splits live memories into the three lists the panel shows. Each row lands in exactly one: profile, then notes (expiring), then log. */
export function memorySections(rows: Memory[]): { profile: Memory[]; notes: Memory[]; log: Memory[] } {
  const profile = rows.filter(isProfile)
  const rest = rows.filter((m) => !isProfile(m))
  return {
    profile: [...profile.filter((m) => m.pinned), ...profile.filter((m) => !m.pinned)],
    notes: rest.filter((m) => m.expires_at != null).sort((a, b) => a.expires_at! - b.expires_at!),
    log: rest.filter((m) => m.expires_at == null).sort((a, b) => when(b) - when(a)),
  }
}

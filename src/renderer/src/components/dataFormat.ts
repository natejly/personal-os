import type { BackupInfo } from '@shared/types'

export const KIND_LABEL: Record<BackupInfo['kind'], string> = {
  daily: 'Daily',
  manual: 'Manual',
  premigrate: 'Before update',
  prerestore: 'Before restore'
}

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}

/** "just now", "5 min ago", "3 h ago", else the date: what the last-backup line says. */
export function ago(ts: number, now = Date.now() / 1000): string {
  const s = Math.max(0, now - ts)
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.floor(s / 60)} min ago`
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`
  return new Date(ts * 1000).toLocaleDateString()
}

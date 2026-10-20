/** The last sync, as small header text; clicking it syncs again. */
export default function SyncStatus({ at, busy, label, onRefresh }: { at: number | null; busy: boolean; label: string; onRefresh: () => void }): JSX.Element {
  const time = at ? new Date(at).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }) : ''
  return (
    <button type="button" className="sync-status" aria-label={`Sync ${label}`} title={`Sync ${label}`} onClick={onRefresh} disabled={busy}>
      {busy ? 'Syncing…' : time ? `Synced ${time}` : 'Sync'}
    </button>
  )
}

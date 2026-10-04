import { useCallback, useEffect, useState } from 'react'
import { Download, FolderOpen, History, RotateCcw } from 'lucide-react'
import { useStore } from '../store'
import { api } from '../lib/api'
import type { DataOverview } from '@shared/types'
import { KIND_LABEL, ago, formatBytes } from './dataFormat'

/**
 * Settings → Data: backups, restore and export. Everything lives in one SQLite file plus uploads/, so
 * "Back up now" and the daily snapshot are the safety net and "Export all data" is the way out.
 * Restore never swaps the live database: it stages a choice the backend applies at its next start.
 */
export default function DataSettings(): JSX.Element {
  const toast = useStore((s) => s.toast)
  const [info, setInfo] = useState<DataOverview | null>(null)
  const [loadError, setLoadError] = useState(false)
  const [busy, setBusy] = useState<'backup' | 'export' | null>(null)

  const refresh = useCallback(async (): Promise<void> => {
    try { setInfo(await api.data.overview()); setLoadError(false) } catch (e) { setLoadError(true); toast((e as Error).message, 'error') }
  }, [toast])
  useEffect(() => { void refresh() }, [refresh])

  const backUp = async (): Promise<void> => {
    setBusy('backup')
    try { await api.data.backUp(); toast('Backup created') } catch (e) { toast((e as Error).message, 'error') }
    setBusy(null)
    void refresh()
  }

  const restore = async (name: string, when: string): Promise<void> => {
    if (!window.confirm(`Restore the backup from ${when}?\n\nGrain will replace your current data with it the next time it starts. Your current data is saved as a backup first, so this can be undone. Nothing changes until you restart.`)) return
    try {
      await api.data.restore(name)
      void refresh()
      if (window.confirm('Restore is ready. Restart Grain now to apply it?')) await window.os.data.relaunch()
    } catch (e) {
      toast((e as Error).message, 'error')
    }
  }

  const cancelRestore = async (): Promise<void> => {
    try { await api.data.cancelRestore() } catch (e) { toast((e as Error).message, 'error') }
    void refresh()
  }

  const exportAll = async (): Promise<void> => {
    const dest = await window.os.data.chooseExportPath()
    if (!dest) return
    setBusy('export')
    try {
      const r = await api.data.exportTo(dest)
      toast(`Exported ${formatBytes(r.size)}`)
      void window.os.data.reveal(r.path.replace(/[^/\\]+$/, ''))
    } catch (e) {
      toast((e as Error).message, 'error')
    }
    setBusy(null)
  }

  return (
    <section>
      <h3>Data</h3>
      <p className="muted">Grain keeps a database plus folders of files (uploads, pasted images, kept meeting audio, desk outputs) on this Mac. Backups snapshot the database only, daily and before any update that changes its structure, keeping the newest of each. Export also takes the files.</p>

      <div className="data-row">
        <span className="toggle-text">
          <b>Last backup</b>
          <small>{info ? (info.last_backup ? ago(info.last_backup) : 'No backup yet') : loadError ? 'Could not load' : 'Loading…'}</small>
        </span>
        <button className="ghost-btn" onClick={() => void backUp()} disabled={busy !== null}><History size={14} /> {busy === 'backup' ? 'Backing up…' : 'Back up now'}</button>
      </div>

      {info?.pending_restore && (
        <p className="muted" role="status">
          A restore is waiting for the next start. <button className="ghost-btn" onClick={() => void window.os.data.relaunch()}>Restart now</button>{' '}
          <button className="ghost-btn" onClick={() => void cancelRestore()}>Cancel restore</button>
        </p>
      )}

      {info?.restore_failed && !info.pending_restore && (
        <p className="muted" role="alert">
          The restore staged before the last start could not be applied ({info.restore_failed.error}), so your data was left as it was.{' '}
          <button className="ghost-btn" onClick={() => void cancelRestore()}>Dismiss</button>
        </p>
      )}

      {info && info.backups.length > 0 && (
        <ul className="data-list" aria-label="Backups">
          {info.backups.map((b) => {
            const when = new Date(b.created_at * 1000).toLocaleString()
            return (
              <li key={b.name} className="data-row">
                <span className="toggle-text"><b>{when}</b><small>{KIND_LABEL[b.kind] ?? b.kind} · {formatBytes(b.size)}</small></span>
                <button className="ghost-btn" onClick={() => void restore(b.name, when)}><RotateCcw size={14} /> Restore…</button>
              </li>
            )
          })}
        </ul>
      )}

      <div className="data-row">
        <span className="toggle-text"><b>Export all data</b><small>A zip with a full database copy, your uploads, pasted images, kept meeting audio, desk outputs, and conversations, memories and files as readable Markdown and JSON. API keys and tokens stay in your Keychain and are not included. The export still holds your personal data, so keep it private.</small></span>
        <button className="ghost-btn" onClick={() => void exportAll()} disabled={busy !== null}><Download size={14} /> {busy === 'export' ? 'Exporting…' : 'Export all data…'}</button>
      </div>

      {info && (
        <div className="data-row">
          <span className="toggle-text"><b>Data folder</b><small>{info.data_dir}</small></span>
          <button className="ghost-btn" onClick={() => void window.os.data.reveal(info.data_dir)}><FolderOpen size={14} /> Show in Finder</button>
        </div>
      )}
    </section>
  )
}

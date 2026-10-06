import type { Settings } from '@shared/types'
import { HostList } from './CoworkSettings'

type Props = { draft: Settings; patch: (p: Partial<Settings>) => void }

/**
 * Two standing safety settings of Settings > Advanced: which hosts fetch_url may still read after a reply has seen
 * untrusted content, and folder snapshots before a reply edits. It edits the modal's `draft` through `patch`, so
 * nothing is saved until Save. The permission mode itself lives in PermissionMode.tsx.
 */
export default function RunSafetySettings({ draft, patch }: Props): JSX.Element {
  return (
    <HostList title="Allowed hosts after reading untrusted content"
      help="Once a reply has read a web page, mail or another outside source, it may only fetch links you or a search gave it, plus these hosts. A name also allows its subdomains. The agent's browser also uses its own Allowed sites list."
      value={draft.fetchAllowlist ?? []} onChange={(fetchAllowlist) => patch({ fetchAllowlist })} />
  )
}

export function SnapshotToggle({ draft, patch }: Props): JSX.Element {
  const snapOk = draft.snapshotsAvailable !== false
  return (
    <label className="toggle-row plain">
      <span className="toggle-text"><b>Snapshot folders before changes</b>
        <small>{snapOk
          ? 'Lets Undo reverse shell effects too. Keeps a copy before a reply first changes a folder.'
          : 'Unavailable on this Mac: folder snapshots need git installed (xcode-select --install).'}</small></span>
      <input type="checkbox" disabled={!snapOk} checked={snapOk && draft.snapshotsEnabled !== false}
        onChange={(e) => patch({ snapshotsEnabled: e.target.checked })} /><span className="switch" />
    </label>
  )
}

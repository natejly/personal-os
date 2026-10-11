import type { Settings } from '@shared/types'
import { HostList } from './CoworkSettings'

type Props = { draft: Settings; patch: (p: Partial<Settings>) => void }

/**
 * Standing safety settings in Settings > Permissions: which hosts fetch_url may still read after a reply has seen
 * untrusted content, and whether such a reply may run networked commands and write outside its desk without a card.
 * (Snapshot folders, the third of these, lives under Advanced.) They edit the modal's `draft` through `patch`, so
 * nothing is saved until Save. The permission mode itself lives in PermissionMode.tsx.
 */
export default function RunSafetySettings({ draft, patch }: Props): JSX.Element {
  return (
    <>
      <HostList title="Allowed hosts after reading untrusted content"
        help="Once a reply has read a web page, mail or another outside source, it may only fetch links you or a search gave it, plus these hosts. A name also allows its subdomains. The agent's browser also uses its own Allowed sites list."
        value={draft.fetchAllowlist ?? []} onChange={(fetchAllowlist) => patch({ fetchAllowlist })} />
      <TrustExternalToggle draft={draft} patch={patch} />
    </>
  )
}

/** Off by default. Its help text says plainly that it reduces protection: a reply that has read outside content can
 *  then run networked commands and write outside its desk without a card, while the hard blocks still ask. */
export function TrustExternalToggle({ draft, patch }: Props): JSX.Element {
  return (
    <label className={`toggle-row plain ${draft.trustExternalContent ? 'danger' : ''}`}>
      <span className="toggle-text"><b>Trust content from outside Grain</b>
        <small>Off by default. Lets a reply that has read content from outside Grain run commands and touch files without asking each time. Credential stores, Grain&apos;s own files and destructive commands still ask.</small></span>
      <input type="checkbox" aria-label="Trust content from outside Grain" checked={!!draft.trustExternalContent}
        onChange={(e) => patch({ trustExternalContent: e.target.checked })} /><span className="switch" />
    </label>
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

import type { Settings } from '@shared/types'
import { HostList } from './CoworkSettings'
import Switch from './settings/Switch'

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
        help="After reading outside content, a reply may only fetch links you or a search gave it, plus these hosts. A name also allows its subdomains."
        value={draft.fetchAllowlist ?? []} onChange={(fetchAllowlist) => patch({ fetchAllowlist })} />
      <TrustExternalToggle draft={draft} patch={patch} />
    </>
  )
}

/** Off by default. Its help text says plainly that it reduces protection: a reply that has read outside content can
 *  then run networked commands and write outside its desk without a card, while the hard blocks still ask. */
export function TrustExternalToggle({ draft, patch }: Props): JSX.Element {
  return (
    <Switch danger={!!draft.trustExternalContent} title="Trust content from outside Grain" label="Trust content from outside Grain"
      help="Off by default. Lets a reply that has read content from outside Grain run commands and touch files without asking each time. Credential stores, Grain's own files and destructive commands still ask."
      checked={!!draft.trustExternalContent} onChange={(trustExternalContent) => patch({ trustExternalContent })} />
  )
}

export function SnapshotToggle({ draft, patch }: Props): JSX.Element {
  const snapOk = draft.snapshotsAvailable !== false
  return (
    <Switch title="Snapshot folders before changes" disabled={!snapOk} checked={snapOk && draft.snapshotsEnabled !== false} onChange={(snapshotsEnabled) => patch({ snapshotsEnabled })}
      help={snapOk
        ? 'Lets Undo reverse shell effects too. Keeps a copy before a reply first changes a folder.'
        : 'Unavailable on this Mac: folder snapshots need git installed (xcode-select --install).'} />
  )
}

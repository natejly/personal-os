import type { Settings } from '@shared/types'
import { HostList } from './CoworkSettings'
import Switch from './settings/Switch'

type Props = { draft: Settings; patch: (p: Partial<Settings>) => void }

/** Standing safety settings in Settings > Permissions; they edit the draft and save with Save. */
export default function RunSafetySettings({ draft, patch }: Props): JSX.Element {
  return (
    <>
      <HostList title="Allowed hosts after reading untrusted content"
        value={draft.fetchAllowlist ?? []} onChange={(fetchAllowlist) => patch({ fetchAllowlist })} />
      <TrustExternalToggle draft={draft} patch={patch} />
    </>
  )
}

/** Off by default; turning it on reduces protection. */
export function TrustExternalToggle({ draft, patch }: Props): JSX.Element {
  return (
    <Switch danger={!!draft.trustExternalContent} title="Trust content from outside Grain" label="Trust content from outside Grain"
      checked={!!draft.trustExternalContent} onChange={(trustExternalContent) => patch({ trustExternalContent })} />
  )
}

export function SnapshotToggle({ draft, patch }: Props): JSX.Element {
  const snapOk = draft.snapshotsAvailable !== false
  return (
    <Switch title="Snapshot folders before changes" disabled={!snapOk} checked={snapOk && draft.snapshotsEnabled !== false} onChange={(snapshotsEnabled) => patch({ snapshotsEnabled })}
      help={snapOk ? undefined : 'Unavailable on this Mac: install git (xcode-select --install)'} />
  )
}

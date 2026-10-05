import type { Settings } from '@shared/types'
import { HostList } from './CoworkSettings'

/**
 * Three standing safety settings of the Tools tab: which hosts fetch_url may still read after a reply has seen
 * untrusted content, whether a run nobody is watching asks or refuses, and folder snapshots before a reply edits.
 * It edits the modal's `draft` through `patch`, so nothing is saved until Save.
 */
export default function RunSafetySettings({ draft, patch }: { draft: Settings; patch: (p: Partial<Settings>) => void }): JSX.Element {
  const unattended = draft.unattendedApprovals ?? 'deny'
  const review = draft.autoReview ?? 'off'
  const snapOk = draft.snapshotsAvailable !== false
  return (
    <>
      <HostList title="Allowed hosts after reading untrusted content"
        help="Once a reply has read a web page, mail or another outside source, it may only fetch links you or a search gave it, plus these hosts. A name also allows its subdomains; chats working autonomously also use the Allowed sites list under Autonomy."
        value={draft.fetchAllowlist ?? []} onChange={(fetchAllowlist) => patch({ fetchAllowlist })} />
      <div className="send-hold">
        <span className="toggle-text"><b>Unattended runs</b><small>What a scheduled job or other run with nobody watching does with a call that would ask for approval.</small></span>
        <div className="seg" role="group" aria-label="Unattended runs">
          <button type="button" className={unattended === 'ask' ? 'on' : ''} aria-pressed={unattended === 'ask'} onClick={() => patch({ unattendedApprovals: 'ask' })}>Ask</button>
          <button type="button" className={unattended === 'deny' ? 'on' : ''} aria-pressed={unattended === 'deny'} onClick={() => patch({ unattendedApprovals: 'deny' })}>Refuse</button>
        </div>
        <p className="muted small">Ask leaves an approval card waiting until you answer. Refuse turns the call down and the run carries on without it.</p>
      </div>
      <div className="send-hold">
        <span className="toggle-text"><b>Review gate</b><small>A second model looks at a call before it runs on its own and can turn it into an approval card. Your ask and deny rules always win, and an ask from the reviewer is never skipped by an allow rule.</small></span>
        <div className="seg" role="group" aria-label="Review gate">
          {([['off', 'Off'], ['risky', 'Risky calls'], ['all-writes', 'All writes']] as const).map(([v, label]) => (
            <button key={v} type="button" className={review === v ? 'on' : ''} aria-pressed={review === v} onClick={() => patch({ autoReview: v })}>{label}</button>
          ))}
        </div>
        <p className="muted small">Risky calls: running code, outside actions, changes in the app, delegation and scheduling. All writes also covers web fetches. One short model call per reviewed call.</p>
        {review !== 'off' && (
          <input value={draft.autoReviewModel ?? ''} placeholder="Reviewer model (empty: the extraction model)" spellCheck={false} aria-label="Reviewer model"
            onChange={(e) => patch({ autoReviewModel: e.target.value })} />
        )}
      </div>
      <label className="toggle-row plain">
        <span className="toggle-text"><b>Snapshot granted folders before runs</b>
          <small>{snapOk
            ? 'Before a reply first changes a granted folder or desk workspace, keep a copy so Undo can take back shell effects too.'
            : 'Unavailable on this Mac: folder snapshots need git installed (xcode-select --install).'}</small></span>
        <input type="checkbox" disabled={!snapOk} checked={snapOk && draft.snapshotsEnabled !== false}
          onChange={(e) => patch({ snapshotsEnabled: e.target.checked })} /><span className="switch" />
      </label>
    </>
  )
}

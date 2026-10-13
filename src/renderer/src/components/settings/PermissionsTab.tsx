import { ShieldAlert } from '../../icons'
import { modeOf } from '../../lib/permissionMode'
import { AlwaysAsk, ToolGlobalToggles } from '../ToolPermissions'
import PermissionRules from '../PermissionRules'
import GrantsPanel from '../GrantsPanel'
import { ShellNetwork } from '../CoworkSettings'
import RunSafetySettings, { SnapshotToggle } from '../RunSafetySettings'
import { PermissionModeCards } from '../PermissionMode'
import { DestructiveDefaults } from '../DestructiveDefaults'
import Switch from './Switch'
import { Sec, type TabProps } from './shared'

export default function PermissionsTab({ draft, patch }: TabProps): JSX.Element {
  const mode = modeOf(draft)
  return (
    <section className="permissions-tab">
      <h3>Permissions</h3>
      <Sec id="mode"><PermissionModeCards mode={mode} onPick={(permissionMode) => patch({ permissionMode })} /></Sec>
      {mode === 'manual' && <DestructiveDefaults value={draft.destructiveDefaults} onChange={(destructiveDefaults) => patch({ destructiveDefaults })} />}
      <Switch className="all-connections" danger={!!draft.allowAllConnections} label="Allow all domains and MCP servers"
        title={<>{draft.allowAllConnections && <ShieldAlert size={13} aria-hidden />} Allow all domains and MCP servers</>}
        help="Lifts the host allow-lists for fetching, browsing and shell network, and lets every connector tool run without a per-tool approval. Grain's own data, app, credential stores and the untrusted-content checks stay protected."
        checked={!!draft.allowAllConnections} onChange={(allowAllConnections) => patch({ allowAllConnections })} />
      <Sec id="safety"><RunSafetySettings draft={draft} patch={patch} /></Sec>
      <p className="muted small">Grain can work anywhere on this Mac. Whatever the mode, its own data and the app are off limits. Allow everything asks only before destructive shell commands (permanent deletes, disk wipes, force-pushes); Auto and Manual ask more, including for credential access and email.</p>
      {mode === 'auto' && (
        <details className="modal-free">
          <summary>Reviewer model: {draft.autoReviewModel ? draft.autoReviewModel : 'automatic'}</summary>
          <label><span className="toggle-text"><b>Reviewer model</b><small>Empty means automatic: the fast model, else the helper model, else the chat model.</small></span>
            <input list="model-options" value={draft.autoReviewModel ?? ''} onChange={(e) => patch({ autoReviewModel: e.target.value })} placeholder="Automatic" spellCheck={false} />
          </label>
        </details>
      )}
      <Sec id="tools">
        <h4>Per-tool access</h4>
        <ToolGlobalToggles value={draft.tools ?? {}} onChange={(tools) => patch({ tools })} />
      </Sec>
      <Sec id="always-ask">
        <h4>Always ask first</h4>
        <p className="muted small">Cards that appear every time, in every mode except Allow everything. Sending email asks in every mode. Keep what you cannot take back here.</p>
        <AlwaysAsk value={draft.alwaysAsk ?? []} onChange={(alwaysAsk) => patch({ alwaysAsk })} />
        <PermissionRules value={draft.permissionRules} onChange={(permissionRules) => patch({ permissionRules })} />
        <GrantsPanel draft={draft} patch={patch} />
      </Sec>
      <Sec id="file-edits">
        <div className="setting-row">
          <span className="toggle-text"><b>File edits</b><small>Diff review: each edit lands as a diff and waits for you to accept or reject it. Full agentic editing: edits are written without waiting; the diff is still shown and you can undo it from the file&rsquo;s history. Full agentic editing reduces protection.</small></span>
          <div className="seg" role="group" aria-label="File edits">
            <button type="button" className={(draft.docEditMode ?? 'review') === 'review' ? 'on' : ''} aria-pressed={(draft.docEditMode ?? 'review') === 'review'} onClick={() => patch({ docEditMode: 'review' })}>Diff review</button>
            <button type="button" className={draft.docEditMode === 'apply' ? 'on' : ''} aria-pressed={draft.docEditMode === 'apply'} onClick={() => patch({ docEditMode: 'apply' })}>Full agentic editing</button>
          </div>
        </div>
      </Sec>
      <Sec id="network">
        <SnapshotToggle draft={draft} patch={patch} />
        <h4>Network for commands</h4>
        <ShellNetwork draft={draft} patch={patch} />
      </Sec>
    </section>
  )
}

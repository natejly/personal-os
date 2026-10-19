import { useState } from 'react'
import ModelSelect from '../ModelSelect'
import { ShieldAlert } from '../../icons'
import { modeOf } from '../../lib/permissionMode'
import { AlwaysAsk, ToolGlobalToggles } from '../ToolPermissions'
import PermissionRules from '../PermissionRules'
import GrantsPanel from '../GrantsPanel'
import { ShellNetwork } from '../CoworkSettings'
import RunSafetySettings, { SnapshotToggle } from '../RunSafetySettings'
import { PermissionModeCards } from '../PermissionMode'
import { DestructiveDefaults } from '../DestructiveDefaults'
import NumberField from './NumberField'
import Switch from './Switch'
import PresetsSection from './PresetsSection'
import ConfirmDanger from './ConfirmDanger'
import { Sec, type TabProps } from './shared'

export default function PermissionsTab({ draft, patch, onPresetApplied }: TabProps & { onPresetApplied: () => Promise<void> }): JSX.Element {
  const mode = modeOf(draft)
  // The mode cards bring their own confirmation for Allow everything; these two switches share one inline confirmation.
  const [ask, setAsk] = useState<'connections' | 'edits' | null>(null)
  return (
    <section className="permissions-tab">
      <PresetsSection onApplied={onPresetApplied} />
      <Sec id="mode">
        <h4>Permission mode</h4>
        <p className="set-help">How much the assistant may do before it asks. Cards that always ask still ask in every mode except Allow everything.</p>
        <PermissionModeCards mode={mode} onPick={(permissionMode) => patch({ permissionMode })} />
      </Sec>
      {mode !== 'manual' && (
        <label className="setting-row"><span className="toggle-text"><b>Go ahead with confident plans after</b><small>Seconds. A plan the assistant is sure about starts by itself unless you change or answer it first. Cards that always ask still ask. 0 turns this off.</small></span>
          <NumberField name="planAutoApproveSeconds" value={draft.planAutoApproveSeconds ?? 30} fallback={0} onChange={(planAutoApproveSeconds) => patch({ planAutoApproveSeconds })} />
        </label>
      )}
      {mode === 'manual' && <DestructiveDefaults value={draft.destructiveDefaults} onChange={(destructiveDefaults) => patch({ destructiveDefaults })} />}
      <Switch className="all-connections" danger={!!draft.allowAllConnections} label="Allow all domains and MCP servers"
        title={<>{draft.allowAllConnections && <ShieldAlert size={13} aria-hidden />} Allow all domains and MCP servers</>}
        help="Lifts the host allow-lists for fetching, browsing and shell network, and lets every connector tool run without a per-tool approval. Grain's own data, app, credential stores and the untrusted-content checks stay protected."
        checked={!!draft.allowAllConnections} onChange={(allowAllConnections) => { if (allowAllConnections) setAsk('connections'); else { setAsk(null); patch({ allowAllConnections }) } }} />
      <ConfirmDanger open={ask === 'connections'} title="Allow all domains and MCP servers?"
        consequences={['Fetching, browsing and shell commands can reach any website, not just the ones you listed.', 'Every connector tool runs without asking you first.', 'Grain\'s own data, credential stores and the untrusted-content checks stay protected.']}
        confirmLabel="Allow all" onConfirm={() => { patch({ allowAllConnections: true }); setAsk(null) }} onCancel={() => setAsk(null)} />
      <Sec id="safety">
        <h4>Run safety</h4>
        <RunSafetySettings draft={draft} patch={patch} />
      </Sec>
      <p className="set-help">Grain can work anywhere on this Mac. Whatever the mode, its own data and the app are off limits. Allow everything asks only before destructive shell commands (permanent deletes, disk wipes, force-pushes); Auto and Manual ask more, including for credential access and email.</p>
      {mode === 'auto' && (
        <details className="modal-free">
          <summary>Reviewer model: {draft.autoReviewModel ? draft.autoReviewModel : 'automatic'}</summary>
          <label><span className="toggle-text"><b>Reviewer model</b><small>Empty means automatic: the fast model, else the helper model, else the chat model.</small></span>
            <ModelSelect kind="chat" label="Reviewer model" blank="Automatic" value={draft.autoReviewModel ?? ''} onChange={(autoReviewModel) => patch({ autoReviewModel })} />
          </label>
        </details>
      )}
      <Sec id="tools">
        <h4>Per-tool access</h4>
        <p className="set-help">Turn a tool on, off or to ask first, for every chat.</p>
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
        <h4>File edits</h4>
        <div className="setting-row">
          <span className="toggle-text"><b>How edits land</b><small>Diff review: each edit lands as a diff and waits for you to accept or reject it. Full agentic editing: edits are written without waiting; the diff is still shown and you can undo it from the file&rsquo;s history. Full agentic editing reduces protection.</small></span>
          <div className="seg" role="group" aria-label="File edits">
            <button type="button" className={(draft.docEditMode ?? 'review') === 'review' ? 'on' : ''} aria-pressed={(draft.docEditMode ?? 'review') === 'review'} onClick={() => patch({ docEditMode: 'review' })}>Diff review</button>
            <button type="button" className={draft.docEditMode === 'apply' ? 'on' : ''} aria-pressed={draft.docEditMode === 'apply'} onClick={() => { if (draft.docEditMode !== 'apply') setAsk('edits') }}>Full agentic editing</button>
          </div>
        </div>
        <ConfirmDanger open={ask === 'edits'} title="Write file edits without waiting for you?"
          consequences={['Edits are saved as soon as the assistant makes them; you no longer accept or reject each diff first.', 'The diff is still shown and you can undo an edit from the file history.']}
          confirmLabel="Use full agentic editing" onConfirm={() => { patch({ docEditMode: 'apply' }); setAsk(null) }} onCancel={() => setAsk(null)} />
      </Sec>
      <Sec id="network">
        <h4>Network for commands</h4>
        <p className="set-help">Which hosts shell commands may reach, and whether a snapshot is taken before they run.</p>
        <SnapshotToggle draft={draft} patch={patch} />
        <ShellNetwork draft={draft} patch={patch} />
      </Sec>
    </section>
  )
}

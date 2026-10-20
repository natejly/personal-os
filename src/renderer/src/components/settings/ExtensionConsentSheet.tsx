import { useEffect, useId, useRef } from 'react'
import type { ReactNode } from 'react'
import type { ExtensionConsent } from '@shared/types'
import { BADGE_TEXT, UI_NOT_LOADED_TEXT, badgeKind, blockingFindings, changeLines, contextCostText, shortHash } from '../../lib/extensionsCatalog'
import './extensionsSection.css'

export type SheetKind = 'bundled' | 'install' | 'update'

function Group({ title, warn, children }: { title: string; warn?: boolean; children: ReactNode }): JSX.Element {
  return (
    <section className={`ext-consent-group${warn ? ' warn' : ''}`}>
      <h3>{title}</h3>
      {children}
    </section>
  )
}

/** The facts a user reads before an extension is enabled, installed or updated. Escape or Cancel closes it without changing anything. */
export default function ExtensionConsentSheet({ consent, kind, busy, error, onConfirm, onCancel }: {
  consent: ExtensionConsent; kind: SheetKind; busy: boolean; error: string | null
  onConfirm: () => void; onCancel: () => void
}): JSX.Element {
  const titleId = useId()
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const before = document.activeElement as HTMLElement | null
    ref.current?.focus()
    return () => { before?.focus?.() }
  }, [])
  const card = consent.card
  const c = card.contributes
  const blocked = blockingFindings(consent)
  const bundled = kind === 'bundled'
  const changes = changeLines(consent.changes)
  const badge = badgeKind(card)
  const confirmLabel = bundled ? 'Enable' : kind === 'update' ? 'Update (stays as it is until you enable it)' : 'Install (stays off until you enable it)'
  return (
    <div className="modal-backdrop ext-sheet-backdrop" onClick={onCancel}>
      <div
        ref={ref} tabIndex={-1} role="dialog" aria-modal="true" aria-labelledby={titleId}
        className="modal modal-free ext-sheet"
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => { if (e.key === 'Escape') { e.stopPropagation(); onCancel() } }}
      >
        <header>
          <h2 id={titleId}>{kind === 'update' ? 'Update' : bundled ? 'Enable' : 'Install'} {card.name} <small className="ext-ver">{card.version}</small></h2>
          <span className={`ext-badge ${badge}`}>{BADGE_TEXT[badge]}</span>
        </header>
        <div className="ext-sheet-body">
          {card.description && <p className="ext-desc">{card.description}</p>}
          {!bundled && <p className="ext-note" role="status">Publisher not verified.</p>}
          {consent.typosquat && (
            <p className="ext-note warn" role="alert">Its name is very close to {consent.typosquat.similarTo}. Make sure it is the one you meant.</p>
          )}
          {consent.findings.map((f, i) => (
            <p key={i} className={`ext-note ${f.level === 'error' ? 'error' : f.level === 'warn' ? 'warn' : ''}`} role={f.level === 'error' ? 'alert' : 'status'}>{f.message}</p>
          ))}
          {changes.length > 0 && (
            <Group title="What this update changes" warn>
              <ul>{changes.map((l) => <li key={l}>{l}</li>)}</ul>
            </Group>
          )}
          {card.uiNotLoaded && <p className="ext-note" role="status">{UI_NOT_LOADED_TEXT}.</p>}

          <Group title="What it adds">
            <ul className="ext-list">
              {c.tools.map((t) => <li key={t.name}><code>{t.name}</code> <span className={`ext-danger ${t.danger}`}>{t.danger}</span></li>)}
              {c.views.map((v) => <li key={`v${v}`}>View: {v}</li>)}
              {c.slots.map((s) => <li key={`s${s}`}>Slot: {s}</li>)}
              {c.commands.map((s) => <li key={`c${s}`}>Command: <code>{s}</code></li>)}
              {c.settingsFields.map((s) => <li key={`f${s}`}>Setting: {s}</li>)}
              {!c.tools.length && !c.views.length && !c.slots.length && !c.commands.length && !c.settingsFields.length && <li>No tools, views or settings.</li>}
            </ul>
          </Group>

          {(c.hooks.length > 0 || c.processes.length > 0) && (
            <Group title="Runs on its own" warn>
              <ul className="ext-list">
                {c.hooks.map((h) => <li key={`h${h}`}>Hook: <code>{h}</code> (runs during your chats)</li>)}
                {c.processes.map((p) => <li key={`p${p}`}>Background process: <code>{p}</code></li>)}
              </ul>
            </Group>
          )}

          <Group title="Permissions">
            {card.permissionText.length
              ? <ul className="ext-list">{card.permissionText.map((t) => <li key={t}>{t}</li>)}</ul>
              : <p className="ext-desc">No permissions.</p>}
          </Group>

          <Group title="Context cost"><p className="ext-desc">{contextCostText(card.contextCostTokens)}.</p></Group>

          {(card.requires.extensions.length > 0 || card.requires.services.length > 0) && (
            <Group title="Requires">
              <p className="ext-desc">{[...card.requires.extensions, ...card.requires.services].join(', ')}</p>
            </Group>
          )}

          {consent.commands.length > 0 && (
            <Group title="Commands its instructions mention" warn>
              <pre className="ext-code" tabIndex={0}>{consent.commands.join('\n')}</pre>
            </Group>
          )}

          <Group title="Source">
            <dl className="ext-facts">
              <dt>Origin</dt><dd>{consent.source.kind}{consent.source.url ? `: ${consent.source.url}` : ''}</dd>
              {consent.source.ref && (<><dt>Ref</dt><dd>{consent.source.ref}</dd></>)}
              {consent.source.subdir && (<><dt>Folder</dt><dd>{consent.source.subdir}</dd></>)}
              {consent.source.commit && (<><dt>Commit</dt><dd><code>{consent.source.commit}</code></dd></>)}
              <dt>sha256</dt><dd><code title={consent.sha256}>{shortHash(consent.sha256, 24)}</code></dd>
            </dl>
          </Group>
          {error && <p className="ext-note error" role="alert">{error}</p>}
        </div>
        <footer>
          <button type="button" className="ghost-btn" onClick={onCancel} disabled={busy}>Cancel</button>
          <button type="button" className="primary-btn" onClick={onConfirm} disabled={busy || blocked.length > 0} title={blocked[0]}>
            {busy ? 'Working...' : confirmLabel}
          </button>
        </footer>
      </div>
    </div>
  )
}

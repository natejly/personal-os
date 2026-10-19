import { useState } from 'react'
import type { CatalogCard } from '@shared/types'
import { BADGE_TEXT, UI_NOT_LOADED_TEXT, authorSource, badgeKind, canToggle, contextCostText, contributionChips, requiredByText, shortHash } from '../../lib/extensionsCatalog'
import Switch from './Switch'
import './extensionsSection.css'

export interface CardActions {
  onToggle?: (on: boolean) => void
  onAdd?: () => void
  onCheckUpdate?: () => void
  onRemove?: () => void
}

/** One extension in Settings > Extensions: installed cards carry the on/off switch, available cards an Enable or Install button. */
export default function ExtensionCardView({ card, names, busy, mode, ...act }: {
  card: CatalogCard; names: Record<string, string>; busy: boolean; mode: 'installed' | 'available'
} & CardActions): JSX.Element {
  const [copied, setCopied] = useState(false)
  const badge = badgeKind(card)
  const chips = contributionChips(card)
  const needsOthers = card.requires.extensions.map((id) => names[id] ?? id)
  const requiredBy = requiredByText(card, names)
  const sha = card.pinned?.sha256
  const copy = (): void => {
    if (!sha) return
    void navigator.clipboard?.writeText(sha).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1500) }).catch(() => undefined)
  }
  return (
    <article className="ext-card" aria-label={card.name}>
      <div className="ext-card-top">
        <b className="ext-name">{card.name}</b>
        <span className="ext-ver">{card.version}</span>
        <span className={`ext-badge ${badge}`}>{BADGE_TEXT[badge]}</span>
        <span className="ext-grow" />
        {mode === 'installed' && canToggle(card) && act.onToggle && (
          <Switch plain={false} title={card.enabled ? 'On' : 'Off'} label={`${card.enabled ? 'Disable' : 'Enable'} ${card.name}`} checked={card.enabled} disabled={busy} onChange={act.onToggle} />
        )}
      </div>
      <div className="ext-meta">{authorSource(card)}</div>
      {card.description && <p className="ext-desc">{card.description}</p>}
      {chips.length > 0 && (
        <ul className="ext-chips" aria-label="Contributions">
          {chips.map((c) => <li key={c.label} className={c.warn ? 'warn' : ''}>{c.label}</li>)}
        </ul>
      )}
      {card.permissionText.length > 0 && <p className="ext-meta">{card.permissionText.join('; ')}</p>}
      {card.state === 'invalid' && <p className="ext-note error" role="status">{card.reason || 'Its manifest is not valid, so it cannot be turned on.'}</p>}
      {card.state === 'needs-review' && <p className="ext-note warn" role="status">{card.reason || 'Review it before turning it on.'}</p>}
      {card.state === 'disabled-dependency' && <p className="ext-note warn" role="status">{card.reason || `Needs ${needsOthers.join(', ')} switched on.`}</p>}
      {requiredBy && <p className="ext-meta">{requiredBy}</p>}
      {card.uiNotLoaded && <p className="ext-note" role="status">{UI_NOT_LOADED_TEXT}.</p>}
      {sha && (
        <p className="ext-meta">
          Pinned <code title={sha}>{shortHash(sha)}</code>{' '}
          <button type="button" className="ghost-btn sm" onClick={copy} aria-label={`Copy pinned hash of ${card.name}`}>{copied ? 'Copied' : 'Copy'}</button>
        </p>
      )}
      <div className="ext-card-foot">
        <span className="ext-meta">{contextCostText(card.contextCostTokens)}</span>
        <span className="ext-grow" />
        {mode === 'installed' && card.tier === 'installed' && (
          <>
            {act.onCheckUpdate && (
              <button type="button" className={card.updateAvailable ? 'primary-btn sm' : 'ghost-btn sm'} disabled={busy} onClick={act.onCheckUpdate}>
                {card.updateAvailable ? 'Update available' : 'Check for update'}
              </button>
            )}
            {act.onRemove && <button type="button" className="ghost-btn sm" disabled={busy} onClick={act.onRemove}>Remove</button>}
          </>
        )}
        {mode === 'available' && act.onAdd && (
          <div className="ext-add">
            <button type="button" className="primary-btn sm" disabled={busy || card.state === 'invalid'} onClick={act.onAdd}>
              {card.source === 'bundled' ? 'Enable' : 'Install'}
            </button>
          </div>
        )}
      </div>
    </article>
  )
}

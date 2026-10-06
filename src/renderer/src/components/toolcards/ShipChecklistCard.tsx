import { useEffect, useState } from 'react'
import { ExternalLink, Rocket } from 'lucide-react'
import type { ShipChecklist } from '@shared/types'
import { useStore } from '../../store'
import { api } from '../../lib/api'
import { STEP_TITLE, shipElapsed, shipStepChip } from '../../lib/shipStatus'
import { str } from '../../lib/toolResult'
import CardShell from './CardShell'
import { Badge, ErrorLine, MonoBlock, useParsed } from './blocks'
import { registerToolCard, type ToolCardProps } from './registry'

/** The checklist itself: four steps with status, elapsed time, log tail and links, and Confirm merge / Retry / Cancel. */
export function ShipChecklistView({ checklist: c }: { checklist: ShipChecklist }): JSX.Element {
  const upsertShip = useStore((s) => s.upsertShip)
  const toast = useStore((s) => s.toast)
  const [busy, setBusy] = useState(false)
  const [open, setOpen] = useState<string | null>(null)
  const [, tick] = useState(0)
  const live = c.status === 'running'
  useEffect(() => {
    if (!live) return
    const t = setInterval(() => tick((n) => n + 1), 1000)
    return () => clearInterval(t)
  }, [live])
  const act = async (fn: (id: string) => Promise<ShipChecklist>, what: string): Promise<void> => {
    setBusy(true)
    try {
      upsertShip(await fn(c.id))
    } catch (e) {
      toast(`${what}: ${(e as Error).message}`, 'error')
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="ship-checklist">
      <div className="tc-muted small">
        <span className="mono">{c.branch}</span> into <span className="mono">{c.base}</span> · <span className="mono" title={c.repo_path}>{c.repo_path.split('/').pop()}</span>
      </div>
      <ol className="ship-steps">
        {c.steps.map((s) => {
          const chip = shipStepChip(s.status)
          return (
            <li key={s.name}>
              <div className="tc-statusrow">
                <span className="ship-step-name">{STEP_TITLE[s.name]}</span>
                <Badge tone={chip.tone}>{chip.label}</Badge>
                <span className="tc-muted">{shipElapsed(s)}</span>
                {s.link && <a href={s.link} target="_blank" rel="noreferrer" aria-label={`Open the pull request for ${c.branch}`}><ExternalLink size={11} /> PR</a>}
                {s.log_tail && (
                  <button type="button" className="tc-more" aria-expanded={open === s.name} onClick={() => setOpen((o) => (o === s.name ? null : s.name))}>
                    {open === s.name ? 'Hide log' : 'Log'}
                  </button>
                )}
              </div>
              {open === s.name && <MonoBlock text={s.log_tail} tail />}
            </li>
          )
        })}
      </ol>
      <div className="ship-actions">
        {c.status === 'awaiting_confirm' && (
          <button type="button" className="primary-btn sm" disabled={busy}
            onClick={() => { if (confirm(`Merge ${c.pr_url ?? 'the pull request'} into ${c.base}?`)) void act(api.ship.confirm, 'Merge') }}>
            Confirm merge
          </button>
        )}
        {(c.status === 'failed' || c.status === 'cancelled') && (
          <button type="button" className="ghost-btn sm" disabled={busy} onClick={() => void act(api.ship.retry, 'Retry')}>Retry</button>
        )}
        {(c.status === 'running' || c.status === 'awaiting_confirm') && (
          <button type="button" className="ghost-btn sm" disabled={busy} onClick={() => void act(api.ship.cancel, 'Cancel')}>Cancel</button>
        )}
        {c.merged_sha && <span className="tc-muted small">Merged as <span className="mono">{c.merged_sha.slice(0, 8)}</span></span>}
      </div>
    </div>
  )
}

/** ship_checklist / ship_status in the transcript: the call's arguments, then the live checklist it started. */
export default function ShipChecklistCard(props: ToolCardProps): JSX.Element {
  const { event } = props
  const p = useParsed(event)
  const id = str(p.data?.ship_checklist_id) || (event.name === 'ship_status' ? str(event.arguments.id) : '')
  const live = useStore((s) => (id ? s.shipChecklists[id] : undefined))
  const upsertShip = useStore((s) => s.upsertShip)
  useEffect(() => {
    if (id && !live) api.ship.get(id).then(upsertShip).catch(() => undefined)
  }, [id, live, upsertShip])
  const a = event.arguments
  return (
    <CardShell {...props} icon={<Rocket size={14} />} title="Ship checklist" subject={str(a.branch) || undefined} hideResult={!!live}>
      {event.pending && (
        <div className="tc-muted small">
          Runs the tests, pushes <span className="mono">{str(a.branch)}</span> to origin and opens a pull request into{' '}
          <span className="mono">{str(a.base) || 'main'}</span>. The merge waits for your confirmation.
        </div>
      )}
      {live && <ShipChecklistView checklist={live} />}
      <ErrorLine event={event} />
    </CardShell>
  )
}

registerToolCard('ship_checklist', ShipChecklistCard)
registerToolCard('ship_status', ShipChecklistCard)

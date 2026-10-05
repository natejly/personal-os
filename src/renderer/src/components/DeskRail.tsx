import type { Desk, DeskStatus } from '@shared/types'
import { STATUS_LABEL, deskElapsed, fmtDur, useTick } from '../lib/deskStatus'
// The runtime values come from the relative path, not `@shared/*`: `npm test`'s esbuild line maps no
// alias, so a component that ever becomes reachable from a bundled test would fail to build. store.ts
// imports NEEDS_YOU the same way and says so for the same reason.
import { NEEDS_YOU } from '../../../shared/types'
import { useStore } from '../store'
import { queuePositions } from '../lib/deskFiles'
import { dragProps } from '../canvas/dnd'
import Face from './Face'

interface Section {
  key: string
  label: string
  has: (s: DeskStatus) => boolean
}

/**
 * The triage model, as five buckets a desk belongs to exactly one of, in the order they want
 * attention. `review` sits with the rest of NEEDS_YOU: it is waiting on the user like the others, and
 * the row's own status label says which kind of waiting it is. A draft or a paused desk is not
 * working, so neither is listed as if it were.
 */
const SECTIONS: Section[] = [
  { key: 'needs', label: 'Needs you', has: (s) => NEEDS_YOU.includes(s) },
  { key: 'working', label: 'Working', has: (s) => s === 'planning' || s === 'working' },
  { key: 'queued', label: 'Queued', has: (s) => s === 'queued' },
  { key: 'idle', label: 'Drafts & paused', has: (s) => s === 'draft' || s === 'paused' },
  { key: 'done', label: 'Done', has: (s) => s === 'done' || s === 'failed' || s === 'stopped' }
]

/** The rail top to bottom, which is also what `[` and `]` step through. */
export const railOrder = (desks: Desk[]): Desk[] => SECTIONS.flatMap((sec) => desks.filter((d) => sec.has(d.status)))

/**
 * One row. A component rather than JSX inside `.map` so each row subscribes to its own approval
 * count: the whole rail would otherwise re-render whenever any desk's session changes.
 */
function DeskRow({ desk, active, position, onOpen }: { desk: Desk; active: boolean; position?: number; onOpen: () => void }): JSX.Element {
  const approvals = useStore((s) => s.sessions[desk.conversation_id]?.pendingApprovals ?? 0)
  const title = desk.title || 'Untitled desk'
  const detail = desk.headline || desk.status_reason
  return (
    <div
      className={`desk-row ${active ? 'active' : ''}`}
      role="button"
      tabIndex={0}
      aria-current={active}
      title={desk.brief || title}
      onClick={onOpen}
      onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onOpen() } }}
      {...dragProps({ kind: 'desk', id: desk.id, label: title, projectId: desk.project_id })}
    >
      <span className={`desk-ring desk-ring-${desk.status}`} title={STATUS_LABEL[desk.status]}><Face name={desk.id} status={desk.status} size={20} /></span>
      <span className="desk-row-main">
        <span className="desk-row-title">
          {title}
        </span>
        {/* The status in words on every row: the ring's colour and glyph are not left to carry it. */}
        <span className="desk-row-meta">
          <span className={`desk-row-status desk-ring-${desk.status}`}>{STATUS_LABEL[desk.status]}</span>
          {(position || detail) && <span className="desk-row-head">{position ? `#${position} in line` : detail}</span>}
          <span className="desk-row-age">{fmtDur(deskElapsed(desk))}</span>
        </span>
      </span>
      {approvals > 0 && <span className="desk-badge ask" title={`${approvals} waiting on your approval`}>{approvals}</span>}
      {desk.unseen > 0 && <span className="desk-badge" title={`${desk.unseen} unread update${desk.unseen === 1 ? '' : 's'}`}>{desk.unseen}</span>}
    </div>
  )
}

export default function DeskRail({ desks, activeId, onOpen }: {
  desks: Desk[]
  activeId: string | null
  onOpen: (id: string) => void
}): JSX.Element {
  useTick(desks.some((d) => d.live))
  const positions = queuePositions(desks)
  return (
    <div className="desk-rail">
      {SECTIONS.map((sec) => {
        const rows = desks.filter((d) => sec.has(d.status))
        if (rows.length === 0) return null
        return (
          <section key={sec.key} className={`desk-section ${sec.key}`}>
            <h4>{sec.label}<span className="count">{rows.length}</span></h4>
            {rows.map((d) => <DeskRow key={d.id} desk={d} active={d.id === activeId} position={positions.get(d.id)} onOpen={() => onOpen(d.id)} />)}
          </section>
        )
      })}
    </div>
  )
}

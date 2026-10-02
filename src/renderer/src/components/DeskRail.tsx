import { useEffect, useState } from 'react'
import {
  Ban, CircleCheck, CircleDashed, CircleHelp, CircleSlash, FileCheck2,
  ListChecks, LoaderCircle, Pause, TriangleAlert
} from 'lucide-react'
import type { Desk, DeskStatus } from '@shared/types'
// The runtime values come from the relative path, not `@shared/*`: `npm test`'s esbuild line maps no
// alias, so a component that ever becomes reachable from a bundled test would fail to build. store.ts
// imports NEEDS_YOU the same way and says so for the same reason.
import { NEEDS_YOU } from '../../../shared/types'
import { useStore } from '../store'
import ChatPulse from './ChatPulse'

export const STATUS_LABEL: Record<DeskStatus, string> = {
  draft: 'Draft',
  planning: 'Planning',
  awaiting_plan: 'Plan to approve',
  working: 'Working',
  needs_approval: 'Approval needed',
  blocked: 'Waiting on you',
  paused: 'Paused',
  interrupted: 'Interrupted',
  review: 'Ready to review',
  done: 'Done',
  failed: 'Failed',
  stopped: 'Stopped'
}

const STATUS_ICON: Record<DeskStatus, JSX.Element> = {
  draft: <CircleDashed size={13} />,
  planning: <ListChecks size={13} />,
  awaiting_plan: <ListChecks size={13} />,
  working: <LoaderCircle size={13} className="spin" />,
  needs_approval: <CircleHelp size={13} />,
  blocked: <CircleHelp size={13} />,
  paused: <Pause size={13} />,
  interrupted: <TriangleAlert size={13} />,
  review: <FileCheck2 size={13} />,
  done: <CircleCheck size={13} />,
  failed: <Ban size={13} />,
  stopped: <CircleSlash size={13} />
}

export const fmtDur = (seconds: number): string => {
  const s = Math.max(0, seconds)
  if (s < 60) return `${Math.round(s)}s`
  if (s < 3600) return `${Math.round(s / 60)}m`
  if (s < 86400) return `${(s / 3600).toFixed(1)}h`
  return `${Math.round(s / 86400)}d`
}

/** How long this desk has been going: wall clock since it was created, frozen once it ended. */
export const deskElapsed = (d: Desk): number => (d.ended_at ?? Date.now() / 1000) - d.created_at

/**
 * A slow re-render while anything is live. Elapsed time is the one number on screen that moves on its
 * own, and `desk_status` only arrives when the agent does something — without this a desk that sits
 * on one tool call for ten minutes keeps claiming "2m".
 */
export function useTick(active: boolean, ms = 30_000): void {
  const [, bump] = useState(0)
  useEffect(() => {
    if (!active) return
    const t = setInterval(() => bump((n) => n + 1), ms)
    return () => clearInterval(t)
  }, [active, ms])
}

interface Section {
  key: string
  label: string
  has: (s: DeskStatus) => boolean
}

/**
 * The triage model, as four buckets a desk belongs to exactly one of. `review` is split out of
 * NEEDS_YOU because it is the one "needs you" that is good news, and listing it twice would make the
 * first section's count a lie.
 */
const SECTIONS: Section[] = [
  { key: 'needs', label: 'Needs you', has: (s) => s !== 'review' && NEEDS_YOU.includes(s) },
  { key: 'working', label: 'Working', has: (s) => s === 'draft' || s === 'planning' || s === 'working' || s === 'paused' },
  { key: 'review', label: 'Review', has: (s) => s === 'review' },
  { key: 'done', label: 'Done', has: (s) => s === 'done' || s === 'failed' || s === 'stopped' }
]

/** The rail top to bottom, which is also what `[` and `]` step through. */
export const railOrder = (desks: Desk[]): Desk[] => SECTIONS.flatMap((sec) => desks.filter((d) => sec.has(d.status)))

/**
 * One row. A component rather than JSX inside `.map` so each row subscribes to its own approval
 * count: the whole rail would otherwise re-render whenever any desk's session changes.
 */
function DeskRow({ desk, active, onOpen }: { desk: Desk; active: boolean; onOpen: () => void }): JSX.Element {
  const approvals = useStore((s) => s.sessions[desk.conversation_id]?.pendingApprovals ?? 0)
  const title = desk.title || 'Untitled desk'
  return (
    <div
      className={`desk-row ${active ? 'active' : ''}`}
      role="button"
      tabIndex={0}
      aria-current={active}
      title={desk.brief || title}
      onClick={onOpen}
      onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onOpen() } }}
    >
      <span className={`desk-ring desk-ring-${desk.status}`} title={STATUS_LABEL[desk.status]}>{STATUS_ICON[desk.status]}</span>
      <span className="desk-row-main">
        <span className="desk-row-title">
          {title}
          {desk.live && <ChatPulse conversationId={desk.conversation_id} />}
        </span>
        <span className="desk-row-meta">
          <span className="desk-row-head">{desk.headline || desk.status_reason || STATUS_LABEL[desk.status]}</span>
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
  return (
    <div className="desk-rail">
      {desks.length === 0 && <p className="empty-hint">No desks yet.</p>}
      {SECTIONS.map((sec) => {
        const rows = desks.filter((d) => sec.has(d.status))
        if (rows.length === 0) return null
        return (
          <section key={sec.key} className={`desk-section ${sec.key}`}>
            <h4>{sec.label}<span className="count">{rows.length}</span></h4>
            {rows.map((d) => <DeskRow key={d.id} desk={d} active={d.id === activeId} onOpen={() => onOpen(d.id)} />)}
          </section>
        )
      })}
    </div>
  )
}

import { CircleHelp, ShieldQuestion } from 'lucide-react'
import type { Desk, DeskStatus, FullDesk, ToolEvent } from '@shared/types'
import { useStore } from '../store'
import { inlinePlanShown } from '../lib/statusChrome'
import DeskPlan from './DeskPlan'
import DeskApprovalCard from './DeskApprovalCard'

const ENDED: DeskStatus[] = ['done', 'failed', 'stopped']

/** The desk a chat works in: the open (full) row when it is this one, else the list row. */
export const useChatDesk = (deskId?: string): Desk | FullDesk | null =>
  useStore((s) => (!deskId ? null : s.activeDesk?.id === deskId ? s.activeDesk : s.desks.find((d) => d.id === deskId) ?? null))

/**
 * What the desk is waiting on you for, at the foot of the transcript: a question (answered from the composer), the cards a
 * parked turn let go of, and a plan waiting for approval. Nothing about its own progress: that has no card (lib/statusChrome).
 */
export function DeskInline({ desk, events }: { desk: FullDesk; events: ToolEvent[] }): JSX.Element | null {
  // A card whose call is still pending in the transcript is answered there; these are the ones a parked turn let go of.
  const approvals = (desk.approvals ?? []).filter((a) => !events.some((e) => e.id === a.call_id && e.pending && e.needs_approval))
  const asking = desk.question && !ENDED.includes(desk.status) && !approvals.some((a) => a.tool === 'desk_ask')
  return (
    <div className="desk-inline">
      {asking && (
        <div className="desk-banner ask">
          <CircleHelp size={14} />
          <div><b>It needs an answer</b><p>{desk.question}</p><p className="muted small">Reply below.</p></div>
        </div>
      )}
      {approvals.length > 0 && (
        <div className="desk-banner ask">
          <ShieldQuestion size={14} />
          <div className="desk-cards">
            <b>{approvals.length === 1 ? 'It is waiting on you' : `It is waiting on ${approvals.length} things`}</b>
            {approvals.map((a) => (
              <div key={a.call_id} className="desk-card">
                <DeskApprovalCard approval={a} conversationId={desk.conversation_id} event={events.find((e) => e.id === a.call_id && e.needs_approval)} />
              </div>
            ))}
          </div>
        </div>
      )}
      {inlinePlanShown(desk.plan) && <DeskPlan desk={desk} />}
    </div>
  )
}

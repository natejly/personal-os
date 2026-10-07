import { CircleHelp, PanelRight, Pause, Play, ShieldQuestion, Square } from 'lucide-react'
import type { Desk, DeskStatus, FullDesk, ToolEvent } from '@shared/types'
import { useChatFaceById, useStore, useWorkers } from '../store'
import { STATUS_LABEL } from '../lib/deskStatus'
import { inlinePlanShown, stripShown } from '../lib/statusChrome'
import { liveWorkerCount } from '../lib/workers'
import { queuePositions } from '../lib/deskFiles'
import DeskPlan from './DeskPlan'
import DeskApprovalCard from './DeskApprovalCard'
import Face from './Face'

const ENDED: DeskStatus[] = ['done', 'failed', 'stopped']

/** The desk a chat works in: the open (full) row when it is this one, else the list row. */
export const useChatDesk = (deskId?: string): Desk | FullDesk | null =>
  useStore((s) => (!deskId ? null : s.activeDesk?.id === deskId ? s.activeDesk : s.desks.find((d) => d.id === deskId) ?? null))

/**
 * The slim line above the composer while a chat works autonomously: what it is doing, how far it has got, what is
 * waiting on you, and the run controls. Shown only while a background worker is live (see `stripShown`); the main agent's own progress has no strip. Buttons are gated on `desk.actions`, which the backend reads off the same
 * transition tables its routes enforce, so a button is never offered for a route that 409s.
 */
export default function DeskStrip({ deskId, panelOpen, onPanel }: { deskId: string; panelOpen?: boolean; onPanel?: () => void }): JSX.Element | null {
  const desk = useChatDesk(deskId)
  const approvals = useStore((s) => (desk ? s.sessions[desk.conversation_id]?.pendingApprovals ?? 0 : 0))
  const position = useStore((s) => (desk?.status === 'queued' ? queuePositions(s.desks).get(deskId) : undefined))
  const { startDesk, pauseDesk, resumeDesk, stopDesk } = useStore()
  const liveWorkers = liveWorkerCount(useWorkers(desk?.conversation_id))
  const face = useChatFaceById(desk?.conversation_id)
  if (!desk || !stripShown(desk.status, liveWorkers)) return null
  // Live, the status word says it all; the reason only matters once the run has stopped.
  const detail = position ? `#${position} in line` : ['stopped', 'failed', 'interrupted'].includes(desk.status) ? desk.status_reason : ''
  return (
    <div className={`desk-strip desk-ring-${desk.status}`} role="status" aria-label="Working autonomously">
      <Face {...face} status={desk.status} size={18} title={STATUS_LABEL[desk.status]} />
      <b className="desk-strip-status">{STATUS_LABEL[desk.status]}</b>
      {detail && <span className="desk-strip-detail">{detail}</span>}
      {approvals > 0 && <span className="desk-badge ask" title={`${approvals} waiting on your approval`}>{approvals}</span>}
      {desk.unseen > 0 && <span className="desk-badge" title={`${desk.unseen} need${desk.unseen === 1 ? 's' : ''} you`}>{desk.unseen}</span>}
      <span className="spacer" />
      {desk.actions.includes('start') && <button className="ghost-btn xs" onClick={() => void startDesk(desk.id)}><Play size={11} /> Start</button>}
      {desk.actions.includes('pause') && <button className="ghost-btn xs" onClick={() => void pauseDesk(desk.id)}><Pause size={11} /> Pause</button>}
      {/* Review and a waiting plan have their own answers (Accept / the plan card), so no bare Resume beside them. */}
      {desk.actions.includes('resume') && desk.status !== 'review' && desk.status !== 'awaiting_plan' && (
        <button className="ghost-btn xs" onClick={() => void resumeDesk(desk.id)}><Play size={11} /> Resume</button>
      )}
      {desk.actions.includes('stop') && <button className="ghost-btn xs danger" onClick={() => void stopDesk(desk.id)}><Square size={11} /> Stop</button>}
      {onPanel && (
        <button className={`icon-btn ghost ${panelOpen ? 'on' : ''}`} aria-pressed={!!panelOpen} title="Files, changes and review" aria-label="Files, changes and review" onClick={onPanel}>
          <PanelRight size={14} />
        </button>
      )}
    </div>
  )
}

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

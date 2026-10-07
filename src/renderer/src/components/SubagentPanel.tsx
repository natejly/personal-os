import { useEffect, useState } from 'react'
import { X } from 'lucide-react'
import type { SubagentView } from '@shared/types'
import { api } from '../lib/api'
import { useStore, useWorkerFace, useWorkers } from '../store'
import Face from './Face'
import WorkerChat from './WorkerChat'
import { workerIsLive } from '../lib/workers'

/**
 * One subagent, opened from its run card or its face in the crew ring: the transcript as it grows, and a box to
 * talk to it. A running child takes the message before its next turn; a finished one is continued through the chat
 * that started it (the reply can agent_spawn it with resume_id), so the message goes there instead.
 */
export default function SubagentPanel({ id }: { id: string }): JSX.Element {
  const { openSubagent, toast } = useStore()
  const [view, setView] = useState<SubagentView | null>(null)
  useEffect(() => { api.subagents.get(id).then(setView).catch(() => undefined) }, [id])
  const role = String(view?.run.input?.role ?? view?.agent?.role ?? 'agent')
  const parentConv = view?.run.input?.conversation_id as string | undefined
  // A worker's face follows its resume chain and its Library agent; a plain subagent wears its own id.
  const worker = useWorkers(parentConv).find((x) => x.id === id)
  const face = useWorkerFace({ id, agent: worker?.agent ?? role, origin: worker?.origin })
  const stop = worker && workerIsLive(worker)
    ? () => api.workers.stop(id).catch((e: Error) => toast(e.message, 'error'))
    : undefined

  return (
    <div className="modal-backdrop" onClick={() => openSubagent(null)}>
      <div className="modal subagent-panel modal-free" role="dialog" aria-label={`Subagent ${role}`} onClick={(e) => e.stopPropagation()}>
        <header>
          <h2><Face {...face} status={view?.agent?.state ?? view?.run.status} size={22} /> {worker?.title || role}</h2>
          <button className="icon-btn ghost" aria-label="Close" onClick={() => openSubagent(null)}><X size={14} /></button>
        </header>
        <WorkerChat id={id} onStop={stop} />
      </div>
    </div>
  )
}

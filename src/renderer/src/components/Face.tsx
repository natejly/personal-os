import { Blobatar } from 'blobatar/react'
import { happy, idle, sad, scared, sleepy, surprised, thinking, unsure, type Expression } from 'blobatar/expression'
import 'blobatar/motion.css'

/** Status words used across replies, desks, subagents and job runs, read as a pose. Unknown words stay idle. */
const MOOD: Record<string, Expression> = {
  streaming: thinking, running: thinking, working: thinking, planning: thinking,
  awaiting_plan: unsure, blocked: unsure,
  needs_approval: surprised, awaiting_approval: surprised, 'needs-approval': surprised,
  paused: sleepy, queued: sleepy, stopped: sleepy,
  interrupted: scared, timed_out: scared,
  error: sad, failed: sad,
  review: happy, done: happy
}

const LIVE = new Set(['streaming', 'running', 'working', 'planning'])

/**
 * The same name always draws the same creature, so an agent is recognisable wherever it shows up.
 * Live ones move all the time; the rest only move on hover, which keeps a long transcript quiet.
 */
export default function Face({ name, status, size = 26, title }: { name: string; status?: string; size?: number; title?: string }): JSX.Element {
  return (
    <Blobatar
      className="face"
      name={name}
      size={size}
      title={title}
      expression={(status && MOOD[status]) || idle}
      animate={status && LIVE.has(status) ? 'always' : 'hover'}
    />
  )
}

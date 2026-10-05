import { Blobatar } from 'blobatar/react'
import { happy, idle, sad, scared, sleepy, surprised, thinking, unsure, type Expression } from 'blobatar/expression'
import 'blobatar/motion.css'

/** Status words used across replies, desks, subagents and job runs, read as a pose. Unknown words stay idle. */
const MOOD: Record<string, Expression> = {
  streaming: thinking, running: thinking, working: thinking, planning: thinking,
  awaiting_plan: unsure, blocked: unsure,
  needs_approval: surprised, awaiting_approval: surprised,
  paused: sleepy, queued: sleepy, stopped: sleepy,
  interrupted: scared, timed_out: scared, cancelled: sleepy,
  error: sad, failed: sad,
  review: happy, done: happy, completed: happy, partial: unsure,
  waiting_approval: surprised
}

const LIVE = new Set(['streaming', 'running', 'working', 'planning'])

/** Spaces all wear this one color; the space id then picks only the silhouette. */
export const SPACE_HUE = { hue: 150, tone: 0.45 }

/**
 * The same name always draws the same creature, so an agent is recognisable wherever it shows up.
 * Live ones move all the time; the rest only move on hover, which keeps a long transcript quiet.
 * `hue`/`tone` lock the color so a family of faces differs by shape alone.
 */
export default function Face({ name, status, size = 26, title, hue, tone }: { name: string; status?: string; size?: number | 'fill'; title?: string; hue?: number; tone?: number }): JSX.Element {
  return (
    <Blobatar
      className="face"
      name={name}
      size={size === 'fill' ? undefined : size}
      title={title}
      hue={hue}
      tone={tone}
      expression={(status && MOOD[status]) || idle}
      animate={status && LIVE.has(status) ? 'always' : 'hover'}
    />
  )
}

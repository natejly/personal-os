import type { AgentDef, BuiltinAgent } from '@shared/types'

/** The roles every worker runs as when it is not a Library agent; they share the plain id-seeded face. */
const ROLES = new Set(['', 'general', 'worker', 'researcher', 'reviewer'])

export interface FaceLook { name: string; hue?: number; tone?: number }

/** The Library agent a worker or subagent runs as, else undefined (a bare role). */
export const libraryAgent = (agent?: string | null): string | undefined => (agent && !ROLES.has(agent) ? agent : undefined)

/** A Library agent's face colour (custom first, like the Library lists them), else undefined. */
export const agentHue = (defs: { builtin: BuiltinAgent[]; custom: AgentDef[] }, name?: string): number | undefined =>
  name ? (defs.custom.find((d) => d.name === name) ?? defs.builtin.find((d) => d.name === name))?.hue ?? undefined : undefined

/**
 * The one rule for who wears which creature. An agent keeps its own face everywhere (its name, its Library hue);
 * a project's colour only tints it when the agent has no hue. Anything else wears `id` (a chat's id, or the first
 * worker of a resume chain), tinted by its project when it has one.
 */
export function faceSeed({ id, agent, hue, project }: { id: string; agent?: string; hue?: number | null; project?: { hue: number; tone: number } | null }): FaceLook {
  const name = agent || id
  if (agent && hue != null) return { name, hue }
  return project ? { name, hue: project.hue, tone: project.tone } : { name }
}

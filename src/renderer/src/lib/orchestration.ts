import type { ToolEvent } from '@shared/types'
import { staysVisible } from './toolDisplay'

/** The hand-off tools (mirror of backend workers.FRONT_TOOLS): the app shows the workers themselves, so their calls are not transcript cards. */
const ORCHESTRATION = new Set(['delegate', 'message_worker', 'check_worker', 'stop_worker', 'resume_worker'])

export const isOrchestration = (name: string): boolean => ORCHESTRATION.has(name)

/** A reply's events without the hand-offs, except one the user has to act on or that was refused. */
export const quietEvents = (events: ToolEvent[] | null | undefined): ToolEvent[] => (events ?? []).filter((t) => !isOrchestration(t.name) || staysVisible(t))

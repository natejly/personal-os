import type { ProjectTab } from '../store'

/** The project view's tabs, in order. Context is the project's files: its chats' outputs, notes and uploads. */
export const PROJECT_TABS: ProjectTab[] = ['chats', 'context', 'instructions', 'memory']
export const PROJECT_TAB_LABEL: Record<ProjectTab, string> = { chats: 'Chats', context: 'Context', instructions: 'Instructions', memory: 'Memory' }

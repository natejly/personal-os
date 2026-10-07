export type ProjectSection = 'chats' | 'files' | 'context' | 'instructions' | 'memory'

/** The project view's sections, in page order: Chats and Files side by side, then Context, Instructions and Memory. */
export const PROJECT_SECTIONS: ProjectSection[] = ['chats', 'files', 'context', 'instructions', 'memory']
export const PROJECT_SECTION_LABEL: Record<ProjectSection, string> = { chats: 'Chats', files: 'Files', context: 'Context', instructions: 'Instructions', memory: 'Memory' }

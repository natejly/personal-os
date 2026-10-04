/** The tools the Settings rule tester can try, each with the one argument its rules are matched on. */
export const TESTERS = [
  { tool: 'shell_run', label: 'Command', key: 'command', placeholder: 'git status && rm -rf build' },
  { tool: 'read_local_file', label: 'Read a file', key: 'path', placeholder: '~/Documents/notes.txt' },
  { tool: 'write_local_file', label: 'Write a file', key: 'path', placeholder: '~/Projects/app/src/x.ts' },
  { tool: 'gmail_send', label: 'Send mail', key: 'to', placeholder: 'a@example.com, b@example.com' },
  { tool: 'gmail_draft', label: 'Draft mail', key: 'to', placeholder: 'a@example.com' },
  { tool: 'calendar_create', label: 'Create an event', key: 'calendar_id', placeholder: 'primary or work@example.com' }
] as const

export type TesterTool = (typeof TESTERS)[number]['tool']

/** The /permissions/evaluate body for a tester entry and what the user typed. */
export function testBody(tool: TesterTool, value: string): { tool: string; args: Record<string, string> } {
  const t = TESTERS.find((x) => x.tool === tool) ?? TESTERS[0]
  return { tool: t.tool, args: { [t.key]: value.trim() } }
}

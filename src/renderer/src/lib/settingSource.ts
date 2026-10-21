/** Words for where a setting's value came from, shown beside a settings row. */

const SCOPES: Record<string, string> = { global: 'Global', project: 'Project', chat: 'Chat' }

/** The chip text for a scope; an unknown scope reads as itself. */
export const scopeLabel = (scope: string): string => SCOPES[scope] ?? scope

/** A source id as a phrase that follows "Set by", e.g. "your settings", "the acme extension". */
export function sourceText(source: string): string {
  if (source === 'default') return 'the built-in default'
  if (source === 'user') return 'your settings'
  if (source === 'project') return 'this project'
  if (source === 'chat') return 'this chat'
  if (source.startsWith('ext:')) return `the ${source.slice('ext:'.length)} extension`
  return source
}

/** A value as short text for a popover; long values are cut. */
export function valueText(v: unknown): string {
  const s = typeof v === 'string' ? v : JSON.stringify(v) ?? String(v)
  return s.length > 60 ? `${s.slice(0, 60)}...` : s
}

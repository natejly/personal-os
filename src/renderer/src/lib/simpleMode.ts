/**
 * Simple mode: what the setting hides. Purely presentational: the agent's prompt, tools and permissions are
 * untouched and hidden settings keep their values. A leaf (no store import) so the rules live in one place.
 */
import type { Settings } from '@shared/types'

export const simpleOn = (s: Settings): boolean => s.simpleMode === true
/** Developer tools never show in simple mode, whatever the saved flag says. */
export const devToolsOn = (s: Settings): boolean => s.devTools === true && !simpleOn(s)

/** Settings sections that disappear whole. */
export const SIMPLE_HIDDEN_SECTIONS: ReadonlySet<string> = new Set(['models', 'advanced'])
/** Blocks (`sec-<id>`) hidden inside the sections that stay: Assistant keeps only memories, Permissions only mode. */
export const SIMPLE_HIDDEN_BLOCKS: ReadonlySet<string> = new Set([
  'loop', 'desks', 'workers', 'coding', 'browser', 'checks', 'compaction', 'inspector', 'learning', 'search', 'map',
  'safety', 'file-edits', 'network', 'tools', 'always-ask', 'system'
])
/** True when simple mode hides this section, or this block of it. */
export const simpleHides = (section: string, sec?: string): boolean =>
  SIMPLE_HIDDEN_SECTIONS.has(section) || (!!sec && SIMPLE_HIDDEN_BLOCKS.has(sec))

/** Slash commands that act on the context window. */
export const SIMPLE_HIDDEN_COMMANDS: ReadonlySet<string> = new Set(['compact'])

/** What a failed reply says instead of the raw error. */
export const SIMPLE_ERROR = 'Something went wrong. Try again, or turn off Simple mode in Settings for details.'

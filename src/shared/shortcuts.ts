/**
 * Every keyboard shortcut Grain has, in one table. The app menu (src/main/index.ts) takes its
 * accelerators and actions from here, and the shortcut overlay (HelpOverlay) lists this same table,
 * so neither can drift from the other. Plain data: imported by main and renderer alike.
 *
 * scope:
 * - menu:      an app-menu accelerator; `action` goes to the main window (sendMenu).
 * - window:    an app-menu accelerator; `action` goes to whichever window has focus (sendWindowMenu).
 * - component: handled by a component itself (editor, composer, canvas). Listed for display only.
 * - global:    an OS-wide shortcut the user picks in Settings; `keys` is the default, the overlay shows the live one.
 */
export type ShortcutScope = 'menu' | 'window' | 'component' | 'global'

export interface Shortcut {
  id: string
  label: string
  /** An Electron accelerator string; a component entry may use a plain key name ('?', 'Enter', 'Drag'). */
  keys: string
  group: string
  action?: string
  scope: ShortcutScope
}

const SPACES: Shortcut[] = Array.from({ length: 9 }, (_, i) => ({
  id: `space-${i + 1}`, label: `Canvas ${i + 1}`, keys: `Control+${i + 1}`, group: 'Canvases', action: `canvas:space:${i + 1}`, scope: 'menu' as const
}))

export const SHORTCUTS: readonly Shortcut[] = [
  // General
  { id: 'settings', label: 'Settings…', keys: 'CmdOrCtrl+,', group: 'General', action: 'settings', scope: 'menu' },
  { id: 'palette', label: 'Command Palette…', keys: 'CmdOrCtrl+K', group: 'General', action: 'palette', scope: 'menu' },
  { id: 'help', label: 'Keyboard Shortcuts…', keys: 'CmdOrCtrl+/', group: 'General', action: 'help', scope: 'menu' },
  { id: 'help-key', label: 'Keyboard Shortcuts (outside a text field)', keys: '?', group: 'General', scope: 'component' },
  { id: 'toggle-sidebar', label: 'Toggle Sidebar', keys: 'CmdOrCtrl+B', group: 'General', action: 'toggle-sidebar', scope: 'menu' },
  { id: 'page-agent', label: 'Context panel / Quick chat', keys: 'CmdOrCtrl+I', group: 'General', action: 'page-agent', scope: 'menu' },
  { id: 'toggle-desk', label: 'Toggle Side Panel', keys: '', group: 'General', action: 'toggle-desk', scope: 'menu' },
  { id: 'panel-context', label: 'Side Panel: Context and Terminal', keys: '', group: 'General', action: 'panel-context', scope: 'menu' },
  { id: 'toggle-context', label: 'Toggle Context Panel', keys: 'Control+Command+I', group: 'General', action: 'toggle-context', scope: 'menu' },
  { id: 'zoom-reset', label: 'Actual Size', keys: 'CmdOrCtrl+Alt+0', group: 'General', action: 'zoom:reset', scope: 'menu' },
  { id: 'zoom-in', label: 'Zoom In', keys: 'CmdOrCtrl+=', group: 'General', action: 'zoom:in', scope: 'menu' },
  { id: 'zoom-out', label: 'Zoom Out', keys: 'CmdOrCtrl+-', group: 'General', action: 'zoom:out', scope: 'menu' },

  // Create
  { id: 'new-chat', label: 'New Chat', keys: 'CmdOrCtrl+N', group: 'Create', action: 'new-chat', scope: 'menu' },
  { id: 'new-note', label: 'New File', keys: 'CmdOrCtrl+Shift+N', group: 'Create', action: 'new-note', scope: 'menu' },
  { id: 'daily-note', label: "Today's File", keys: 'CmdOrCtrl+Shift+D', group: 'Create', action: 'daily-note', scope: 'menu' },
  { id: 'upload', label: 'Upload File…', keys: 'CmdOrCtrl+U', group: 'Create', action: 'upload', scope: 'menu' },

  // Go to
  { id: 'view-chat', label: 'Chats', keys: 'CmdOrCtrl+1', group: 'Go to', action: 'view:chat', scope: 'menu' },
  { id: 'view-todos', label: 'Lists', keys: 'CmdOrCtrl+2', group: 'Go to', action: 'view:todos', scope: 'menu' },
  { id: 'view-calendar', label: 'Calendar', keys: 'CmdOrCtrl+3', group: 'Go to', action: 'view:calendar', scope: 'menu' },
  { id: 'view-docs', label: 'Files', keys: 'CmdOrCtrl+4', group: 'Go to', action: 'view:docs', scope: 'menu' },
  { id: 'view-mail', label: 'Mail', keys: 'CmdOrCtrl+5', group: 'Go to', action: 'view:mail', scope: 'menu' },
  { id: 'view-memory', label: 'Memory…', keys: 'CmdOrCtrl+6', group: 'Go to', action: 'view:memory', scope: 'menu' },

  // Chat
  { id: 'chat-prev', label: 'Previous Chat', keys: 'CmdOrCtrl+Shift+[', group: 'Chat', action: 'chat:prev', scope: 'menu' },
  { id: 'chat-next', label: 'Next Chat', keys: 'CmdOrCtrl+Shift+]', group: 'Chat', action: 'chat:next', scope: 'menu' },
  { id: 'chat-search', label: 'Search Chats', keys: 'CmdOrCtrl+Shift+F', group: 'Chat', action: 'chat:search', scope: 'menu' },
  { id: 'find', label: 'Find…', keys: 'CmdOrCtrl+F', group: 'Chat', action: 'chat:find', scope: 'window' },
  { id: 'find-next', label: 'Find Next', keys: 'CmdOrCtrl+G', group: 'Chat', action: 'chat:find-next', scope: 'window' },
  { id: 'find-prev', label: 'Find Previous', keys: 'Shift+CmdOrCtrl+G', group: 'Chat', action: 'chat:find-prev', scope: 'window' },
  { id: 'send', label: 'Send (queue while a reply runs)', keys: 'Enter', group: 'Chat', scope: 'component' },
  { id: 'steer', label: 'Steer the running reply', keys: 'CmdOrCtrl+Enter', group: 'Chat', scope: 'component' },
  { id: 'newline', label: 'New line', keys: 'Shift+Enter', group: 'Chat', scope: 'component' },
  { id: 'stop', label: 'Stop the reply', keys: 'Escape', group: 'Chat', scope: 'component' },
  // The composer binds ⇧⌘P itself; the menu shows it without registering it.
  // The chord is the user's (Settings → Advanced → Voice and shortcuts); the overlay shows the live one.
  { id: 'dictation', label: 'Dictate (hold) or latch (tap)', keys: 'Control+Alt+D', group: 'Chat', scope: 'component' },

  // Files (the note editor)
  { id: 'md-toggle-edit', label: 'Edit or read', keys: 'CmdOrCtrl+E', group: 'Files', scope: 'component' },
  { id: 'md-save', label: 'Save', keys: 'CmdOrCtrl+S', group: 'Files', scope: 'component' },
  { id: 'md-bold', label: 'Bold', keys: 'CmdOrCtrl+Shift+B', group: 'Files', scope: 'component' },
  { id: 'md-italic', label: 'Italic', keys: 'CmdOrCtrl+Shift+I', group: 'Files', scope: 'component' },
  { id: 'md-link', label: 'Link', keys: 'CmdOrCtrl+K', group: 'Files', scope: 'component' },
  { id: 'md-math', label: 'Maths', keys: 'Control+Command+M', group: 'Files', scope: 'component' },
  { id: 'md-code', label: 'Code', keys: 'CmdOrCtrl+Shift+E', group: 'Files', scope: 'component' },
  { id: 'md-indent', label: 'Indent', keys: 'Tab', group: 'Files', scope: 'component' },

  // Spaces
  { id: 'canvas-toggle', label: 'Toggle Canvases', keys: 'CmdOrCtrl+Shift+C', group: 'Canvases', action: 'canvas:toggle', scope: 'menu' },
  { id: 'canvas-new', label: 'New Canvas', keys: 'Control+Command+N', group: 'Canvases', action: 'canvas:new-space', scope: 'menu' },
  // ⌥⌘arrows, not ⌃arrows: macOS owns ⌃←/⌃→/⌃↑ and an app accelerator loses to a system one.
  { id: 'canvas-prev', label: 'Previous Canvas', keys: 'Alt+Command+Left', group: 'Canvases', action: 'canvas:prev-space', scope: 'menu' },
  { id: 'canvas-next', label: 'Next Canvas', keys: 'Alt+Command+Right', group: 'Canvases', action: 'canvas:next-space', scope: 'menu' },
  { id: 'canvas-overview', label: 'Overview', keys: 'Alt+Command+Up', group: 'Canvases', action: 'canvas:overview', scope: 'menu' },
  ...SPACES,
  { id: 'canvas-tidy', label: 'Tidy Up', keys: 'Control+Command+T', group: 'Canvases', action: 'canvas:tidy', scope: 'menu' },
  { id: 'canvas-lock', label: 'Lock / Unlock Canvas', keys: 'Control+Command+L', group: 'Canvases', action: 'canvas:lock', scope: 'menu' },
  { id: 'canvas-deselect', label: 'Deselect, leave the overview', keys: 'Escape', group: 'Canvases', scope: 'component' },
  { id: 'canvas-delete', label: 'Close selected windows', keys: 'Backspace', group: 'Canvases', scope: 'component' },
  { id: 'canvas-zoom', label: 'Zoom the plane', keys: 'Command+Scroll', group: 'Canvases', scope: 'component' },
  { id: 'canvas-drag', label: 'Move a window from anywhere in it', keys: 'Command+Drag', group: 'Canvases', scope: 'component' },

  // Windows
  { id: 'close-window', label: 'Close Window', keys: 'CmdOrCtrl+W', group: 'Windows', action: 'close-window', scope: 'window' },
  { id: 'minimize-window', label: 'Minimize', keys: 'CmdOrCtrl+M', group: 'Windows', action: 'minimize-window', scope: 'window' },
  { id: 'popout', label: 'Pop Out', keys: 'Control+Command+O', group: 'Windows', action: 'canvas:popout', scope: 'window' },
  { id: 'unpopout', label: 'Return to Canvas', keys: 'Control+Command+Shift+O', group: 'Windows', action: 'canvas:unpopout', scope: 'window' },
  { id: 'pin', label: 'Pin on Top', keys: 'Control+Command+P', group: 'Windows', action: 'canvas:pin', scope: 'window' },
  { id: 'opacity-down', label: 'More Transparent', keys: 'Control+Command+[', group: 'Windows', action: 'canvas:opacity:down', scope: 'window' },
  { id: 'opacity-up', label: 'Less Transparent', keys: 'Control+Command+]', group: 'Windows', action: 'canvas:opacity:up', scope: 'window' },
  // Main runs these two itself (popouts.ts), so their actions never reach a renderer.
  { id: 'gather-widgets', label: 'Gather Widgets', keys: 'Alt+Command+G', group: 'Windows', action: 'gather', scope: 'menu' },
  { id: 'popouts-front', label: 'Bring Pop-outs to Front', keys: 'Alt+Command+F', group: 'Windows', action: 'popouts-front', scope: 'menu' },

  // Anywhere on your Mac (defaults; Settings → Behavior → Shortcuts → Advanced changes them)
  { id: 'global-gather', label: 'Gather widgets', keys: 'Control+Alt+Command+Space', group: 'Anywhere on your Mac', scope: 'global' },
  { id: 'global-capture', label: 'Quick capture', keys: 'CommandOrControl+Shift+Space', group: 'Anywhere on your Mac', scope: 'global' },
  { id: 'global-ask', label: 'Quick ask', keys: 'Alt+Space', group: 'Anywhere on your Mac', scope: 'global' }
]

const BY_ID = new Map(SHORTCUTS.map((s) => [s.id, s]))

/** The entry for `id`; a typo is a startup error, not a silent missing shortcut. */
export function shortcut(id: string): Shortcut {
  const s = BY_ID.get(id)
  if (!s) throw new Error(`unknown shortcut "${id}"`)
  return s
}

const MODS: Record<string, string> = {
  control: '⌃', ctrl: '⌃', alt: '⌥', option: '⌥', shift: '⇧',
  cmdorctrl: '⌘', commandorcontrol: '⌘', command: '⌘', cmd: '⌘', super: '⌘', meta: '⌘'
}
const ORDER = ['⌃', '⌥', '⇧', '⌘']
const KEYS: Record<string, string> = {
  left: '←', right: '→', up: '↑', down: '↓', enter: '↩', return: '↩', escape: 'Esc', esc: 'Esc',
  backspace: '⌫', delete: '⌦', tab: '⇥', space: 'Space', plus: '+'
}

/** 'CmdOrCtrl+Shift+P' → '⇧⌘P', modifiers in the macOS order ⌃⌥⇧⌘. Unknown keys pass through. */
export function formatAccelerator(accel: string): string {
  if (!accel) return ''
  // A trailing '+' is the key itself ('CmdOrCtrl++'), not a separator.
  const parts = accel.endsWith('++') ? [...accel.slice(0, -2).split('+'), '+'] : accel.split('+')
  const mods = new Set<string>()
  const keys: string[] = []
  for (const p of parts) {
    const m = MODS[p.toLowerCase()]
    if (m) mods.add(m)
    else keys.push(KEYS[p.toLowerCase()] ?? (p.length === 1 ? p.toUpperCase() : p))
  }
  return ORDER.filter((m) => mods.has(m)).join('') + keys.join('')
}

const KEY_NAMES: Record<string, string> = {
  return: 'enter', esc: 'escape', plus: '+', numadd: '+', minus: '-', arrowup: 'up', arrowdown: 'down', arrowleft: 'left', arrowright: 'right'  // a key event's names → Electron's
}
/** What a shifted key press reports (US layout) → the key it is spelled as in a binding ('Shift+1' arrives as '!'). */
const UNSHIFTED = Object.fromEntries([...'!@#$%^&*()'].map((c, i) => [c, String((i + 1) % 10)]).concat(
  [['~', '`'], ['_', '-'], ['{', '['], ['}', ']'], ['|', '\\'], [':', ';'], ['"', "'"], ['<', ','], ['>', '.'], ['?', '/'], ['+', '=']]))

/** A modifier spelling → its canonical name. 'mod' is the primary modifier: ⌘ on macOS, Ctrl elsewhere, so there Control and CmdOrCtrl are one key. */
function modifierName(lower: string, mac: boolean): string | undefined {
  switch (lower) {
    case 'cmdorctrl': case 'commandorcontrol': return 'mod'
    case 'control': case 'ctrl': return mac ? 'ctrl' : 'mod'
    case 'command': case 'cmd': case 'super': case 'meta': return mac ? 'mod' : 'meta'
    case 'alt': case 'option': return 'alt'
    case 'shift': return 'shift'
    default: return undefined
  }
}

/**
 * One spelling for an accelerator on a platform, so two spellings of the same chord compare equal: modifiers sorted and named
 * ('mod' is the primary modifier), the key lower-cased. 'Shift+CmdOrCtrl+g' and 'CommandOrControl+Shift+G' both give 'mod+shift+g'.
 */
export function canonicalAccelerator(accel: string, mac: boolean): string {
  const parts = accel.endsWith('++') ? [...accel.slice(0, -2).split('+'), '+'] : accel.split('+')
  const mods = new Set<string>()
  let key = ''
  for (const p of parts) {
    const lower = p.toLowerCase()
    const mod = modifierName(lower, mac)
    if (mod) mods.add(mod)
    else key = KEY_NAMES[lower] ?? lower
  }
  if (key === '+' && mods.has('shift')) key = '='  // Shift+= types '+' on a US layout, and acceleratorOf names that press '='
  return [...[...mods].sort(), key].join('+')
}

/** Are these two accelerators the same chord on macOS or on any other platform? Checked on both, because a binding is registered before the platform matters. */
export function sameChord(a: string, b: string): boolean {
  return [true, false].some((mac) => canonicalAccelerator(a, mac) === canonicalAccelerator(b, mac))
}

/** The core shortcut that already uses this chord, if any. */
export function shortcutForKeys(accel: string): Shortcut | undefined {
  return SHORTCUTS.find((s) => s.keys && sameChord(s.keys, accel))
}

/** The canonical accelerator of a key press, to compare with `canonicalAccelerator` of a binding. */
export function acceleratorOf(e: { key: string; code?: string; metaKey: boolean; ctrlKey: boolean; altKey: boolean; shiftKey: boolean }, mac: boolean): string {
  const key = mac && e.altKey && e.code?.match(/^(?:Key|Digit)(.)$/)?.[1].toLowerCase()  // macOS Option+letter types another character ('ø'): the physical key names it. Elsewhere e.key is the layout's letter
    || (e.shiftKey && UNSHIFTED[e.key]) || e.key
  const mods = [e.shiftKey && 'shift', e.altKey && 'alt', mac ? e.metaKey && 'mod' : e.metaKey && 'meta', mac ? e.ctrlKey && 'ctrl' : e.ctrlKey && 'mod']
  return [...mods.filter(Boolean).sort(), key === ' ' ? 'space' : KEY_NAMES[key.toLowerCase()] ?? key.toLowerCase()].join('+')
}

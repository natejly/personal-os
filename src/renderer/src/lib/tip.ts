import { formatAccelerator, shortcut } from '@shared/shortcuts'

/** Hover text for a button whose action has a shortcut: `tip('Close', 'close-window')` gives "Close (⌘W)". Throws on an unknown id. */
export const tip = (label: string, id: string): string => { const k = formatAccelerator(shortcut(id).keys); return k ? `${label} (${k})` : label }

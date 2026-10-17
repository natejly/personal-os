import { Calendar, Mail, Library, Scheduled } from '../icons'
import { CORE, registerSlot, slotItems, type SlotEntry } from '../extensions/host'
import '../extensions'

/**
 * The shell's own sidebar views. Files is fixed and comes before; Calendar and Mail take 14 and 16,
 * Library 40, and an extension's view slots in by its `nav.order` (Lists 12, Health 18).
 */
registerSlot(CORE, 'sidebar.row', { id: 'library', view: 'library', description: 'Skills, agents and connectors', label: 'Library', icon: <Library size={15} />, order: 40 })
registerSlot(CORE, 'sidebar.row', { id: 'jobs', view: 'jobs', description: 'Scheduled jobs and their runs', label: 'Jobs', icon: <Scheduled size={15} />, order: 41 })
registerSlot(CORE, 'sidebar.row', { id: 'calendar', view: 'calendar', description: 'Google Calendar', label: 'Calendar', icon: <Calendar size={15} />, kind: 'calendar', order: 14 })
registerSlot(CORE, 'sidebar.row', { id: 'mail', view: 'mail', description: 'Gmail inbox', label: 'Mail', icon: <Mail size={15} />, order: 16 })

/** The sidebar's view rows, in order. */
export const navEntries = (): SlotEntry<'sidebar.row'>[] => slotItems('sidebar.row')

/** Hover text for a nav entry: its label, then what it is for. */
export const navTitle = (e: { label: string; description?: string }): string => (e.description ? `${e.label} — ${e.description}` : e.label)

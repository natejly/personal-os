/** Rendering a fixed slot: the items registered for it, in order, those whose extension is on and whose `when` holds. */
import { useStore, type State } from '../store'
import { ownerOn, slotItems, type ComposerToolbarProps, type MessageSlotProps, type SlotEntry, type SlotName } from './host'

const shown = (s: State, item: { extension: string; when?: (s: State) => boolean }): boolean =>
  ownerOn(s.settings, item.extension) && (!item.when || item.when(s))

/** The items to show now. The store is read through a string of ids, so a component re-renders only when that list changes. */
export function useSlot<K extends SlotName>(slot: K): SlotEntry<K>[] {
  const items = slotItems(slot)
  const ids = useStore((s) => items.filter((i) => shown(s, i)).map((i) => i.id).join('\n'))
  const on = new Set(ids.split('\n'))
  return items.filter((i) => on.has(i.id))
}

type ComponentSlot = 'sidebar.section' | 'composer.toolbar' | 'message.header' | 'message.footer' | 'titlebar.left' | 'titlebar.right' | 'statusbar.item' | 'project.files'
interface SlotProps {
  'sidebar.section': Record<string, never>
  'composer.toolbar': ComposerToolbarProps
  'message.header': MessageSlotProps
  'message.footer': MessageSlotProps
  'titlebar.left': Record<string, never>
  'titlebar.right': Record<string, never>
  'statusbar.item': Record<string, never>
  'project.files': { projectId: string }
}

/** Every item of a component slot, each given `props`. */
export function Slot<K extends ComponentSlot>({ name, props }: { name: K; props: SlotProps[K] }): JSX.Element {
  const items = useSlot(name)
  return <>{items.map((i) => { const C = i.Component as (p: SlotProps[K]) => JSX.Element | null; return <C key={i.id} {...props} /> })}</>
}

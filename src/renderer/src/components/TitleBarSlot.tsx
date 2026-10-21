import { Slot, useSlot } from '../extensions/slot'

/** The extensions' items at one end of a title bar; nothing is drawn while no item is listed. */
export default function TitleBarSlot({ side }: { side: 'left' | 'right' }): JSX.Element | null {
  const name = `titlebar.${side}` as const
  const listed = useSlot(name).length > 0
  if (!listed) return null
  return <div className="titlebar-slot no-drag"><Slot name={name} props={{}} /></div>
}

import { useStore } from '../store'
import { Slot, useSlot } from '../extensions/slot'

/** The extensions' items at one end of a title bar; nothing is drawn while no item is listed or a profile closes the title bar. */
export default function TitleBarSlot({ side }: { side: 'left' | 'right' }): JSX.Element | null {
  const hidden = useStore((s) => s.hiddenChrome.includes('titlebar'))
  const name = `titlebar.${side}` as const
  const listed = useSlot(name).length > 0
  if (hidden || !listed) return null
  return <div className="titlebar-slot no-drag"><Slot name={name} props={{}} /></div>
}

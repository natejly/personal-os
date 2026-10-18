import { useStore } from '../store'
import { Slot, useSlot } from '../extensions/slot'

/** The bar along the window's foot: the extensions' `statusbar.item`s, drawn only while one is listed and no profile closes it. */
export default function StatusBar(): JSX.Element | null {
  const hidden = useStore((s) => s.hiddenChrome.includes('statusbar'))
  const listed = useSlot('statusbar.item').length > 0
  if (hidden || !listed) return null
  return <footer className="statusbar"><Slot name="statusbar.item" props={{}} /></footer>
}

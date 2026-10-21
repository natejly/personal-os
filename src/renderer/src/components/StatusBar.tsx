import { Slot, useSlot } from '../extensions/slot'

/** The bar along the window's foot: the extensions' `statusbar.item`s, drawn only while one is listed. */
export default function StatusBar(): JSX.Element | null {
  const listed = useSlot('statusbar.item').length > 0
  if (!listed) return null
  return <footer className="statusbar"><Slot name="statusbar.item" props={{}} /></footer>
}

import { useEffect } from 'react'
import { useShallow } from 'zustand/react/shallow'
import { useStore } from '../store'
import { pimStatus } from '../lib/pim'

/** A failed mail or calendar load. The account status is re-read on each error, so an expired
 *  sign-in (still "connected", but refused) shows a Reconnect button right here instead of only in Settings. */
export function PimErrorBar({ error }: { error: string | null }): JSX.Element | null {
  const google = useStore(pimStatus)
  const { refreshGoogle, connectGoogle } = useStore(useShallow((s) => ({ refreshGoogle: s.refreshGoogle, connectGoogle: s.connectGoogle })))
  useEffect(() => { if (error) void refreshGoogle() }, [error, refreshGoogle])
  if (!error) return null
  return (
    <div className="notice-bar error">
      {error}
      {google?.needs_reauth && <> <button className="link" onClick={() => void connectGoogle()}>Reconnect</button></>}
    </div>
  )
}

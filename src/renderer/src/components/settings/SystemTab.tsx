import { PermissionsPanel } from '../PermissionsPanel'
import { Sec } from './shared'

export default function SystemTab(): JSX.Element {
  return (
    <section className="system-tab">
      <h3>System access</h3>
      <Sec id="system"><PermissionsPanel /></Sec>
    </section>
  )
}

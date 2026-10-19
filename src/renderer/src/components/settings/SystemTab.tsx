import { PermissionsPanel } from '../PermissionsPanel'
import { Sec } from './shared'

export default function SystemTab(): JSX.Element {
  return (
    <section className="system-tab">
      <Sec id="system">
        <h4>System access</h4>
        <PermissionsPanel />
      </Sec>
    </section>
  )
}

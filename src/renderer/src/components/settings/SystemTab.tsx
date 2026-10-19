import { PermissionsPanel } from '../PermissionsPanel'
import { Sec } from './shared'

export default function SystemTab(): JSX.Element {
  return (
    <section className="system-tab">
      <Sec id="system">
        <h4>System access</h4>
        <p className="set-help">What macOS lets Grain do on this Mac: accessibility, screen recording, automation and the folders it may reach.</p>
        <PermissionsPanel />
      </Sec>
    </section>
  )
}

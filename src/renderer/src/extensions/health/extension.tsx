import { HeartPulse } from '../../icons'
import type { UiApi } from '../host'
import HealthView from './HealthView'

export function activate(ui: UiApi): void {
  ui.view({
    id: 'health',
    label: 'Health',
    description: 'Sleep, activity and workouts',
    icon: <HeartPulse size={15} />,
    Component: HealthView,
    // After Calendar (0) and Mail (10) in the title-bar strip.
    nav: { order: 18 }
  })
}

import { HeartPulse } from 'lucide-react'
import type { ModuleDef } from '../../shell/types'
import HealthView from './HealthView'

export const healthModule: ModuleDef = {
  key: 'health',
  label: 'Health',
  description: 'Sleep, activity and workouts synced from your fitness devices',
  icon: <HeartPulse size={15} />,
  view: { id: 'health', Component: HealthView, optional: true },
  // After Calendar (0) and Mail (10) in the title-bar strip.
  nav: { order: 18 },
}

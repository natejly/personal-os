import { HeartPulse } from 'lucide-react'
import type { ModuleDef } from '../../shell/types'
import HealthView from './HealthView'
import HealthCard from './HealthCard'

export const healthModule: ModuleDef = {
  key: 'health',
  label: 'Health',
  icon: <HeartPulse size={15} />,
  view: { id: 'health', Component: HealthView, optional: true },
  // After Calendar (0) and Mail (10) in the title-bar strip.
  nav: { section: 'apps', order: 20 },
  home: { key: 'health', label: 'Health', Card: HealthCard },
}

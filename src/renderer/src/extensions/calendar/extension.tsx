import { Calendar } from '../../icons'
import type { UiApi } from '../host'
import CalendarView from './CalendarView'
import { def as calendarWidget } from '../../canvas/widgets/calendar'

export function activate(ui: UiApi): void {
  ui.view({
    id: 'calendar',
    label: 'Calendar',
    description: 'Google Calendar',
    icon: <Calendar size={15} />,
    Component: CalendarView,
    nav: { order: 14 },
    widget: calendarWidget
  })
}

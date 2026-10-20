import { Calendar } from '../../icons'
import type { UiApi } from '../host'
import { useStore } from '../../store'
import { requestViewCommand } from '../../lib/viewCommands'
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
  ui.command({ id: 'calendar:refresh', label: 'Refresh calendar', run: () => { useStore.getState().setView('calendar'); requestViewCommand('calendar:refresh') } })
  ui.command({ id: 'calendar:plan-week', label: 'Plan my week', run: () => { useStore.getState().setView('calendar'); requestViewCommand('calendar:plan-week') } })
}

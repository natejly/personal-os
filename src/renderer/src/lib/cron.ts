/** Plain English for the repeat schedules the task form builds; any other cron string comes back as is. */
export const WEEKDAYS = ['Sundays', 'Mondays', 'Tuesdays', 'Wednesdays', 'Thursdays', 'Fridays', 'Saturdays']

export const describeCron = (cron: string): string => {
  const p = cron.trim().match(/^(\d{1,2}) (\d{1,2}) \* \* (\*|1-5|[0-6])$/)
  if (!p || +p[1] > 59 || +p[2] > 23) return cron
  const at = `${p[2].padStart(2, '0')}:${p[1].padStart(2, '0')}`
  return p[3] === '*' ? `Every day at ${at}` : p[3] === '1-5' ? `Weekdays at ${at}` : `${WEEKDAYS[+p[3]]} at ${at}`
}

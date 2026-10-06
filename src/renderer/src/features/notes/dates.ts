const WEEKDAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']
const MONTHS = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December'
]

const pad = (n: number): string => String(n).padStart(2, '0')

/** The viewer's LOCAL calendar day as YYYY-MM-DD. toISOString() is UTC and would roll the day over at the wrong hour. */
export function isoDate(d: Date): string {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

/** "Friday, October 2, 2026": the same shape the backend writes into a new daily note. */
export function longDate(d: Date): string {
  return `${WEEKDAYS[d.getDay()]}, ${MONTHS[d.getMonth()]} ${d.getDate()}, ${d.getFullYear()}`
}

/** 24-hour local time, HH:MM. */
export function clock(d: Date): string {
  return `${pad(d.getHours())}:${pad(d.getMinutes())}`
}

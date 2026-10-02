/** Collapse a mail header so it cannot add another line to the message the user sends. */
export function oneLine(value: string, max = 180): string {
  return value.replace(/[\u0000-\u001f\u007f\u2028\u2029"]/g, ' ').replace(/\s+/g, ' ').trim().slice(0, max)
}

/** The chat message for "ask about this email". The subject is someone else's text. */
export function emailAsk(id: string, subject: string | null | undefined): string {
  const label = oneLine(subject || '') || '(no subject)'
  const mid = oneLine(id, 80)
  return `Summarize this email and suggest a reply if one is needed. Message id: "${mid}". Subject: "${label}". Read it with gmail_read before answering.`
}

/** The chat message for "prep me for this event". The title is someone else's text. */
export function eventPrep(name: string, when: string, attendees: string[]): string {
  const title = oneLine(name) || 'this event'
  const at = oneLine(when, 80)
  const who = attendees.map((a) => oneLine(a, 80)).filter(Boolean)
  const whenBit = at ? ` (${at})` : ''
  const whoBit = who.length ? ` Attendees: ${who.join(', ')}.` : ''
  return `Prep me for "${title}"${whenBit}.${whoBit} Check my memory, documents and recent email for context on the attendees and topic, then give me a one-page brief.`
}

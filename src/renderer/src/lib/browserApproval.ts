/**
 * The sentence on an in-tool browser approval card, built from the arguments `browser.py` raises it with
 * (`{action, url, element, risk, form_action, href, text, files, reason, why}`). Returned as parts so the
 * renderer can emphasise the host without parsing a string.
 */
export interface Sentence { before: string; host: string; after: string }

const hostOf = (raw: unknown): string => {
  const s = String(raw ?? '').trim()
  if (!s) return ''
  try { return new URL(s).hostname } catch { return s.replace(/^[a-z]+:\/\//i, '').split(/[/?#]/)[0] }
}

/** Host and path of an address the user is asked to judge. */
const shortUrl = (raw: unknown): { host: string; rest: string } => {
  const s = String(raw ?? '').trim()
  try {
    const u = new URL(s)
    const rest = (u.pathname === '/' ? '' : u.pathname) + u.search
    return { host: u.hostname, rest: rest.length > 60 ? rest.slice(0, 57) + '…' : rest }
  } catch { return { host: hostOf(s), rest: '' } }
}

/** `button "Place order"` (what browser.py sends) or `{role, name}` -> the role word and the visible name. */
function element(raw: unknown): { role: string; name: string } {
  if (raw && typeof raw === 'object') {
    const e = raw as { role?: unknown; tag?: unknown; name?: unknown }
    return { role: String(e.role ?? e.tag ?? 'element'), name: String(e.name ?? '') }
  }
  const m = /^(\S+)\s+"([\s\S]*)"$/.exec(String(raw ?? ''))
  return m ? { role: m[1], name: m[2] } : { role: 'element', name: String(raw ?? '') }
}

export function browserSentence(args: Record<string, unknown>): Sentence {
  const action = String(args.action ?? '')
  const risk = String(args.risk ?? '')
  const url = shortUrl(args.url)
  const el = element(args.element)
  const q = el.name ? ` (${el.role} “${el.name}”)` : ''
  const on = (before: string, after = ''): Sentence => ({ before, host: url.host, after })
  switch (action) {
    case 'open':
      return on('Open ', `${url.rest}${args.why ? ` — ${String(args.why)}` : ''}`)
    case 'upload': {
      const files = Array.isArray(args.files) ? args.files.map(String).join(', ') : ''
      return on(`Upload ${files || 'a file'} to `)
    }
    case 'handoff':
      return { before: `Take over the browser: ${String(args.reason ?? 'the page needs you')}`, host: '', after: '' }
    case 'type':
      if (risk === 'password') return on('Type into the password field on ', q)
      if (risk === 'payment') return on('Type payment details on ', q)
      if (risk === 'submit') return on('Type and submit on ', q)
      return on('Type into the page on ', q)
    case 'select':
      return on(risk === 'payment' ? 'Choose a payment option on ' : 'Choose an option on ', q)
    case 'press':
      return on('Press a key that submits the form on ', q)
    case 'click': {
      if (risk === 'download') return on('Download a file from ', q)
      if (risk === 'submit') {
        const target = hostOf(args.form_action)
        return on('Submit the form on ', `${q}${target && target !== url.host ? `, which sends it to ${target}` : ''}`)
      }
      return on('Click on ', q)
    }
    default:
      return on(`Browser ${action || 'action'} on `, q)
  }
}

/** The primary button of a hand-off reads "I'm done": the browser window is open and the user is the one acting. */
export const browserAllowLabel = (args: Record<string, unknown>): string => (String(args.action ?? '') === 'handoff' ? "I'm done" : 'Allow')

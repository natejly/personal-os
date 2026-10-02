/**
 * One plain sentence for a browser approval card. The backend raises these from inside a browser tool call
 * (browser.py `card` / `browser_open` / upload / handoff), named `browser`, with arguments like
 *   { action: 'open', url, why }
 *   { action: 'click'|'type'|'select'|'press', element: 'button "Pay now"', url, risk, form_action?, href?, text? }
 *   { action: 'upload', ref, files: [names] }
 *   { action: 'handoff', reason, url }
 * Pure, so the transcript card and the desk approval banner can share it (browserApproval.test.ts).
 */

const str = (v: unknown): string => (typeof v === 'string' ? v : '')

/** `https://shop.example.com/cart?id=1#x` -> `shop.example.com/cart`. Not a URL: returned as typed, clipped. */
export function hostPath(url: string): string {
  try {
    const u = new URL(url)
    const path = u.pathname === '/' ? '' : u.pathname
    return `${u.host}${path}`
  } catch {
    return url.length > 80 ? url.slice(0, 79) + '…' : url
  }
}

/** What the page is, for "on <page>". Empty when the call carried no URL. */
function onPage(url: string): string {
  const h = hostPath(url)
  return h ? ` on ${h}` : ''
}

const RISK: Record<string, string> = {
  submit: 'This sends a form.',
  password: 'This is a password field; the text is hidden.',
  payment: 'This is a payment field; the text is hidden.',
  download: 'This downloads a file into the desk.'
}

export function browserApprovalSentence(args: Record<string, unknown> | null | undefined): string {
  const a = args ?? {}
  const action = str(a.action)
  const url = str(a.url)
  const element = str(a.element) || 'an element'
  const extra: string[] = []
  const risk = RISK[str(a.risk)]
  if (risk) extra.push(risk)
  if (str(a.form_action)) extra.push(`The form posts to ${hostPath(str(a.form_action))}.`)
  if (str(a.href)) extra.push(`The link goes to ${hostPath(str(a.href))}.`)
  const tail = extra.length ? ' ' + extra.join(' ') : ''
  switch (action) {
    case 'open': {
      const why = str(a.why)
      return `Open ${hostPath(url) || 'a page'} in the browser${why ? `, because ${why}` : ''}.`
    }
    case 'click': return `Click ${element}${onPage(url)}.${tail}`
    case 'type': {
      const text = str(a.text)
      const shown = !text ? '' : text === '<hidden>' ? ' (hidden)' : ` “${text.length > 60 ? text.slice(0, 59) + '…' : text}”`
      return `Type${shown} into ${element}${onPage(url)}.${tail}`
    }
    case 'select': return `Choose an option in ${element}${onPage(url)}.${tail}`
    case 'press': return `Press a key${element !== 'an element' ? ` in ${element}` : ''}${onPage(url)}.${tail}`
    case 'upload': {
      const files = Array.isArray(a.files) ? a.files.map(String) : []
      return `Upload ${files.length ? files.join(', ') : 'a file'} through the page's file chooser${str(a.ref) ? ` (${str(a.ref)})` : ''}.`
    }
    case 'handoff': return `Hand the browser over to you: ${str(a.reason) || 'the page needs you'}${url ? ` (${hostPath(url)})` : ''}.`
    default: return action ? `Let the browser ${action}${onPage(url)}.` : 'Let the agent act in the browser.'
  }
}

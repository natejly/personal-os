import { useEffect, useRef, useState } from 'react'
import { ArrowLeft, ArrowRight, Globe, RotateCw } from 'lucide-react'
import type { WidgetDef, WidgetProps } from '../registry'

/**
 * The <webview> tag: a real Chromium guest, because the open web does not allow itself into an iframe
 * (google.com, github.com and most login pages send X-Frame-Options). The tag only exists because the
 * windows opt in with `webviewTag: true`, and guardNavigation's will-attach-webview strips the guest
 * of the preload and node before it attaches. React's JSX types know the tag; its Electron methods
 * they do not, hence this small interface rather than a dependency on electron's renderer types.
 */
interface WebviewEl extends HTMLElement {
  src: string
  goBack: () => void
  goForward: () => void
  reload: () => void
  canGoBack: () => boolean
  canGoForward: () => boolean
  loadURL: (url: string) => Promise<void>
}

/** Every web widget shares one persistent session, so a login survives close, reopen and restart. */
const PARTITION = 'persist:web-widget'

/** An address bar accepts anything: a URL, a bare domain, or words — words go to search. */
export const toUrl = (input: string): string => {
  const s = input.trim()
  if (!s) return ''
  if (/^https?:\/\//i.test(s)) return s
  if (!/\s/.test(s) && s.includes('.')) return `https://${s}`
  return `https://www.google.com/search?q=${encodeURIComponent(s)}`
}

const host = (url: string): string => {
  try {
    return new URL(url).hostname.replace(/^www\./, '')
  } catch {
    return url
  }
}

function WebWidget({ window: win, live, onConfig, onTitle }: WidgetProps): JSX.Element {
  const url = typeof win.config.url === 'string' ? win.config.url : ''
  const wv = useRef<WebviewEl | null>(null)
  // The src the <webview> mounts with. Frozen while the guest lives: navigation writes config.url
  // back, and a reactive src={url} would then re-navigate the guest it just heard from, forever.
  const mountSrc = useRef(url)
  if (!wv.current) mountSrc.current = url
  /** What the address bar shows; null = tracking the page, a string = the user is typing. */
  const [draft, setDraft] = useState<string | null>(url ? null : '')
  const [address, setAddress] = useState(url)
  const [nav, setNav] = useState({ back: false, forward: false })
  /** The last title this widget set, so a hand-renamed window is never overwritten (the note's rule). */
  const derived = useRef('')
  const titled = useRef(win.title)
  useEffect(() => { titled.current = win.title }, [win.title])

  // webview events are DOM events on the element; attach once per mount. `url` is deliberately not a
  // dependency: re-running this on every navigation would double-bind, and the element outlives config.
  useEffect(() => {
    const el = wv.current
    if (!el) return
    const syncNav = (): void => setNav({ back: el.canGoBack(), forward: el.canGoForward() })
    const onNavigate = (e: Event): void => {
      const to = (e as Event & { url?: string }).url ?? ''
      if (!to) return
      setAddress(to)
      syncNav()
      onConfig({ url: to }) // reopening the window lands on the last page, not the first
    }
    const onInPage = (e: Event): void => {
      const d = e as Event & { url?: string; isMainFrame?: boolean }
      if (d.isMainFrame === false || !d.url) return
      setAddress(d.url)
      syncNav()
    }
    const onTitleEvent = (e: Event): void => {
      const t = ((e as Event & { title?: string }).title ?? '').trim().slice(0, 60)
      if (t && t !== titled.current && (titled.current === '' || titled.current === derived.current)) {
        derived.current = t
        onTitle(t)
      }
    }
    el.addEventListener('did-navigate', onNavigate)
    el.addEventListener('did-navigate-in-page', onInPage)
    el.addEventListener('page-title-updated', onTitleEvent)
    return () => {
      el.removeEventListener('did-navigate', onNavigate)
      el.removeEventListener('did-navigate-in-page', onInPage)
      el.removeEventListener('page-title-updated', onTitleEvent)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [live, !url, onConfig, onTitle]) // remount points: the guest exists only when live with a url

  const go = (input: string): void => {
    const to = toUrl(input)
    if (!to) return
    setDraft(null)
    setAddress(to)
    const el = wv.current
    if (el) void el.loadURL(to).catch(() => undefined)
    else onConfig({ url: to }) // first URL: mount the webview with it
  }

  // Off-screen or over the heavy cap: the guest process is the whole cost, so it unmounts. The
  // partition keeps cookies and logins; the page itself reloads on return.
  if (!live) {
    return (
      <div className="proxy-card">
        <Globe size={18} />
        <strong>{host(url) || 'Web'}</strong>
      </div>
    )
  }

  return (
    <div className="widget">
      <div className="widget-bar web-bar">
        <button className="widget-chip" title="Back" aria-label="Back" disabled={!nav.back} onClick={() => wv.current?.goBack()}><ArrowLeft size={11} /></button>
        <button className="widget-chip" title="Forward" aria-label="Forward" disabled={!nav.forward} onClick={() => wv.current?.goForward()}><ArrowRight size={11} /></button>
        <button className="widget-chip" title="Reload" aria-label="Reload" disabled={!url} onClick={() => wv.current?.reload()}><RotateCw size={11} /></button>
        <input
          className="widget-input web-address"
          type="text"
          placeholder="Search or enter address"
          value={draft ?? address}
          onChange={(e) => setDraft(e.target.value)}
          onFocus={(e) => e.target.select()}
          onBlur={() => setDraft(null)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') go(e.currentTarget.value)
            if (e.key === 'Escape') { setDraft(null); e.currentTarget.blur() }
          }}
        />
      </div>
      {url ? (
        // Opaque background, same reason as the dashboard iframe: a loading guest over the vibrancy
        // window reads as a hole to the desktop.
        <webview ref={(el) => { wv.current = el as WebviewEl | null }} src={mountSrc.current} partition={PARTITION}
          style={{ flex: 1, width: '100%', background: '#fff' }} />
      ) : (
        <div className="widget-empty web-empty">
          <Globe size={22} />
          <span>Search or enter an address above — or drop a link onto the canvas.</span>
        </div>
      )}
    </div>
  )
}

export const def: WidgetDef = {
  kind: 'web',
  label: 'Web',
  icon: <Globe size={18} />,
  defaultSize: { w: 560, h: 420 },
  minSize: { w: 280, h: 200 },
  chrome: 'full',
  heavy: true,
  Component: WebWidget
}

export default WebWidget

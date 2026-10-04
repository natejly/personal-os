import { useState } from 'react'
import { Eye, EyeOff, Globe } from 'lucide-react'
import { chatBrowserSession, hostPath } from '../../lib/browserApproval'
import DeskBrowser from '../DeskBrowser'
import { browserLine, num, str, strList } from '../../lib/toolResult'
import CardShell from './CardShell'
import { ErrorLine, Meta, MonoBlock, unreadable, useParsed } from './blocks'
import { registerToolCard, type ToolCardProps } from './registry'

const NAMES = ['browser_open', 'browser_snapshot', 'browser_click', 'browser_type', 'browser_select', 'browser_press', 'browser_scroll', 'browser_manage']

/**
 * browser_*: one line for what was done, the page it left the browser on, any notes, and the page text behind a
 * disclosure. The reply's last browser card can also show the chat's browser live, with Take over and Hide.
 */
export default function BrowserCard(props: ToolCardProps): JSX.Element {
  const { event } = props
  const p = useParsed(event)
  const d = p.data
  const line = browserLine(event.name, event.arguments, d)
  const snapshot = str(d?.snapshot)
  const notes = d ? strList(d.notes) : []
  const shot = event.name === 'browser_manage' && str(d?.path) ? str(d?.path) : ''
  const tabs = num(d?.tabs)
  const [watch, setWatch] = useState(false)
  return (
    <CardShell {...props} icon={<Globe size={14} />} title={line.action} subject={line.subject || undefined} hideResult={!unreadable(p, event)}>
      {d && (str(d.title) || str(d.url)) && (
        <Meta items={[
          ['Page', str(d.title) || null],
          ['Address', str(d.url) ? <span className="mono" title={str(d.url)}>{hostPath(str(d.url))}</span> : null],
          ['Tabs', tabs !== null && tabs > 1 ? String(tabs) : null]
        ]} />
      )}
      {shot && (
        <Meta items={[['Screenshot saved', <span className="mono" key="p">{shot}</span>], ['Size', num(d?.width) && num(d?.height) ? `${num(d?.width)} × ${num(d?.height)}` : null]]} />
      )}
      {notes.length > 0 && <ul className="tc-list tc-notes">{notes.map((n, i) => <li key={i}>{n}</li>)}</ul>}
      {snapshot && (
        <details className="tc-snapshot">
          <summary>Page text{p.cut ? ' (cut short)' : ''}</summary>
          <MonoBlock text={snapshot} collapseAt={400} />
        </details>
      )}
      <ErrorLine event={event} />
      {props.latestBrowser && props.conversationId && window.os?.agentBrowser && (
        <>
          <button type="button" className="link small" aria-expanded={watch} onClick={() => setWatch((w) => !w)}>
            {watch ? <EyeOff size={12} /> : <Eye size={12} />} {watch ? 'Stop watching' : 'Watch the browser'}
          </button>
          {watch && (
            <DeskBrowser session={chatBrowserSession(props.conversationId)} live={!!props.streaming}
              emptyText="This chat's browser is closed (it shuts after a few idle minutes)." />
          )}
        </>
      )}
    </CardShell>
  )
}

for (const n of NAMES) registerToolCard(n, BrowserCard)

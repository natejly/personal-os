import { Camera, Send } from 'lucide-react'
import { attachedNames, num, str } from '../../lib/toolResult'
import CardShell from './CardShell'
import { Badge, ErrorLine, Meta, unreadable, useParsed } from './blocks'
import { registerToolCard, type ToolCardProps } from './registry'

/** The pictures a call produced, in the markup every image-making tool shares. */
function Images({ event }: Pick<ToolCardProps, 'event'>): JSX.Element | null {
  if (!event.images?.length) return null
  return (
    <div className="tool-images">
      {event.images.map((im) => (
        <figure key={im.name}>
          <img src={im.data} alt={im.name} />
          <figcaption>{im.name} <a href={im.data} download={im.name}>save</a></figcaption>
        </figure>
      ))}
    </div>
  )
}

/** screenshot: what was captured (the screen, a window of an app, a region) and the picture itself. */
function ScreenshotCard(props: ToolCardProps): JSX.Element {
  const { event } = props
  const p = useParsed(event)
  const a = event.arguments
  const target = str(a.target) || 'screen'
  const shot = Array.isArray(p.data?.saved) ? (p.data?.saved[0] as Record<string, unknown> | undefined) : undefined
  const w = num(shot?.width)
  const h = num(shot?.height)
  return (
    <CardShell {...props} icon={<Camera size={14} />} title="Screenshot" subject={str(a.app) || target} hideResult={!unreadable(p, event)}>
      <Meta items={[['Target', target], ['App', str(a.app) || null], ['Window', str(a.title) || null], ['Region', str(a.region) || null], ['Size', w && h ? `${w} × ${h}` : null]]} />
      <Images event={event} />
      <ErrorLine event={event} />
    </CardShell>
  )
}

/** send_files: the files handed to the user, and where they went (the phone at once, or only the reply in the app). */
function SendFilesCard(props: ToolCardProps): JSX.Element {
  const { event } = props
  const p = useParsed(event)
  const names = attachedNames(p.data)
  const phone = p.data?.delivered === 'telegram'
  const text = str(p.data?.text) || str(event.arguments.text)
  return (
    <CardShell {...props} icon={<Send size={14} />} title="Send files" subject={names.length ? names.join(', ') : undefined} hideResult={!unreadable(p, event)}>
      {phone && !event.error && <div className="tc-statusrow"><Badge tone="ok">Sent to your phone</Badge></div>}
      <Meta items={[['Files', names.length ? names.join(', ') : null], ['Note', text || null]]} />
      <Images event={event} />
      <ErrorLine event={event} />
    </CardShell>
  )
}

registerToolCard('screenshot', ScreenshotCard)
registerToolCard('send_files', SendFilesCard)

import { ShieldQuestion } from 'lucide-react'
import { browserApprovalSentence } from '../../lib/browserApproval'
import CardShell from './CardShell'
import { registerToolCard, type ToolCardProps } from './registry'

/**
 * The approval a browser tool raises from inside its own call (name `browser`): one sentence saying what the
 * agent is about to do in the page. The arguments are already shaped for reading (typed text arrives hidden for
 * password and payment fields), so Details is safe to open.
 */
function BrowserApprovalCard(props: ToolCardProps): JSX.Element {
  const handoff = props.event.arguments.action === 'handoff'
  const risky = ['submit', 'password', 'payment', 'download'].includes(String(props.event.arguments.risk ?? ''))
  return (
    <CardShell {...props} icon={<ShieldQuestion size={14} />} title={handoff ? 'Browser needs you' : 'Browser needs your OK'} approveLabel={handoff ? 'Hand back' : undefined} denyLabel={handoff ? 'Cancel' : undefined} tone={risky && props.pending ? 'warn' : undefined} hideResult>
      <p className="tc-sentence">{browserApprovalSentence(props.event.arguments)}</p>
    </CardShell>
  )
}

registerToolCard('browser', BrowserApprovalCard)

import { api } from '../lib/api'
import { modelLabel } from '../lib/modelLabel'
import { useStore } from '../store'
import type { DuelSide, DuelWinner } from '@shared/types'

function Column({ title, side }: { title: string; side: DuelSide }): JSX.Element {
  return (
    <div className="duel-col">
      <div className="duel-head"><b>{title}</b> {modelLabel(side.model)}{side.done && side.ms != null && <small> {(side.ms / 1000).toFixed(1)}s · {side.tokens ?? 0} tok</small>}{!side.done && <small> streaming…</small>}</div>
      <pre className="duel-text">{side.text || (side.done && !side.error ? '(no text)' : '')}</pre>
      {side.error && <div className="notice error" role="alert">{side.error}</div>}
      {side.calls.length > 0 && (
        <div className="duel-calls">
          <small>Wants to run (not run during the duel):</small>
          {side.calls.map((c, i) => <code key={i}>{c.name}({c.args})</code>)}
        </div>
      )}
    </div>
  )
}

/** The two replies of a developer-mode duel, side by side, until one is picked. */
export default function DuelPanel({ conversationId }: { conversationId: string | undefined }): JSX.Element | null {
  const duel = useStore((s) => (conversationId ? s.sessions[conversationId]?.duel : null))
  const toast = useStore((s) => s.toast)
  if (!conversationId || !duel) return null
  const pick = (w: DuelWinner): void => { void api.abPick(conversationId, duel.messageId, w).catch((e: Error) => toast(e.message, 'error')) }
  return (
    <div className="duel" role="group" aria-label="Model duel">
      <div className="duel-cols">
        <Column title="A" side={duel.a} />
        <Column title="B" side={duel.b} />
      </div>
      <div className="duel-pick">
        <button className="ghost-btn" disabled={!duel.ready} onClick={() => pick('a')}>Use A</button>
        <button className="ghost-btn" disabled={!duel.ready || !!duel.b.error} onClick={() => pick('b')}>Use B</button>
        <button className="ghost-btn" disabled={!duel.ready} onClick={() => pick('tie')}>Tie (use A)</button>
        <button className="ghost-btn" disabled={!duel.ready} onClick={() => pick('bad')}>Both bad (use A)</button>
      </div>
    </div>
  )
}

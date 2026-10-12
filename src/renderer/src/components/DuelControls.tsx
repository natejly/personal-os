import { DEFAULT_EFFORT } from '@shared/types'
import { useStore } from '../store'
import ModelMenu from './ModelMenu'

/** Developer mode: also send the next messages to a second model and compare the replies. Hidden unless Developer tools is on. */
export default function DuelControls(): JSX.Element | null {
  const devTools = useStore((s) => s.settings.devTools === true)
  const on = useStore((s) => s.abOn)
  const model = useStore((s) => s.abModel)
  const defaultModel = useStore((s) => s.settings.defaultModel)
  const setAb = useStore((s) => s.setAb)
  if (!devTools) return null
  return (
    <span className="duel-controls">
      <button type="button" className={`ghost-btn${on ? ' on' : ''}`} aria-pressed={on} title="Send each message to two models and pick the better reply"
        onClick={() => setAb({ on: !on, model: model ?? defaultModel })}>A/B</button>
      {on && (
        <>
          <span className="duel-vs">vs</span>
          <ModelMenu model={model ?? defaultModel} effort={DEFAULT_EFFORT} fast={false} placement="up" onChange={(c) => { if (c.model) setAb({ model: c.model }) }} />
        </>
      )}
    </span>
  )
}

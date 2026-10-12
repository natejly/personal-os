import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { modelLabel } from '../lib/modelLabel'
import type { ModelStanding } from '@shared/types'

/** Settings > Model, developer mode: the head-to-head record per model from the duels you judged. */
export default function ModelDuels(): JSX.Element {
  const [data, setData] = useState<{ votes: number; models: ModelStanding[] } | null>(null)
  const load = (): void => { void api.modelVotes().then(setData).catch(() => setData({ votes: 0, models: [] })) }
  useEffect(load, [])
  const reset = (): void => { void api.resetModelVotes().then(load) }
  return (
    <div className="model-duels">
      <div className="setting-row">
        <span className="toggle-text"><b>Model duels</b><small>{data ? `${data.votes} judged. Turn on A/B under the composer to add more.` : 'Loading…'}</small></span>
        <button className="ghost-btn" type="button" disabled={!data?.votes} onClick={reset}>Reset</button>
      </div>
      {!!data?.models.length && (
        <table className="duel-table">
          <thead><tr><th>Model</th><th>Elo</th><th>W</th><th>L</th><th>T</th><th>Win rate</th><th>Mean</th></tr></thead>
          <tbody>
            {data.models.map((m) => (
              <tr key={m.model}>
                <td>{modelLabel(m.model)}</td><td>{m.elo}</td><td>{m.wins}</td><td>{m.losses}</td><td>{m.ties}</td>
                <td>{m.win_rate == null ? '-' : `${Math.round(m.win_rate * 100)}%`}</td>
                <td>{m.mean_ms == null ? '-' : `${(m.mean_ms / 1000).toFixed(1)}s`}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}

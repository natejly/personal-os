import { useState } from 'react'
import { useStore } from '../../store'
import { DIGEST_TOKENS, MERGES_PER_TURN, SETTING_PREFIX, clampInt, type RebuildResult } from './cover'
import { useDigest } from './store'

const key = (name: string): never => `${SETTING_PREFIX}${name}` as never

/** Settings > History digest: the digest's size, how much it summarises per turn, and a manual rebuild. */
export default function DigestSettings(): JSX.Element {
  const settings = useStore((s) => s.settings) as unknown as Record<string, unknown>
  const save = useStore((s) => s.saveSettings)
  const rebuild = useDigest((s) => s.rebuild)
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<RebuildResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const put = (name: string, value: unknown): void => { void save({ [key(name)]: value } as never) }
  const get = <T,>(name: string, fallback: T): T => (settings[`${SETTING_PREFIX}${name}`] as T | undefined) ?? fallback
  const run = (): void => {
    setBusy(true); setError(null)
    rebuild('personal').then(setResult).catch((e: Error) => setError(e.message)).finally(() => setBusy(false))
  }
  return (
    <>
      <label className="set-row">Digest size
        <input type="number" min={DIGEST_TOKENS.min} max={DIGEST_TOKENS.max} value={get('memoryTreeDigestTokens', DIGEST_TOKENS.fallback)}
          onChange={(e) => put('memoryTreeDigestTokens', clampInt(e.target.value, DIGEST_TOKENS.min, DIGEST_TOKENS.max, DIGEST_TOKENS.fallback))} />
      </label>
      <label className="set-row">Summaries built per turn
        <input type="number" min={MERGES_PER_TURN.min} max={MERGES_PER_TURN.max} value={get('memoryTreeMergesPerTurn', MERGES_PER_TURN.fallback)}
          onChange={(e) => put('memoryTreeMergesPerTurn', clampInt(e.target.value, MERGES_PER_TURN.min, MERGES_PER_TURN.max, MERGES_PER_TURN.fallback))} />
      </label>
      <label className="set-row">Summary model
        <select value={get('memoryTreeMergeModel', 'fast')} onChange={(e) => put('memoryTreeMergeModel', e.target.value)}>
          <option value="fast">Fast model</option>
          <option value="default">Default model</option>
        </select>
      </label>
      <label className="set-row">
        <input type="checkbox" checked={get('memoryTreeIncludeAuto', true)} onChange={(e) => put('memoryTreeIncludeAuto', e.target.checked)} />
        Include memories the assistant saved on its own
      </label>
      <div className="set-row">
        <button type="button" className="ghost-btn sm" disabled={busy} onClick={run}>{busy ? 'Rebuilding…' : 'Rebuild digest'}</button>
        {result && <span className="set-help" role="status">{result.leaves} entries, {result.nodes} summaries</span>}
        {error && <span className="set-help" role="status">{error}</span>}
      </div>
    </>
  )
}

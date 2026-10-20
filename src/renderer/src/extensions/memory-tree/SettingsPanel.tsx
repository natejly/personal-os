import { useState } from 'react'
import { useStore } from '../../store'
import { DIGEST_TOKENS, MERGES_PER_TURN, SETTING_PREFIX, clampInt, type RebuildResult } from './cover'
import { useDigest } from './store'
import SettingRow from '../../components/settings/SettingRow'
import Switch from '../../components/settings/Switch'

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
      <SettingRow title="Digest size">
        <input type="number" aria-label="Digest size" min={DIGEST_TOKENS.min} max={DIGEST_TOKENS.max} value={get('memoryTreeDigestTokens', DIGEST_TOKENS.fallback)}
          onChange={(e) => put('memoryTreeDigestTokens', clampInt(e.target.value, DIGEST_TOKENS.min, DIGEST_TOKENS.max, DIGEST_TOKENS.fallback))} />
      </SettingRow>
      <SettingRow title="Summaries per turn">
        <input type="number" aria-label="Summaries per turn" min={MERGES_PER_TURN.min} max={MERGES_PER_TURN.max} value={get('memoryTreeMergesPerTurn', MERGES_PER_TURN.fallback)}
          onChange={(e) => put('memoryTreeMergesPerTurn', clampInt(e.target.value, MERGES_PER_TURN.min, MERGES_PER_TURN.max, MERGES_PER_TURN.fallback))} />
      </SettingRow>
      <SettingRow title="Summary model">
        <select aria-label="Summary model" value={get('memoryTreeMergeModel', 'fast')} onChange={(e) => put('memoryTreeMergeModel', e.target.value)}>
          <option value="fast">Fast model</option>
          <option value="default">Default model</option>
        </select>
      </SettingRow>
      <Switch title="Automatic memories" checked={get('memoryTreeIncludeAuto', true)} onChange={(v) => put('memoryTreeIncludeAuto', v)} />
      <SettingRow title="Rebuild digest">
        {result && <span className="set-help" role="status">{result.leaves} entries, {result.nodes} summaries</span>}
        {error && <span className="set-help" role="status">{error}</span>}
        <button type="button" className="ghost-btn sm" disabled={busy} onClick={run}>{busy ? 'Rebuilding…' : 'Rebuild'}</button>
      </SettingRow>
    </>
  )
}

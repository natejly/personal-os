import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { PREVIEW_SANDBOX } from '../lib/htmlFence'

/** Pinned: the artifact route is also served under a `sandbox allow-scripts` CSP header, so even if this
 *  attribute were dropped the document would stay in an opaque origin. Never add allow-same-origin. */
export const FRAME_SANDBOX = PREVIEW_SANDBOX

/**
 * A saved artifact, live, in a sandboxed iframe. The src is the backend render route with a signed,
 * expiring, per-artifact token (the iframe cannot send the app token), fetched fresh on mount so a card
 * opened days later still has a valid URL.
 */
export default function ArtifactFrame({ id, version, height, title, reloadKey }: {
  id: string
  /** Pin a version; omit for the latest. */
  version?: number | null
  height: number | string
  title: string
  reloadKey?: number | string
}): JSX.Element {
  const [src, setSrc] = useState<string | null>(null)
  const [err, setErr] = useState('')

  useEffect(() => {
    let live = true
    setErr('')
    const p = version ? api.artifacts.version(id, version).then((v) => v.render_path) : api.artifacts.get(id).then((a) => a.render_path)
    void p.then((path) => { if (live && path) setSrc(api.artifacts.renderUrl(path)) })
      .catch((e: Error) => { if (live) setErr(e.message || 'Could not load') })
    return () => { live = false }
  }, [id, version, reloadKey])

  if (err) return <div className="art-frame-empty" style={{ height }}>{/404|No such/.test(err) ? 'This artifact was deleted.' : `Could not load: ${err}`}</div>
  if (!src) return <div className="art-frame-empty" style={{ height }}>Loading…</div>
  return <iframe className="art-frame" title={title} sandbox={FRAME_SANDBOX} src={src} style={{ height }} />
}

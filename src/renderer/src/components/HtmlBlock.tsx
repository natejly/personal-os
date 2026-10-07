import { useEffect, useMemo, useRef, useState } from 'react'
import { Code2, Eye } from 'lucide-react'
import { clampPreviewHeight, PREVIEW_HEIGHT_MESSAGE, previewUrl } from '../../../shared/htmlPreview'
import { buildPreviewDoc, PREVIEW_SANDBOX } from '../lib/htmlFence'
import { CopyButton } from './MarkdownPreview'
import OpenInPanel from './ShowButton'
import '../styles/htmlFence.css'

/**
 * A fenced ```html block in an assistant reply. Code | Preview toggle; the preview is an iframe in an opaque
 * origin (sandbox allow-scripts, never allow-same-origin) loading the `grain-preview:` scheme, which serves it
 * with its own CSP: scripts run, nothing can connect. Model output is never executed in the app's origin.
 */
export default function HtmlBlock({ source, streaming }: { source: string; streaming: boolean }): JSX.Element {
  const [mode, setMode] = useState<'code' | 'preview'>('preview')
  const [id, setId] = useState<string | null>(null)
  const [height, setHeight] = useState<number | undefined>()
  const frame = useRef<HTMLIFrameElement>(null)
  const showing = streaming || !id ? 'code' : mode

  useEffect(() => {
    if (streaming) return
    let live = true
    window.os?.previewPut?.(source).then((i) => live && setId(i), () => {})
    return () => { live = false }
  }, [source, streaming])

  useEffect(() => {
    const onMessage = (e: MessageEvent): void => {
      if (e.source !== frame.current?.contentWindow || e.data?.type !== PREVIEW_HEIGHT_MESSAGE) return
      const h = clampPreviewHeight(e.data.height)
      if (h !== null) setHeight(h)
    }
    window.addEventListener('message', onMessage)
    return () => window.removeEventListener('message', onMessage)
  }, [])

  return (
    <div className="code-block art-fence">
      <div className="code-head">
        <span>html</span>
        <span className="art-fence-actions">
          <span className="seg" role="tablist" aria-label="Code or preview">
            <button role="tab" aria-selected={showing === 'code'} className={showing === 'code' ? 'on' : ''} onClick={() => setMode('code')}><Code2 size={11} /> Code</button>
            <button role="tab" aria-selected={showing === 'preview'} className={showing === 'preview' ? 'on' : ''} disabled={streaming} onClick={() => setMode('preview')}><Eye size={11} /> Preview</button>
          </span>
          {!streaming && <OpenInPanel kind="html" source={source} />}
          <CopyButton text={source} />
        </span>
      </div>
      {showing === 'code' ? (
        <pre><code>{source}</code></pre>
      ) : (
        <iframe ref={frame} className="art-fence-frame" title="HTML preview" sandbox={PREVIEW_SANDBOX} src={previewUrl(id!)} style={{ height }} />
      )}
    </div>
  )
}

/** A fenced ```svg block, shown as an image. Same iframe sandbox, and its CSP forbids script outright. */
export function SvgBlock({ source, streaming }: { source: string; streaming: boolean }): JSX.Element {
  const doc = useMemo(() => (streaming ? '' : buildPreviewDoc(source)), [source, streaming])
  const [code, setCode] = useState(false)
  return (
    <div className="code-block art-fence">
      <div className="code-head">
        <span>svg</span>
        <span className="art-fence-actions">
          <span className="seg">
            <button className={!code ? 'on' : ''} disabled={streaming} onClick={() => setCode(false)}><Eye size={11} /> Image</button>
            <button className={code || streaming ? 'on' : ''} onClick={() => setCode(true)}><Code2 size={11} /> Code</button>
          </span>
          {!streaming && <OpenInPanel kind="svg" source={source} />}
          <CopyButton text={source} />
        </span>
      </div>
      {code || streaming
        ? <pre><code>{source}</code></pre>
        : <iframe className="art-fence-frame svg" title="SVG preview" sandbox={PREVIEW_SANDBOX} srcDoc={doc} />}
    </div>
  )
}

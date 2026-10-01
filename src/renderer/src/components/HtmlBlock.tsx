import { useMemo, useState } from 'react'
import { Code2, Eye, Package, Check } from 'lucide-react'
import { api } from '../lib/api'
import { useStore } from '../store'
import { buildPreviewDoc, hasScript, PREVIEW_SANDBOX, titleOf } from '../lib/htmlFence'
import { CopyButton } from './MarkdownPreview'
import '../styles/artifacts.css'

/**
 * A fenced ```html block in an assistant reply. Code | Preview toggle; the preview is a `srcdoc` iframe in an
 * opaque origin (sandbox allow-scripts, never allow-same-origin) with a CSP meta tag injected first. Model
 * output is never executed in the app's origin. "Save as artifact" files it under Library -> Made, where it
 * is served by the artifact render route and its scripts can run.
 */
export default function HtmlBlock({ source, streaming }: { source: string; streaming: boolean }): JSX.Element {
  const [mode, setMode] = useState<'code' | 'preview'>('preview')
  const [saved, setSaved] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const doc = useMemo(() => (streaming ? '' : buildPreviewDoc(source, 'html')), [source, streaming])
  const showing = streaming ? 'code' : mode

  const save = async (): Promise<void> => {
    if (busy || saved) return
    setBusy(true)
    try {
      const st = useStore.getState()
      const a = await api.artifacts.create({ title: titleOf(source), code: source, conversation_id: st.focusedConversationId ?? undefined })
      setSaved(a.id)
      st.toast('Saved as artifact', 'info')
    } catch (e) {
      useStore.getState().toast((e as Error).message, 'error')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="code-block art-fence">
      <div className="code-head">
        <span>html</span>
        <span className="art-fence-actions">
          <span className="seg" role="tablist" aria-label="Code or preview">
            <button role="tab" aria-selected={showing === 'code'} className={showing === 'code' ? 'on' : ''} onClick={() => setMode('code')}><Code2 size={11} /> Code</button>
            <button role="tab" aria-selected={showing === 'preview'} className={showing === 'preview' ? 'on' : ''} disabled={streaming} onClick={() => setMode('preview')}><Eye size={11} /> Preview</button>
          </span>
          <button className="icon-btn ghost" title={saved ? 'Saved to Library → Made' : 'Save as artifact'} aria-label="Save as artifact" disabled={streaming || busy || !!saved} onClick={() => void save()}>
            {saved ? <Check size={13} /> : <Package size={13} />}
          </button>
          <CopyButton text={source} />
        </span>
      </div>
      {showing === 'code' ? (
        <pre><code>{source}</code></pre>
      ) : (
        <>
          <iframe className="art-fence-frame" title="HTML preview" sandbox={PREVIEW_SANDBOX} srcDoc={doc} />
          {hasScript(source) && (
            <p className="art-fence-note">Scripts are blocked in this inline preview. Save as artifact to run it.</p>
          )}
        </>
      )}
    </div>
  )
}

/** A fenced ```svg block, shown as an image. Same iframe sandbox, and its CSP forbids script outright. */
export function SvgBlock({ source, streaming }: { source: string; streaming: boolean }): JSX.Element {
  const doc = useMemo(() => (streaming ? '' : buildPreviewDoc(source, 'svg')), [source, streaming])
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
          <CopyButton text={source} />
        </span>
      </div>
      {code || streaming
        ? <pre><code>{source}</code></pre>
        : <iframe className="art-fence-frame svg" title="SVG preview" sandbox={PREVIEW_SANDBOX} srcDoc={doc} />}
    </div>
  )
}

import { useEffect, useRef, useState } from 'react'
import { GitBranch, Code2, Copy, Check, AlertCircle } from 'lucide-react'
import { useStore } from '../store'

/** Renders a ```mermaid fenced block as a diagram. Mermaid is loaded on first use (it is a large dependency). */

type Mermaid = typeof import('mermaid')['default']
let mermaidPromise: Promise<Mermaid> | null = null
let initializedFor: string | null = null
let seq = 0

async function getMermaid(dark: boolean): Promise<Mermaid> {
  mermaidPromise ??= import('mermaid').then((m) => m.default)
  const m = await mermaidPromise
  const key = dark ? 'dark' : 'light'
  if (initializedFor !== key) {
    m.initialize({
      startOnLoad: false,
      securityLevel: 'strict',
      theme: dark ? 'dark' : 'neutral',
      fontFamily: 'var(--font)',
      themeVariables: dark ? { background: '#232220', primaryColor: '#2f2d2a', lineColor: '#9c9a94' } : { background: '#ffffff' }
    })
    initializedFor = key
  }
  return m
}

export function useDarkTheme(): boolean {
  const theme = useStore((s) => s.settings.theme)
  return theme === 'dark' || (theme === 'system' && window.matchMedia('(prefers-color-scheme: dark)').matches)
}

export default function MermaidBlock({ source, streaming }: { source: string; streaming: boolean }): JSX.Element {
  const dark = useDarkTheme()
  const [svg, setSvg] = useState<string>('')
  const [error, setError] = useState<string | null>(null)
  const [view, setView] = useState<'diagram' | 'source'>('diagram')
  const [copied, setCopied] = useState(false)
  const last = useRef('')

  useEffect(() => {
    let cancelled = false
    const t = setTimeout(async () => {
      const code = source.trim()
      if (!code || (code === last.current && !!svg && !error)) return
      try {
        const m = await getMermaid(dark)
        await m.parse(code)
        const { svg: out } = await m.render(`pos-mermaid-${++seq}`, code)
        if (cancelled) return
        last.current = code
        setSvg(out)
        setError(null)
      } catch (e) {
        if (cancelled) return
        setError((e as Error).message || String(e))
      }
    }, streaming ? 400 : 0)
    return () => { cancelled = true; clearTimeout(t) }
  }, [source, dark, streaming])

  const copy = (): void => { void navigator.clipboard.writeText(source); setCopied(true); setTimeout(() => setCopied(false), 1200) }

  if (!svg && error && streaming) return <div className="chart-block placeholder"><GitBranch size={14} /> Drawing diagram…</div>
  if (!svg && !error) return <div className="chart-block placeholder"><GitBranch size={14} /> Drawing diagram…</div>
  return (
    <figure className={`chart-block mermaid ${error && !streaming ? 'error' : ''}`}>
      <div className="code-head">
        <span>diagram</span>
        <div className="chart-tools">
          <button className={`icon-btn ghost ${view === 'diagram' ? 'on' : ''}`} title="Diagram" onClick={() => setView('diagram')}><GitBranch size={13} /></button>
          <button className={`icon-btn ghost ${view === 'source' ? 'on' : ''}`} title="Source" onClick={() => setView('source')}><Code2 size={13} /></button>
          <button className="icon-btn ghost" title="Copy source" onClick={copy}>{copied ? <Check size={13} /> : <Copy size={13} />}</button>
        </div>
      </div>
      {error && !streaming && <div className="chart-err"><AlertCircle size={14} /> {svg ? 'Latest edit failed to parse; showing previous render. ' : "Couldn't render diagram: "}{error}</div>}
      {view === 'diagram' && svg ? <div className="mermaid-svg" dangerouslySetInnerHTML={{ __html: svg }} /> : null}
      {(view === 'source' || (!svg && error)) && <pre className="chart-source">{source}</pre>}
    </figure>
  )
}

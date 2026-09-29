import { useEffect, useMemo } from 'react'
import { Code2, FileCode2, FileText, LayoutTemplate, PenTool, ArrowUpRight } from 'lucide-react'
import type { ArtifactKind } from '@shared/types'
import { useStore } from '../store'
import { KIND_LABEL, parseArtifactBlock } from '../lib/artifacts'

const ICON: Record<ArtifactKind, typeof Code2> = {
  html: LayoutTemplate, svg: PenTool, react: FileCode2, markdown: FileText, code: Code2
}

/**
 * The inline stand-in for an ```artifact block. The document itself lives on the canvas; this is the
 * card in the transcript that opens it, and the thing that registers a still-streaming artifact with
 * the store so the canvas can render it before the reply finishes.
 */
export default function ArtifactBlock({ source, streaming }: { source: string; streaming: boolean }): JSX.Element | null {
  const draft = useMemo(() => parseArtifactBlock(source, !streaming), [source, streaming])
  const noteArtifact = useStore((s) => s.noteArtifact)
  const openCanvas = useStore((s) => s.openCanvas)
  const openId = useStore((s) => (s.canvas.open ? s.canvas.identifier : null))
  const saved = useStore((s) => (draft ? s.artifacts.find((a) => a.identifier === draft.identifier) : undefined))

  useEffect(() => {
    if (draft) noteArtifact(draft)
  }, [draft, noteArtifact])

  if (!draft) return null
  const Icon = ICON[draft.kind]
  const active = openId === draft.identifier
  const lines = draft.content ? draft.content.split('\n').length : 0

  return (
    <button
      className={`artifact-card ${active ? 'active' : ''} ${streaming ? 'writing' : ''}`}
      onClick={() => openCanvas(draft.identifier)}
      title={active ? 'Showing on the canvas' : 'Open on the canvas'}
    >
      <span className="artifact-card-icon"><Icon size={16} /></span>
      <span className="artifact-card-main">
        <span className="artifact-card-title">{saved?.title ?? draft.title}</span>
        <span className="artifact-card-meta">
          {KIND_LABEL[saved?.kind ?? draft.kind]}
          {saved && saved.version > 1 && <> · v{saved.version}</>}
          {!streaming && lines > 0 && <> · {lines} line{lines === 1 ? '' : 's'}</>}
          {streaming && <> · writing…</>}
        </span>
      </span>
      {streaming ? <span className="artifact-card-pulse" /> : <ArrowUpRight size={14} className="artifact-card-go" />}
    </button>
  )
}

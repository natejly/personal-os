import { useEffect, useRef, useState } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Check, Download } from 'lucide-react'
import { normalizeMathBlocks } from '../../lib/mathBlocks'
import { printFilename } from '../../lib/printDoc'
import { useStore } from '../../store'
import { copyMarkdown, downloadMarkdown, printDoc, stripAiFences } from './exportDoc'
import '../../styles/notes.css'

/** Download the doc as .md, copy its markdown, export a typeset PDF (saved anywhere, or into Uploads), or print it. */
export default function ExportMenu({ title, content, projectId = null }: { title: string; content: string; projectId?: string | null }): JSX.Element {
  const [open, setOpen] = useState(false)
  const [copied, setCopied] = useState(false)
  const root = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const away = (e: MouseEvent): void => { if (!root.current?.contains(e.target as Node)) setOpen(false) }
    const esc = (e: KeyboardEvent): void => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', away)
    document.addEventListener('keydown', esc)
    return () => { document.removeEventListener('mousedown', away); document.removeEventListener('keydown', esc) }
  }, [open])

  const exportPdf = async (mode: 'save' | 'bytes'): Promise<void> => {
    setOpen(false)
    const { toast, uploadDocuments } = useStore.getState()
    const name = printFilename(title)
    try {
      const r = await window.os.print.exportPdf(title || 'Untitled', stripAiFences(content), name, mode)
      if (!r) return
      if (typeof r === 'string') toast(`Saved ${name}`)
      else await uploadDocuments([new File([new Uint8Array(r)], name, { type: 'application/pdf' })], projectId)
    } catch (e) {
      toast(`PDF export failed: ${(e as Error).message}`, 'error')
    }
  }

  const print = (): void => {
    setOpen(false)
    // Static markup keeps this free of KaTeX and the app stylesheet: maths prints as its source.
    const html = renderToStaticMarkup(<ReactMarkdown remarkPlugins={[remarkGfm]}>{normalizeMathBlocks(stripAiFences(content))}</ReactMarkdown>)
    printDoc(title || 'Untitled', `<h1>${title.replace(/&/g, '&amp;').replace(/</g, '&lt;')}</h1>${html}`)
  }

  return (
    <div className="newdoc" ref={root}>
      <button className="icon-btn" title="Export" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        {copied ? <Check size={14} /> : <Download size={14} />}
      </button>
      {open && (
        <div className="notes-menu right" role="menu">
          <button role="menuitem" className="notes-menu-row" onClick={() => { setOpen(false); downloadMarkdown(title, content) }}>Download Markdown</button>
          <button role="menuitem" className="notes-menu-row" onClick={() => {
            setOpen(false)
            void copyMarkdown(content).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1200) })
          }}>Copy Markdown</button>
          <button role="menuitem" className="notes-menu-row" onClick={() => void exportPdf('save')}>Export as PDF…</button>
          <button role="menuitem" className="notes-menu-row" onClick={() => void exportPdf('bytes')}>Export PDF to Uploads</button>
          <button role="menuitem" className="notes-menu-row" onClick={print}>Print…</button>
        </div>
      )}
    </div>
  )
}

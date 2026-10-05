import { useEffect, useState } from 'react'
import { setBase } from '../../lib/api'
import { pageSizeFor } from '../../lib/printDoc'
import MarkdownPreview from '../../components/MarkdownPreview'
import { stripAiFences } from './exportDoc'
import '../../styles/print.css'

const sleep = (ms: number): Promise<void> => new Promise((r) => setTimeout(r, ms))

/** Resolves once fonts are in and every image has loaded (or 5 s have passed), plus a beat for charts and maths layout. */
async function settle(): Promise<void> {
  const until = Date.now() + 5000
  await document.fonts.ready
  await sleep(200)
  while (Date.now() < until && [...document.images].some((i) => !i.complete)) await sleep(100)
  await sleep(300)
}

/** The hidden window main prints (`?surface=print`): one note on white paper, drawn by the app's own markdown stack. */
export default function PrintSurface(): JSX.Element | null {
  const [doc, setDoc] = useState<{ title: string; content: string } | null>(null)

  useEffect(() => {
    document.documentElement.dataset.theme = 'light'
    document.documentElement.classList.add('print-surface')
    const style = document.createElement('style')
    style.textContent = `@page { size: ${pageSizeFor(navigator.language)}; margin: 2.2cm; }`
    document.head.appendChild(style)
    void (async () => {
      const s = await window.os.backendStatus()
      if (s.url) setBase(s.url)
      setDoc(await window.os.print.payload())
    })()
  }, [])

  useEffect(() => {
    if (doc) void settle().then(() => window.os.print.ready())
  }, [doc])

  if (!doc) return null
  return (
    <article className="print-doc markdown">
      <h1>{doc.title || 'Untitled'}</h1>
      <MarkdownPreview source={stripAiFences(doc.content)} />
    </article>
  )
}

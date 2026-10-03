import { Bold, Code, Italic, Link, List, ListChecks, Quote } from 'lucide-react'
import type { MarkdownEditorHandle } from './handle'
import { toggleLinePrefix, wrapToggle, type Edit } from './format'
import { diffRange } from './textEdit'

/** Thin row of formatting buttons. Every edit goes through the editor handle, so ⌘Z undoes it. */
export default function FormatBar({ editor }: { editor: React.RefObject<MarkdownEditorHandle | null> }): JSX.Element {
  const run = (f: (text: string, s: number, e: number) => Edit): void => {
    const h = editor.current
    if (!h) return
    const text = h.getText()
    const { start, end } = h.getSelection()
    const next = f(text, start, end)
    const d = diffRange(text, next.text)
    h.replaceRange(d.start, d.end, d.text, next.start, next.end)
  }
  const wrap = (l: string, r = l) => () => run((t, s, e) => wrapToggle(t, s, e, l, r))
  const line = (p: string) => () => run((t, s, e) => toggleLinePrefix(t, s, e, p))
  const link = (): void => run((t, s, e) => {
    const sel = t.slice(s, e)
    return { text: `${t.slice(0, s)}[${sel}]()${t.slice(e)}`, start: s + sel.length + 3, end: s + sel.length + 3 }
  })
  const btn = (title: string, on: () => void, body: React.ReactNode): JSX.Element => (
    // mousedown is swallowed so the click does not pull focus (and the selection) out of the textarea
    <button type="button" className="fmt-btn" title={title} aria-label={title} onMouseDown={(e) => e.preventDefault()} onClick={on}>{body}</button>
  )
  return (
    <div className="fmt-bar" role="toolbar" aria-label="Formatting">
      {btn('Bold (⌘B)', wrap('**'), <Bold size={14} />)}
      {btn('Italic (⇧⌘I)', wrap('*'), <Italic size={14} />)}
      {btn('Code (⇧⌘E)', wrap('`'), <Code size={14} />)}
      {btn('Link (⌘K)', link, <Link size={14} />)}
      <span className="fmt-sep" />
      {btn('Heading 1', line('# '), 'H1')}
      {btn('Heading 2', line('## '), 'H2')}
      {btn('Heading 3', line('### '), 'H3')}
      <span className="fmt-sep" />
      {btn('Bulleted list', line('- '), <List size={14} />)}
      {btn('Task list', line('- [ ] '), <ListChecks size={14} />)}
      {btn('Quote', line('> '), <Quote size={14} />)}
    </div>
  )
}

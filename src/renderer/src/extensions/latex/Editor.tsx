import { useEffect, useRef } from 'react'
import { EditorState, Prec } from '@codemirror/state'
import { EditorView, drawSelection, highlightActiveLine, keymap, lineNumbers } from '@codemirror/view'
import { defaultKeymap, history, historyKeymap, indentWithTab } from '@codemirror/commands'
import { HighlightStyle, StreamLanguage, bracketMatching, syntaxHighlighting } from '@codemirror/language'
import { stex } from '@codemirror/legacy-modes/mode/stex'
import { highlightSelectionMatches, search, searchKeymap } from '@codemirror/search'
import { tags as t } from '@lezer/highlight'

// Colours come from the app's CSS variables, so the editor follows the light and dark themes with no palette of its own.
const theme = EditorView.theme({
  '&': { height: '100%', color: 'var(--text)', backgroundColor: 'var(--bg)', fontSize: '13px' },
  '&.cm-focused': { outline: 'none' },
  '.cm-scroller': { fontFamily: 'var(--mono)', lineHeight: '1.55' },
  '.cm-content': { caretColor: 'var(--text)' },
  '.cm-cursor': { borderLeftColor: 'var(--text)' },
  '.cm-gutters': { backgroundColor: 'var(--bg)', color: 'var(--text-faint)', border: 'none' },
  '.cm-activeLine, .cm-activeLineGutter': { backgroundColor: 'var(--hover)' },
  '&.cm-focused .cm-selectionBackground, .cm-selectionBackground, ::selection': { backgroundColor: 'var(--zone-fill)' },
  '.cm-matchingBracket': { backgroundColor: 'var(--active)', outline: '1px solid var(--border-strong)' },
  '.cm-selectionMatch': { backgroundColor: 'var(--hover)' },
  '.cm-panels': { backgroundColor: 'var(--bg-elev)', color: 'var(--text)', borderColor: 'var(--border)' },
  '.cm-textfield': { backgroundColor: 'var(--bg-input)', border: '1px solid var(--control-border)', color: 'var(--text)' },
  '.cm-button': { backgroundImage: 'none', backgroundColor: 'var(--bg-input)', border: '1px solid var(--control-border)', color: 'var(--text)' }
})

const highlight = HighlightStyle.define([
  { tag: t.comment, color: 'var(--text-faint)', fontStyle: 'italic' },
  { tag: [t.tagName, t.keyword], color: 'var(--tk-link)' },
  { tag: [t.atom, t.number], color: 'var(--tk-math)' },
  { tag: [t.string, t.bracket], color: 'var(--tk-code)' },
  { tag: t.invalid, color: 'var(--danger)' }
])

export interface EditorProps {
  /** The file's text when this editor mounts; the parent remounts (key) to switch files. */
  initial: string
  onChange: (text: string) => void
  onSave: () => void
  onCompile: () => void
  /** Move the cursor to a line (1-based); `n` makes a repeat jump to the same line count. */
  jump?: { line: number; n: number }
}

export default function Editor({ initial, onChange, onSave, onCompile, jump }: EditorProps): JSX.Element {
  const host = useRef<HTMLDivElement>(null)
  const view = useRef<EditorView | null>(null)
  const cb = useRef({ onChange, onSave, onCompile })
  cb.current = { onChange, onSave, onCompile }

  useEffect(() => {
    const v = new EditorView({
      parent: host.current!,
      state: EditorState.create({
        doc: initial,
        extensions: [
          lineNumbers(), history(), drawSelection(), highlightActiveLine(), bracketMatching(), highlightSelectionMatches(),
          EditorView.lineWrapping, StreamLanguage.define(stex), syntaxHighlighting(highlight), theme, search({ top: true }),
          Prec.highest(keymap.of([
            { key: 'Mod-s', run: () => { cb.current.onSave(); return true } },
            { key: 'Mod-Enter', run: () => { cb.current.onCompile(); return true } }
          ])),
          keymap.of([...defaultKeymap, ...historyKeymap, ...searchKeymap, indentWithTab]),
          EditorView.updateListener.of((u) => { if (u.docChanged) cb.current.onChange(u.state.doc.toString()) })
        ]
      })
    })
    view.current = v
    return () => v.destroy()
    // eslint-disable-next-line react-hooks/exhaustive-deps -- mounted once per file; the parent remounts with a key
  }, [])

  useEffect(() => {
    const v = view.current
    if (!v || !jump) return
    const at = v.state.doc.line(Math.min(Math.max(jump.line, 1), v.state.doc.lines)).from
    v.dispatch({ selection: { anchor: at }, scrollIntoView: true })
    v.focus()
  }, [jump?.n]) // eslint-disable-line react-hooks/exhaustive-deps

  return <div className="lx-editor" ref={host} />
}

/**
 * What a host (DocsView, the slash menu, dictation) may do to the editor from outside. Every method
 * edits through the textarea's own undo stack, so a programmatic insert is one ⌘Z away.
 */
export interface MarkdownEditorHandle {
  focus: () => void
  /** Insert at the caret (replacing a selection). `caretOffset` puts the caret that many characters into `text`. */
  insertAtCaret: (text: string, caretOffset?: number) => void
  replaceRange: (start: number, end: number, text: string) => void
  getSelection: () => { start: number; end: number; text: string }
  /** The textarea's current text, which is ahead of the `value` prop for a moment after a keystroke. */
  getText: () => string
  /** Move the caret to the start of a 1-based line and scroll it into view. */
  jumpToLine: (line: number) => void
}

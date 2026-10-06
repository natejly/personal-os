/**
 * What a host (DocsView, the slash menu, dictation) may do to the editor from outside. Every method
 * edits through the textarea's own undo stack, so a programmatic insert is one ⌘Z away.
 */
export interface MarkdownEditorHandle {
  focus: () => void
  /** Insert at the caret (replacing a selection). `caretOffset` puts the caret that many characters into `text`. */
  insertAtCaret: (text: string, caretOffset?: number) => void
  /**
   * Insert at the last known caret and NEVER move focus: for text the user did not just ask for
   * (dictation), so a click into another field is not undone by the next clip. Returns false when
   * the editor cannot take text (not mounted or read-only). Undo of this one insert may be lost
   * while the editor is not focused.
   */
  insertQuietly: (text: string) => boolean
  /** Optional selStart/selEnd leave the selection there instead of after the text. */
  replaceRange: (start: number, end: number, text: string, selStart?: number, selEnd?: number) => void
  getSelection: () => { start: number; end: number; text: string }
  /** The textarea's current text, which is ahead of the `value` prop for a moment after a keystroke. */
  getText: () => string
  /** Move the caret to the start of a 1-based line and scroll it into view. */
  jumpToLine: (line: number) => void
}

/**
 * A file dropped where nothing takes it must do nothing. Without this the browser's default is to
 * navigate the window to the file, which replaces the app with a PDF viewer and loses the session.
 *
 * Installed once per window at the root (main.tsx), so pop-outs are covered too. The listeners sit
 * at the bubble end: a real drop target (the composer, the Files page, a canvas widget) has already
 * called preventDefault by the time the event gets here, and is left alone.
 */

/** True for a file drag nobody has claimed; false for text, app payloads and handled drops. */
export function shouldSwallowFileDrag(types: Iterable<string> | null | undefined, defaultPrevented: boolean): boolean {
  if (defaultPrevented || !types) return false
  return Array.from(types).includes('Files')
}

export function installFileDropGuard(target: Window): () => void {
  const onDragOver = (e: DragEvent): void => {
    if (!shouldSwallowFileDrag(e.dataTransfer?.types, e.defaultPrevented)) return
    e.preventDefault()
    if (e.dataTransfer) e.dataTransfer.dropEffect = 'none'
  }
  const onDrop = (e: DragEvent): void => {
    if (!shouldSwallowFileDrag(e.dataTransfer?.types, e.defaultPrevented)) return
    e.preventDefault()
  }
  target.addEventListener('dragover', onDragOver)
  target.addEventListener('drop', onDrop)
  return () => {
    target.removeEventListener('dragover', onDragOver)
    target.removeEventListener('drop', onDrop)
  }
}

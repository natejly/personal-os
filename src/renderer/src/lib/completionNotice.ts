/**
 * The in-app "a chat finished while you were elsewhere" popup. It mirrors the OS notice's own test
 * (store.announce): show it only for a finished reply the user cannot already see, and keep the
 * OS notification as well. The snooze delay is fixed; the popup's own timer, not the toast stack's,
 * owns it so dismissing an unrelated toast never takes the snoozed popup with it.
 */

/** What the completion popup names: the chat that finished and the run that finished in it. */
export interface CompletionPopup {
  convId: string
  runId: string
  title: string
}

/** How long a snoozed completion popup stays hidden before it comes back. */
export const COMPLETION_SNOOZE_MS = 5 * 60 * 1000

/** Show the popup only for a finished reply the user is not looking at. Approval and failure already ring. */
export function shouldShowCompletion(kind: 'reply' | 'approval' | 'failed', visible: boolean, focused: boolean): boolean {
  return kind === 'reply' && !(visible && focused)
}

/**
 * One system notification. The tag collapses repeats the OS would otherwise stack, a click runs `onClick`.
 * Never throws: a notification is not worth a render crash, and a denied permission is a silent no-op.
 */
export function notify(title: string, body: string, opts: { tag?: string; onClick?: () => void } = {}): void {
  try {
    if (typeof Notification !== 'function' || Notification.permission === 'denied') return
    const n = new Notification(title, { body, tag: opts.tag })
    if (opts.onClick) {
      n.onclick = () => {
        try { window.focus() } catch { /* ignore */ }
        opts.onClick?.()
      }
    }
  } catch {
    // A notification is never worth a render crash.
  }
}

/** Fixed bodies: a notification carries no message text, so nothing private lands on a lock screen. */
export const CHAT_NOTICE_BODY = { reply: 'Reply ready', approval: 'Waiting for your approval', failed: 'Reply failed' } as const

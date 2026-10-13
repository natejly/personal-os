import { useEffect, useState } from 'react'
import type { Settings, ShortcutState } from '@shared/types'

export type ShortcutKey = 'gatherShortcut' | 'quickCaptureShortcut' | 'quickAskShortcut'
export const SHORTCUT_KEYS: readonly ShortcutKey[] = ['gatherShortcut', 'quickCaptureShortcut', 'quickAskShortcut']
const IPC = { gatherShortcut: ['gather', 'setGather'], quickCaptureShortcut: ['capture', 'setCapture'], quickAskShortcut: ['ask', 'setAsk'] } as const
const FAILED = { ask: 'quickAskShortcut', capture: 'quickCaptureShortcut' } as const

/**
 * The global accelerators are bound by the main process, not by /settings, so Save registers them before the
 * rest is written. `shortcuts` is each one's last registration result (the reason shows under the field).
 * A shortcut that another app owns fails at startup, long before the modal mounts, so the current state is
 * pulled as well as watched.
 */
export function useShortcuts(): {
  shortcuts: Partial<Record<ShortcutKey, ShortcutState>>
  /** Register what changed from `saved`. Returns the accelerators as bound, or null when one was rejected (it is never saved; the old one stays). */
  apply: (draft: Settings, saved: Settings) => Promise<Partial<Settings> | null>
} {
  const [shortcuts, setShortcuts] = useState<Partial<Record<ShortcutKey, ShortcutState>>>({})
  useEffect(() => {
    const put = (k: ShortcutKey, s: ShortcutState): void => setShortcuts((m) => ({ ...m, [k]: s }))
    for (const k of SHORTCUT_KEYS) void window.os.shortcuts[IPC[k][0]]().then((s) => put(k, s)).catch(() => undefined)
    return window.os.shortcuts.onFailure((s) => put(FAILED[s.which as keyof typeof FAILED] ?? 'gatherShortcut', s))
  }, [])

  const apply = async (draft: Settings, saved: Settings): Promise<Partial<Settings> | null> => {
    const bound: Partial<Settings> = {}
    for (const k of SHORTCUT_KEYS) {
      const want = (draft[k] ?? '').trim()
      if (want === (saved[k] ?? '').trim()) continue
      const r = await window.os.shortcuts[IPC[k][1]](want)
      setShortcuts((m) => ({ ...m, [k]: r }))
      if (!r.ok) return null
      bound[k] = r.accelerator
    }
    return bound
  }
  return { shortcuts, apply }
}

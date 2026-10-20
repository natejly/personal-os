import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import type { EffectiveConfig, EffectiveValue } from '@shared/types'
import { api } from '../../lib/api'
import { useStore } from '../../store'

interface Effective { loading: boolean; entry: (key: string) => EffectiveValue | undefined }

const NONE: Effective = { loading: false, entry: () => undefined }
const Ctx = createContext<Effective>(NONE)

/** Fetches the effective configuration once for the focused chat and project and shares it with every SettingRow below. */
export function EffectiveProvider({ children }: { children: ReactNode }): JSX.Element {
  const chatId = useStore((s) => s.focusedConversationId)
  const projectId = useStore((s) => s.conversations.find((c) => c.id === s.focusedConversationId)?.project_id ?? s.projectViewId)
  const [cfg, setCfg] = useState<EffectiveConfig | null>(null)
  const [loading, setLoading] = useState(true)
  useEffect(() => {
    let live = true
    api.config.effective(projectId, chatId).then((c) => { if (live) setCfg(c) }).catch(() => undefined).finally(() => { if (live) setLoading(false) })
    return () => { live = false }
    // Once per mount: a value edited in the open modal is not saved yet, so refetching would not change it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  const value = useMemo<Effective>(() => ({ loading, entry: (key) => cfg?.values[key] }), [cfg, loading])
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

/** The effective values, or entries that are all undefined when no provider is above. */
export const useEffective = (): Effective => useContext(Ctx)

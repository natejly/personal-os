import { useCallback, useEffect, useState } from 'react'
import type { RuntimeSelection, RuntimeView } from '@shared/types'
import { api } from '../../lib/api'
import { confirmDiff } from '../../lib/runtime'
import { useStore } from '../../store'

export type RuntimeScope = 'global' | 'project' | 'chat'
/** A selection the backend held back because it loosens something; confirming sends it again with `confirm`. */
export interface Asked { slot: string; value: RuntimeSelection; diff: string[] }

export const SCOPE_LABEL: Record<RuntimeScope, string> = { global: 'Everywhere', project: 'This project', chat: 'This chat' }

/** The runtime view of the focused chat and project, the scope being edited, and the select / "loosen" confirmation flow. */
export function useRuntimeSelect() {
  const conversationId = useStore((s) => s.focusedConversationId)
  const projectId = useStore((s) => (s.focusedConversationId ? s.sessions[s.focusedConversationId]?.conversation.project_id : null) ?? null)
  const [view, setView] = useState<RuntimeView | null>(null)
  const [scope, setScope] = useState<RuntimeScope>('global')
  const [asked, setAsked] = useState<Asked | null>(null)
  const [error, setError] = useState<string | null>(null)
  const load = useCallback((): void => {
    api.runtime.get(conversationId, projectId).then(setView).catch((e: Error) => setError(e.message))
  }, [conversationId, projectId])
  useEffect(load, [load])
  const scopes: RuntimeScope[] = ['global', ...(projectId ? ['project' as const] : []), ...(conversationId ? ['chat' as const] : [])]
  const at = scopes.includes(scope) ? scope : 'global'

  const select = (slot: string, value: RuntimeSelection, confirm = false): void => {
    setError(null)
    const scope_id = at === 'chat' ? conversationId : at === 'project' ? projectId : null
    api.runtime.select({ slot, scope: at, scope_id, value, confirm }).then(() => { setAsked(null); load() }).catch((e: unknown) => {
      const diff = confirmDiff(e)
      if (diff) setAsked({ slot, value, diff })
      else setError(e instanceof Error ? e.message : 'The selection failed.')
    })
  }
  const act = (p: Promise<unknown>): void => { setError(null); p.then(load).catch((e: Error) => setError(e.message)) }
  return { view, scopes, at, setScope, asked, setAsked, error, select, act }
}

export type RuntimeSelect = ReturnType<typeof useRuntimeSelect>

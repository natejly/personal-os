import { useEffect } from 'react'
import { create } from 'zustand'
import { api, fetchRaw, req } from './api'
import { useStore } from '../store'
import { fileViewer } from './showPanel'
import { rowAction, type ChatFile, type ChatFilesPage } from './chatFiles'

/** Network and store side of chat files; the grouping and click routing are pure, in chatFiles.ts. */
export const chatFilesApi = {
  forChat: (id: string, limit = 200) => req<ChatFilesPage>(`/conversations/${id}/files?limit=${limit}`),
  list: (scope: 'personal' | 'all', cursor?: string | null, limit = 50) =>
    req<ChatFilesPage>(`/chat-files?scope=${scope}&limit=${limit}${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ''}`),
  counts: () => req<{ counts: Record<string, number> }>('/chat-files/counts?scope=all')
}

/** Per-chat file counts for the sidebar badges, kept outside the main store: one fetch fills every row. */
const useCounts = create<{ counts: Record<string, number> }>(() => ({ counts: {} }))
export const useChatFileCount = (conversationId: string): number => useCounts((s) => s.counts[conversationId] ?? 0)

/**
 * Mount once, in the sidebar. The conversation list is replaced whenever a run finishes or a chat is
 * added or removed, so its identity is the refresh signal; a short wait folds a burst into one fetch.
 */
export function useChatFileCountsSync(): void {
  const conversations = useStore((s) => s.conversations)
  useEffect(() => {
    const t = setTimeout(() => {
      chatFilesApi.counts().then((r) => useCounts.setState({ counts: r.counts })).catch(() => undefined)
    }, 400)
    return () => clearTimeout(t)
  }, [conversations])
}

const TEXTISH = new Set(['markdown', 'text'])

/** Outputs sit in the app's own data folder, which the panel's file route refuses; small text is fetched and shown, the rest is saved. */
async function showOutput(f: ChatFile): Promise<void> {
  const rel = f.rel ?? `outputs/${f.name}`
  const kind = fileViewer({ name: f.name, path: rel, mime: '' })
  if (!TEXTISH.has(kind)) return api.conversations.downloadOutput(f.conversation_id, rel)
  const text = await (await fetchRaw(`/conversations/${f.conversation_id}/outputs/download?path=${encodeURIComponent(rel)}`)).text()
  const source = kind === 'markdown' ? text : `\`\`\`\n${text.replace(/```/g, "'''")}\n\`\`\``
  useStore.getState().openShow(f.conversation_id, { kind: 'markdown', title: f.name, source })
}

/**
 * Does what a click on a row means (see rowAction). `jump` opens the chat, which a side-panel file needs when the
 * click came from the sidebar (the canvas passes its own); null means the chat is already on screen.
 */
export async function openChatFile(f: ChatFile, jump: ((conversationId: string) => void) | null = (id) => void useStore.getState().selectChat(id)): Promise<void> {
  const s = useStore.getState()
  const action = rowAction(f)
  try {
    if (action !== 'open-doc') jump?.(f.conversation_id)
    switch (action) {
      case 'jump-to-chat': return
      case 'open-doc': return await s.openDoc(f.ref)
      case 'open-upload': {
        const d = await api.documents.get(f.ref)
        if (d.text) s.openShow(f.conversation_id, { kind: 'markdown', title: f.name, source: d.text })
        else s.openFiles('uploads')
        return
      }
      case 'open-output': return await showOutput(f)
      case 'open-local': return s.openShow(f.conversation_id, { kind: 'file', title: f.name, path: f.ref, name: f.name })
    }
  } catch (e) {
    s.toast((e as Error).message, 'error')
  }
}

/** Show in Finder: the file itself for a local path, the folder for an output or a coding session. */
export async function revealChatFile(f: ChatFile): Promise<void> {
  const toast = useStore.getState().toast
  try {
    const ok = f.kind === 'local'
      ? await window.os.data.fileAction(f.ref, 'reveal')
      : await window.os.data.reveal(f.kind === 'output' ? (await api.conversations.outputs(f.conversation_id)).folder : f.ref)
    if (!ok) toast('That is no longer there.', 'error')
  } catch (e) {
    toast((e as Error).message, 'error')
  }
}

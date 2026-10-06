import { useEffect } from 'react'
import { create } from 'zustand'
import { api, fetchRaw, req } from './api'
import { useStore } from '../store'
import { fileViewer } from './showPanel'
import { filesQuery, rowAction, type ChatFile, type ChatFilesPage, type FilesScope } from './chatFiles'

/** Network and store side of chat files; the grouping and click routing are pure, in chatFiles.ts. */
export const chatFilesApi = {
  forChat: (id: string, limit = 200) => req<ChatFilesPage>(`/conversations/${id}/files?limit=${limit}`),
  list: (scope: FilesScope, cursor?: string | null, limit = 50) => req<ChatFilesPage>(filesQuery(scope, cursor, limit)),
  counts: () => req<{ counts: Record<string, number>; projects?: Record<string, number> }>('/chat-files/counts?scope=all')
}

/** Per-chat and per-project file counts for the sidebar badges and the project tab, kept outside the main store: one fetch fills every row. */
const useCounts = create<{ counts: Record<string, number>; projects: Record<string, number> }>(() => ({ counts: {}, projects: {} }))
export const useChatFileCount = (conversationId: string): number => useCounts((s) => s.counts[conversationId] ?? 0)
export const useProjectFileCount = (projectId: string): number => useCounts((s) => s.projects[projectId] ?? 0)

/**
 * Mount once, in the sidebar. The conversation list is replaced whenever a run finishes or a chat is
 * added or removed, so its identity is the refresh signal; a short wait folds a burst into one fetch.
 */
export function useChatFileCountsSync(): void {
  const conversations = useStore((s) => s.conversations)
  useEffect(() => {
    const t = setTimeout(() => {
      chatFilesApi.counts().then((r) => useCounts.setState({ counts: r.counts, projects: r.projects ?? {} })).catch(() => undefined)
    }, 400)
    return () => clearTimeout(t)
  }, [conversations])
}

const TEXTISH = new Set(['markdown', 'text'])
const SHOW_MAX_BYTES = 200_000

/** Outputs sit in the app's own data folder, which the panel's file route refuses; small text is fetched and shown, the rest is saved. */
async function showOutput(f: ChatFile, chat: string): Promise<void> {
  const rel = f.rel ?? `outputs/${f.name}`
  const kind = fileViewer({ name: f.name, path: rel, mime: '' })
  if (!TEXTISH.has(kind)) return api.conversations.downloadOutput(chat, rel)
  const res = await fetchRaw(`/conversations/${chat}/outputs/download?path=${encodeURIComponent(rel)}`)
  if (Number(res.headers.get('content-length') || 0) > SHOW_MAX_BYTES) {  // a multi-MB CSV would stall the panel
    void res.body?.cancel()
    return api.conversations.downloadOutput(chat, rel)
  }
  const text = await res.text()
  const source = kind === 'markdown' ? text : `\`\`\`\n${text.replace(/```/g, "'''")}\n\`\`\``
  useStore.getState().openShow(chat, { kind: 'markdown', title: f.name, source })
}

/**
 * Does what a click on a row means (see rowAction). `jump` opens the chat, which a side-panel file needs when the
 * click came from the sidebar (the canvas passes its own); null means the chat is already on screen.
 * A project file no chat touched has no chat to open: a note opens in the editor, an upload in the project's Files tab.
 */
export async function openChatFile(f: ChatFile, jump: ((conversationId: string) => void) | null = (id) => void useStore.getState().selectChat(id)): Promise<void> {
  const s = useStore.getState()
  const action = rowAction(f)
  try {
    const chat = f.conversation_id
    if (action !== 'open-doc' && chat) jump?.(chat)
    switch (action) {
      case 'jump-to-chat': return
      case 'open-doc': return await s.openDoc(f.ref)
      case 'open-upload': {
        if (!chat) return f.project_id ? s.openProject(f.project_id, 'files') : undefined
        const d = await api.documents.get(f.ref)
        if (d.text) s.openShow(chat, { kind: 'markdown', title: f.name, source: d.text })
        else s.openFiles('uploads')
        return
      }
      case 'open-output': return chat ? await showOutput(f, chat) : undefined
      case 'open-local': return chat ? s.openShow(chat, { kind: 'file', title: f.name, path: f.ref, name: f.name }) : undefined
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
      : await window.os.data.reveal(f.kind === 'output' ? (await api.conversations.outputs(f.conversation_id!)).folder : f.ref)
    if (!ok) toast('That is no longer there.', 'error')
  } catch (e) {
    toast((e as Error).message, 'error')
  }
}

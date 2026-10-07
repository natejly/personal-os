import { useEffect } from 'react'
import { create } from 'zustand'
import { api, req } from './api'
import { useStore } from '../store'
import { uploadShowItem } from './showPanel'
import { artifactShowItem, artifactsQuery, filesQuery, rowAction, uploadTarget, type ChatFile, type ChatFilesPage, type FilesScope } from './chatFiles'

/** Network and store side of chat files; the grouping and click routing are pure, in chatFiles.ts. */
export const chatFilesApi = {
  forChat: (id: string, limit = 200) => req<ChatFilesPage>(`/conversations/${id}/files?limit=${limit}`),
  list: (scope: FilesScope, cursor?: string | null, limit = 50) => req<ChatFilesPage>(filesQuery(scope, cursor, limit)),
  counts: () => req<{ counts: Record<string, number>; projects?: Record<string, number> }>('/chat-files/counts?scope=all'),
  artifacts: (cursor?: string | null, limit = 50) => req<ChatFilesPage>(artifactsQuery(cursor, limit)),
  /** Files → Artifacts: open in the default app / reveal in Finder, by the index row (the backend owns the path). */
  openArtifact: (id: string) => req(`/chat-files/${id}/open`, { method: 'POST' }),
  revealArtifact: (id: string) => req(`/chat-files/${id}/reveal`, { method: 'POST' })
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

/** An upload opens in the chat's side panel when there is a chat, else in the standalone viewer. */
export async function openUpload(id: string, chat: string | null | undefined): Promise<void> {
  const s = useStore.getState()
  if (uploadTarget(chat) === 'viewer') return s.openUploadPreview(id)
  s.openShow(chat!, uploadShowItem(await api.documents.get(id)))
}

/**
 * Does what a click on a row means (see rowAction). `jump` opens the chat, which a side-panel file needs when the
 * click came from the sidebar (the canvas passes its own); null means the chat is already on screen.
 * A project file no chat touched has no chat to open: a note opens in the editor, an upload in the standalone viewer.
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
      case 'open-upload': return await openUpload(f.ref, chat)
      case 'open-output': return chat ? s.openShow(chat, artifactShowItem(f)) : undefined
      case 'open-local': return chat ? s.openShow(chat, { kind: 'file', title: f.name, path: f.ref, name: f.name }) : undefined
    }
  } catch (e) {
    s.toast((e as Error).message, 'error')
  }
}

/** Show in Finder: the file itself for a local path or an output, the folder for a coding session. */
export async function revealChatFile(f: ChatFile): Promise<void> {
  const toast = useStore.getState().toast
  try {
    const ok = f.kind === 'local'
      ? await window.os.data.fileAction(f.ref, 'reveal')
      : f.kind === 'output' ? (await chatFilesApi.revealArtifact(f.id), true)
      : await window.os.data.reveal(f.ref)
    if (!ok) toast('That is no longer there.', 'error')
  } catch (e) {
    toast((e as Error).message, 'error')
  }
}

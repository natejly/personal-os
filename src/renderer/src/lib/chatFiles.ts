/**
 * Files a chat touched: what was attached, the notes it made or edited, what its tools saved, local
 * files it read or wrote, coding sessions. Pure, so node:test pins the grouping and the click routing;
 * the fetches and the store calls live in useChatFiles.ts.
 */

export type ChatFileKind = 'upload' | 'note' | 'output' | 'local' | 'coding'
export type ChatFileAction = 'attached' | 'created' | 'edited' | 'saved' | 'uploaded'

export interface ChatFile {
  id: string
  /** Null for a project file no chat has touched; otherwise the newest chat that did. */
  conversation_id: string | null
  conversation_title: string | null
  /** Distinct chats that touched it (project scope; 0 when none). */
  chat_count: number
  /** Uploads: included in every chat of the project. */
  pinned: boolean
  project_id: string | null
  kind: ChatFileKind
  /** upload: documents id; note: doc id; output, local and coding: absolute path (coding: the session's worktree folder). */
  ref: string
  name: string
  action: ChatFileAction
  message_id: string | null
  /** Unix seconds. */
  created_at: number
  /** The path is no longer on disk. */
  missing: boolean
  /** Outputs only: the path inside the chat's outbox, e.g. "outputs/report.csv". */
  rel: string | null
}

/** The personal (no-project) chats, or one project's files: its chats', its notes and its uploads. */
export type FilesScope = 'personal' | { projectId: string }

export const filesQuery = (scope: FilesScope, cursor?: string | null, limit = 50): string =>
  `/chat-files?${scope === 'personal' ? 'scope=personal' : `scope=project&project_id=${encodeURIComponent(scope.projectId)}`}&limit=${limit}${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ''}`

/** Case-insensitive match on a file's name or the title of the chat it came from. */
export function projectFilter(files: ChatFile[], q: string): ChatFile[] {
  const n = q.trim().toLowerCase()
  return n ? files.filter((f) => f.name.toLowerCase().includes(n) || (f.conversation_title ?? '').toLowerCase().includes(n)) : files
}

export interface ChatFilesPage { files: ChatFile[]; next_cursor: string | null }

const KIND_ORDER: ChatFileKind[] = ['upload', 'note', 'output', 'local', 'coding']
const KIND_LABEL: Record<ChatFileKind, string> = { upload: 'Uploads', note: 'Notes', output: 'Outputs', local: 'Local files', coding: 'Coding' }
const ACTION_LABEL: Record<ChatFileAction, string> = { attached: 'Attached', created: 'Created', edited: 'Edited', saved: 'Saved', uploaded: 'Uploaded' }

export const kindLabel = (k: ChatFileKind): string => KIND_LABEL[k] ?? k
export const actionLabel = (a: ChatFileAction): string => ACTION_LABEL[a] ?? a

/** The groups Uploads, Notes, Outputs, Local files, Coding, in that order; a kind with no files is left out. */
export function groupByKind(files: ChatFile[]): { kind: ChatFileKind; label: string; files: ChatFile[] }[] {
  return KIND_ORDER.map((kind) => ({ kind, label: KIND_LABEL[kind], files: files.filter((f) => f.kind === kind) })).filter((g) => g.files.length > 0)
}

/** One group per chat, the chat with the newest file first; files keep the order they came in. Files no chat touched share one group with the id ''. */
export function groupByChat(files: ChatFile[]): { conversationId: string; title: string; files: ChatFile[] }[] {
  const by = new Map<string, { conversationId: string; title: string; files: ChatFile[] }>()
  for (const f of files) {
    const id = f.conversation_id ?? ''
    const g = by.get(id)
    if (g) g.files.push(f)
    else by.set(id, { conversationId: id, title: id ? f.conversation_title || 'Untitled chat' : 'Not from a chat', files: [f] })
  }
  const newest = (g: { files: ChatFile[] }): number => Math.max(...g.files.map((f) => f.created_at))
  return [...by.values()].sort((a, b) => newest(b) - newest(a))
}

/** The sidebar badge: nothing for zero, the number up to 99, then "99+". */
export const formatCount = (n: number): string => (n <= 0 ? '' : n > 99 ? '99+' : String(n))

export type ChatFileClick = 'open-doc' | 'open-upload' | 'open-output' | 'open-local' | 'jump-to-chat'

/** What a click on a row does. A coding session is a folder of changes, and a file that is gone has nothing to open: both go to the chat. */
export function rowAction(f: Pick<ChatFile, 'kind' | 'missing'>): ChatFileClick {
  if (f.kind === 'coding' || f.missing) return 'jump-to-chat'
  return { upload: 'open-upload', note: 'open-doc', output: 'open-output', local: 'open-local' }[f.kind] as ChatFileClick
}

/** Where an upload opens: in the chat's side panel when a chat is on hand, else the standalone viewer. */
export const uploadTarget = (chatId: string | null | undefined): 'panel' | 'viewer' => (chatId ? 'panel' : 'viewer')

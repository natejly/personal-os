import { FolderOpen, X } from 'lucide-react'
import { useStore } from '../store'

/**
 * The chat's working folder, under the composer beside Plan mode. Picking one writes
 * `conv.settings.workingFolder`; the backend grants that folder to the shell, file and coding-agent
 * tools for this chat's runs, first in the root list, so "fix the tests" needs no path. With no
 * conversation yet the store parks the value and `send` applies it to the chat it creates.
 */
export default function WorkingFolder({ conversationId }: { conversationId?: string }): JSX.Element | null {
  const convId = useStore((s) => conversationId ?? s.focusedConversationId)
  const folder = useStore((s) => {
    const id = conversationId ?? s.focusedConversationId
    return id ? s.sessions[id]?.conversation.settings.workingFolder : s.draftChatSettings.workingFolder
  })
  const isDesk = useStore((s) => {
    const id = conversationId ?? s.focusedConversationId
    return !!(id && s.sessions[id]?.conversation.settings.deskId)
  })
  const setChatSettings = useStore((s) => s.setChatSettings)
  if (isDesk) return null // a desk already has its workspace

  const pick = async (): Promise<void> => {
    const chosen = await window.os.data.chooseFolder()
    if (chosen) await setChatSettings({ workingFolder: chosen }, convId ?? undefined)
  }
  const name = folder ? folder.replace(/\/+$/, '').split('/').pop() || folder : ''
  return (
    <span className={`composer-ctl working-folder ${folder ? 'on' : ''}`}>
      <button className="working-folder-pick" title={folder ? `Working in ${folder}. Click to change.` : 'Work in a folder: the assistant may read, edit and run commands there'}
        onClick={() => void pick()}>
        <FolderOpen size={13} /> {folder ? name : 'Folder'}
      </button>
      {folder && (
        <button className="working-folder-clear" title="Stop working in this folder" aria-label="Stop working in this folder"
          onClick={() => void setChatSettings({ workingFolder: '' }, convId ?? undefined)}><X size={11} /></button>
      )}
    </span>
  )
}

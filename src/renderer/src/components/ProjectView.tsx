import { useEffect, useMemo, useState } from 'react'
import { MessageSquarePlus, Pencil, Files, FolderX, Brain, MessageSquare, BookOpen, Trash2 } from 'lucide-react'
import { useStore, useProject, type ProjectTab } from '../store'
import { dragProps } from '../canvas/dnd'
import { rowButton } from '../lib/rowButton'
import SidebarToggle from './SidebarToggle'
import ChatPulse from './ChatPulse'
import MemoryPanel from './MemoryPanel'
import ProjectArtifacts from './ProjectArtifacts'
import SendToSpace from './SendToSpace'
import { oneLine } from '../lib/emailAsk'
import { useProjectFileCount } from '../lib/useChatFiles'
import { fenced, lines, usePageContext } from '../lib/pageContext'
import AppSwitcher from './AppSwitcher'

export default function ProjectView(): JSX.Element {
  const id = useStore((s) => s.projectViewId)!
  const project = useProject(id)
  const conversations = useStore((s) => s.conversations)
  const { newChat, selectChat, deleteChat, setProjectModal, updateProject, loadScope, setView } = useStore()
  const tab = useStore((s) => s.projectTab)
  const setTab = (t: ProjectTab): void => useStore.setState({ projectTab: t })
  const fileCount = useProjectFileCount(id)
  const [prompt, setPrompt] = useState(project?.system_prompt ?? '')
  const rows = useMemo(() => conversations.filter((c) => c.project_id === id), [conversations, id])

  useEffect(() => { setPrompt(project?.system_prompt ?? '') }, [project?.id, project?.system_prompt])
  useEffect(() => { void loadScope(id) }, [id, loadScope])

  usePageContext(() => (project
    ? {
        view: 'project',
        label: `Project “${oneLine(project.name, 80)}”`,
        detail: [
          `Project \`${project.id}\`${project.description ? ` — ${oneLine(project.description, 300)}` : ''}.`,
          project.system_prompt ? `Its instructions:\n${fenced(project.system_prompt, 2000)}` : '',
          `Chats in it:\n${lines(conversations.filter((c) => c.project_id === project.id), (c) => `${c.title} (\`${c.id}\`)`, 20)}`
        ].filter(Boolean).join('\n\n'),
        refs: [{ kind: 'project', id: project.id, name: project.name }],
        hints: ['Where did this project get to?', 'Sharpen the project instructions']
      }
    : null), [project, conversations])

  // Keeps the title bar: without it the window had no drag region and no way back to the sidebar.
  if (!project) {
    return (
      <main className="page project-page">
        <header className="page-header drag">
          <SidebarToggle />
          <h2>Project</h2>
          <AppSwitcher />
        </header>
        <div className="empty-state">
          <FolderX size={28} />
          <h2>Project not found</h2>
          <p>It may have been deleted, or moved to the trash.</p>
          <button className="primary-btn" onClick={() => setView('home')}>Back to Today</button>
        </div>
      </main>
    )
  }
  const st = project.stats

  const TABS: { key: ProjectTab; label: string; icon: JSX.Element; n?: number }[] = [
    { key: 'chats', label: 'Chats', icon: <MessageSquare size={14} />, n: rows.length },
    { key: 'artifacts', label: 'Artifacts', icon: <Files size={14} />, n: fileCount },
    { key: 'instructions', label: 'Instructions', icon: <BookOpen size={14} /> },
    { key: 'memory', label: 'Memory', icon: <Brain size={14} />, n: (st?.memories ?? 0) + (st?.nodes ?? 0) }
  ]

  return (
    <main className="page project-page">
      <header className="page-header drag">
        <SidebarToggle />
        <h2><span className="project-dot" style={{ background: project.color }} />{project.name}</h2>
        <div className="no-drag header-right">
          <SendToSpace items={[{ kind: 'project', refId: project.id }]} />
          <button className="ghost-btn" onClick={() => setProjectModal({ mode: 'edit', project })}><Pencil size={13} /> Edit</button>
          <button className="primary-btn" onClick={() => newChat(id)}><MessageSquarePlus size={14} /> New chat</button>
        </div>
        <AppSwitcher />
      </header>

      <div className="project-hero">
        {project.description ? <p>{project.description}</p> : <p className="muted">No description.</p>}
        <div className="tabs">
          {TABS.map((t) => (
            <button key={t.key} className={tab === t.key ? 'active' : ''} onClick={() => setTab(t.key)}>
              {t.icon}{t.label}{t.n !== undefined && <span className="count">{t.n}</span>}
            </button>
          ))}
        </div>
      </div>

      {tab === 'chats' && (
        <div className="page-body">
          {rows.length === 0 && (
            <div className="empty-state">
              <MessageSquare size={28} />
              <h2>No chats yet</h2>
              <p>A chat started here follows this project&apos;s instructions and can use its knowledge and memory.</p>
              <button className="primary-btn" onClick={() => newChat(id)}><MessageSquarePlus size={14} /> New chat</button>
            </div>
          )}
          <div className="chat-rows">
            {rows.map((c) => (
              <div key={c.id} className="chat-row" {...rowButton(() => void selectChat(c.id))}
                {...dragProps({ kind: 'conversation', id: c.id, label: c.title, projectId: id })}>
                <MessageSquare size={14} />
                <span className="chat-row-title"><ChatPulse conversationId={c.id} />{c.title}</span>
                <span className="muted small">{c.model} · {new Date(c.updated_at * 1000).toLocaleDateString()}</span>
                <button className="icon-btn ghost danger" aria-label={`Delete chat: ${c.title}`} title="Delete" onClick={(e) => { e.stopPropagation(); void deleteChat(c.id) }}><Trash2 size={13} /></button>
              </div>
            ))}
          </div>
        </div>
      )}
      {tab === 'instructions' && (
        <div className="page-body">
          <p className="muted small">Added to the system prompt for every chat in this project, after your global system prompt.</p>
          <textarea className="instructions" rows={12} value={prompt}
            onChange={(e) => setPrompt(e.target.value)} onBlur={() => prompt !== project.system_prompt && void updateProject(id, { system_prompt: prompt })} />
          <p className="muted small">Saved when you click away.</p>
        </div>
      )}
      {tab === 'artifacts' && <ProjectArtifacts key={id} projectId={id} />}
      {tab === 'memory' && <MemoryPanel projectId={id} />}
    </main>
  )
}

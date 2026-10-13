import { useEffect, useMemo, useState } from 'react'
import { useShallow } from 'zustand/react/shallow'
import { MessageSquarePlus, Pencil, Files, FolderX, Brain, MessageSquare, BookOpen, Trash2, Info, ChevronRight } from '../icons'
import { useStore, useProject } from '../store'
import { dragProps } from '../canvas/dnd'
import { rowButton } from '../lib/rowButton'
import SidebarToggle from './SidebarToggle'
import ChatPulse from './ChatPulse'
import MemoryPanel from './MemoryPanel'
import ProjectContext from './ProjectContext'
import { oneLine } from '../lib/emailAsk'
import { useProjectFileCount } from '../lib/useChatFiles'
import { PROJECT_SECTION_LABEL as L } from '../lib/projectTabs'
import { fenced, lines, usePageContext } from '../lib/pageContext'
import { ago } from './dataFormat'
import { modelLabel } from '../lib/modelLabel'
import AppSwitcher from './AppSwitcher'

export default function ProjectView(): JSX.Element {
  const id = useStore((s) => s.projectViewId)!
  const project = useProject(id)
  const conversations = useStore((s) => s.conversations)
  const { newChat, selectChat, deleteChat, setProjectModal, updateProject, loadScope, setView } = useStore(useShallow((s) => ({ newChat: s.newChat, selectChat: s.selectChat, deleteChat: s.deleteChat, setProjectModal: s.setProjectModal, updateProject: s.updateProject, loadScope: s.loadScope, setView: s.setView })))
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
          <button className="primary-btn" onClick={() => setView('chat')}>Back to chats</button>
        </div>
      </main>
    )
  }
  const st = project.stats

  const head = (icon: JSX.Element, label: string, n?: number): JSX.Element => (
    <h3 className="project-sec-head">{icon}{label}{n !== undefined && <span className="count">{n}</span>}</h3>
  )

  /** Collapsed-by-default section: chevron, icon, label, then a muted one-line hint. */
  const fold = (icon: JSX.Element, label: string, hint: string): JSX.Element => (
    <summary><ChevronRight size={12} />{icon}<h3 className="project-sec-head">{label}</h3><span className="muted small project-hint">{hint}</span></summary>
  )

  return (
    <main className="page project-page">
      <header className="page-header drag">
        <SidebarToggle />
        <h2><span className="project-dot" style={{ background: project.color }} />{project.name}</h2>
        <div className="no-drag header-right">
          <button className="ghost-btn" onClick={() => setProjectModal({ mode: 'edit', project })}><Pencil size={13} /> Edit</button>
          <button className="primary-btn" onClick={() => newChat(id)}><MessageSquarePlus size={14} /> New chat</button>
        </div>
        <AppSwitcher />
      </header>

      <div className="page-body wide project-body">
        <div className="project-cols">
          <section className="project-sec project-col" aria-label={L.chats}>
            {head(<MessageSquare size={14} />, L.chats, rows.length)}
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
                  <span className="chat-row-title"><ChatPulse conv={c} />{c.title}</span>
                  <span className="chat-row-meta muted small"><span className="chat-row-model">{modelLabel(c.model)}</span><span>{ago(c.updated_at)}</span></span>
                  <button className="icon-btn ghost danger" aria-label={`Delete chat: ${c.title}`} title="Delete" onClick={(e) => { e.stopPropagation(); void deleteChat(c.id) }}><Trash2 size={13} /></button>
                </div>
              ))}
            </div>
          </section>
          <section className="project-sec project-col" aria-label={L.files}>
            {head(<Files size={14} />, L.files, fileCount)}
            <ProjectContext key={id} projectId={id} />
          </section>
        </div>
        <details className="project-sec project-fold project-tools" aria-label={L.context}>
          {fold(<Info size={14} />, L.context, project.description ? oneLine(project.description, 90) : 'No description')}
          {project.description ? <p className="project-desc">{project.description}</p> : null}
        </details>
        <details className="project-sec project-fold project-tools" aria-label={L.instructions}>
          {fold(<BookOpen size={14} />, L.instructions, prompt.trim() ? oneLine(prompt, 90) : 'None')}
          <textarea className="instructions" rows={4} value={prompt}
            onChange={(e) => setPrompt(e.target.value)} onBlur={() => prompt !== project.system_prompt && void updateProject(id, { system_prompt: prompt })} />
          <p className="muted small">Added to every chat&apos;s system prompt here, after your global one. Saved when you click away.</p>
        </details>
        <details className="project-sec project-fold project-tools" aria-label={L.memory}>
          {fold(<Brain size={14} />, L.memory, String((st?.memories ?? 0) + (st?.nodes ?? 0)))}
          <MemoryPanel projectId={id} />
        </details>
      </div>
    </main>
  )
}

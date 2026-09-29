import { useEffect, useMemo, useState } from 'react'
import { BookOpen, FileText, FolderKanban, MessageSquare } from 'lucide-react'
import { useProject, useStore } from '../../store'
import ChatPulse from '../../components/ChatPulse'
import { useCanvas } from '../store'
import { dragProps } from '../dnd'
import type { WidgetDef, WidgetProps } from '../registry'

type Tab = 'chats' | 'instructions' | 'files'

const TABS: { key: Tab; icon: JSX.Element }[] = [
  { key: 'chats', icon: <MessageSquare size={11} /> },
  { key: 'instructions', icon: <BookOpen size={11} /> },
  { key: 'files', icon: <FileText size={11} /> }
]
const readTab = (v: unknown): Tab => (TABS.some((t) => t.key === v) ? (v as Tab) : 'chats')
const kb = (n: number): string => (n < 1024 ? `${n} B` : n < 1_048_576 ? `${Math.round(n / 1024)} KB` : `${(n / 1_048_576).toFixed(1)} MB`)

const ProjectWidget = ({ window: win, live, onConfig, onTitle }: WidgetProps): JSX.Element => {
  const id = win.ref_id ?? ''
  const project = useProject(id)
  const conversations = useStore((s) => s.conversations)
  const documents = useStore((s) => s.documents)
  const loadScope = useStore((s) => s.loadScope)
  const updateProject = useStore((s) => s.updateProject)
  const openWindow = useCanvas((s) => s.openWindow)
  const tab = readTab(win.config.tab)
  const [prompt, setPrompt] = useState(project?.system_prompt ?? '')

  useEffect(() => { setPrompt(project?.system_prompt ?? '') }, [project?.id, project?.system_prompt])
  // The only fetch this widget makes, and `live` gates it: a window nobody can see loads no scope.
  useEffect(() => { if (live && id) void loadScope(id) }, [live, id, loadScope])
  useEffect(() => { if (project && !win.title) onTitle(project.name) }, [project, win.title, onTitle])

  const chats = useMemo(() => conversations.filter((c) => c.project_id === id), [conversations, id])
  const files = useMemo(() => documents.filter((d) => d.project_id === id), [documents, id])

  // Off-screen or zoomed out: the chat list unmounts, and with it one ChatPulse subscription per row.
  if (!live) {
    return (
      <div className="proxy-card">
        <FolderKanban size={18} />
        <strong>{project?.name || win.title || 'Project'}</strong>
        <span>{chats.length} chats · {files.length} files · paused</span>
      </div>
    )
  }

  if (!project) return <div className="widget-empty"><span>Project not found.</span></div>
  const st = project.stats

  return (
    <div className="widget">
      <div className="widget-bar">
        <span className="project-dot" style={{ background: project.color }} />
        {TABS.map((t) => (
          <button key={t.key} className={`widget-chip ${t.key === tab ? 'on' : ''}`} onClick={() => onConfig({ tab: t.key })} title={t.key}>
            {t.icon}{t.key === 'chats' ? chats.length : t.key === 'files' ? files.length : ''}
          </button>
        ))}
        <span className="spacer" />
        <span className="widget-meta" title="memories · graph nodes">{st?.memories ?? 0}m · {st?.nodes ?? 0}n</span>
      </div>

      {tab === 'chats' && (
        <div className="widget-scroll">
          {chats.length === 0 ? (
            <div className="widget-empty"><span>No chats in this project. Drag one here from the sidebar to open it on the canvas.</span></div>
          ) : (
            <div className="widget-list">
              {chats.map((c) => (
                <div key={c.id} className="widget-row" role="button" tabIndex={0}
                  onClick={() => void openWindow('chat', c.id)}
                  {...dragProps({ kind: 'conversation', id: c.id, label: c.title, projectId: id })}>
                  <ChatPulse conversationId={c.id} />
                  <span className="grow widget-title">{c.title}</span>
                  <span className="widget-meta">{new Date(c.updated_at * 1000).toLocaleDateString()}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {tab === 'instructions' && (
        <div className="widget-scroll">
          <textarea className="instructions" rows={10} value={prompt} placeholder="Added to the system prompt for every chat in this project."
            onChange={(e) => setPrompt(e.target.value)}
            onBlur={() => prompt !== project.system_prompt && void updateProject(id, { system_prompt: prompt })} />
          <p className="widget-meta">Saved when you click away.</p>
        </div>
      )}

      {tab === 'files' && (
        <div className="widget-scroll">
          {files.length === 0 ? (
            <div className="widget-empty"><span>No documents. Drop files on a documents window to add some.</span></div>
          ) : (
            <div className="widget-list">
              {files.map((d) => (
                <div key={d.id} className="widget-row" {...dragProps({ kind: 'document', id: d.id, label: d.name, projectId: id })}>
                  <FileText size={12} />
                  <span className="grow widget-title" title={d.name}>{d.name}</span>
                  <span className="widget-meta">{kb(d.size)} · {d.chunk_count}c</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export const def: WidgetDef = {
  kind: 'project',
  label: 'Project',
  icon: <FolderKanban size={18} />,
  defaultSize: { w: 360, h: 420 },
  minSize: { w: 280, h: 240 },
  chrome: 'full',
  needsRef: true,
  defaultConfig: { tab: 'chats' },
  Component: ProjectWidget
}

export default ProjectWidget

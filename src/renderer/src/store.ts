import { create } from 'zustand'
import { messageCharLimit, tooLongNotice } from './lib/messageLimit'
import type { ApprovalDecision, BackendInfo, BackendState, PlanEdit, PlanDecision, PlanRecord,
  Desk, DeskEvent, DeskFile, FullDesk, PromotionResult, ActivityConfig, ActivityContextFile, ActivityEvent, ActivityInsights, ActivitySignal, ActivityStatus, ActivitySummary, InsightStatus, AgentInbox, ChatEvent, ChatRunStarted, Conversation, ConversationSettings, Doc, DocFolder, DocRevision, Document, Effort, TrashKind, FullDoc, GraphData, Memory, Message, ModelInfo, PageContext, PlanStep, Settings, Project, RunConflict, SessionStatus, Skill, StyleProfile, StyleSample, StyleState, ToolInfo, Todo, GoogleStatus, TasksSyncStatus, TodoCalendarStatus, TodayDashboard, Recap, Job, Meeting, MeetingCandidate, MeetingCapability, MeetingConfig, MeetingPreflight, MeetingSegment, MeetingStatus, MeetingStatusInfo, MeetingStreamEvent, FullMeeting } from '@shared/types'
import { daily as dailyNote } from './features/notes/api'
import { ApiError } from './lib/apiError'
import { installRejectionToasts } from './lib/rejections'
import { api, backgroundStream, chatStream, getBase, getToken, meetingStream, setBase, type Scope } from './lib/api'
import { currentSelection } from './lib/pageContext'
import { DEFAULT_EFFORT, NEEDS_YOU } from '../../shared/types'
import { chatNotice, finishStatus, foldRunState, mergeConversation, onScreen, pickEvictions, pulseStatus, reduceStatus, replayCursor, settleApprovals, type LiveRuns } from './sessionStatus'
import { adjacentChatId } from './lib/chatRows'
import { createDeltaBuffer } from './lib/deltaBuffer'
import { CHAT_NOTICE_BODY, notify } from './lib/notify'
import { applyCursor, fetchSegmentPages, needsSegmentReload } from './lib/transcript'
import { viewHidden } from './moduleToggles'
import { chainTo, folderKey, groupShutKey } from './lib/docTree'
import { clearViews } from './lib/viewCache'
import { emailAsk } from './lib/emailAsk'
import type { UploadResult } from '@shared/types'
import { uploadToast, type UploadOutcome } from './lib/uploadNote'

/**
 * Settings as the renderer holds them: without the legacy `mode`, which only init() reads. Kept out
 * of the store so no Settings draft (the first-run modal, ⌘,) can PUT a stale `mode: 'canvas'` back
 * over the one-shot migration.
 */
const withoutLegacyMode = (s: Settings): Settings => {
  const out = { ...s }
  delete out.mode
  return out
}

/** `'canvas'` is the spaces desktop: one destination among the views, not a separate shell. */
export type View = 'home' | 'chat' | 'todos' | 'health' | 'calendar' | 'mail' | 'boards' | 'dashboards' | 'docs' | 'meetings' | 'activity' | 'library' | 'cowork' | 'project' | 'canvas'
/** Which shelf of the Library is showing. Kept in the store so leaving and coming back lands you where you were. */
export type LibraryTab = 'skills' | 'workflows' | 'connectors' | 'made' | 'artifacts' | 'agents' | 'commands'
/** Every view but the canvas: what ⌘⇧C and the sidebar's LayoutGrid button return to. */
export type ClassicView = Exclude<View, 'canvas'>
/** How the Docs editor splits its panes. */
export type DocMode = 'edit' | 'split' | 'preview'
const DOC_MODE_KEY = 'grain.docMode'
export const readDocMode = (): DocMode => {
  try {
    const v = localStorage.getItem(DOC_MODE_KEY)
    return v === 'edit' || v === 'split' || v === 'preview' ? v : 'split'
  } catch { return 'split' }
}
/** How the Memory panel lays out its halves: the memory list, the knowledge graph, the voice profile. */
export type MemoryMode = 'split' | 'list' | 'graph' | 'style'
export type ContextTab = 'last' | 'preview' | 'trace'
/** Settings sections. 'knowledge' holds what used to be the sidebar's Knowledge Base: memory and documents. */
export type SettingsTab = 'provider' | 'knowledge' | 'memory' | 'integrations' | 'meetings' | 'tools' | 'usage' | 'spaces' | 'modules' | 'behavior' | 'data' | 'trash'
export type KnowledgeTab = 'memory' | 'documents'
export type { Scope, SessionStatus }

/**
 * `abort` only detaches this window from the run's SSE; ending the run itself is `api.stopRun(runId)`.
 *
 * `answering` is the run still producing a reply, which is *not* the same as the SSE being open: the
 * run goes on to auto-learn after its `done`, and that tail can outlast the reply it followed. Every
 * busy affordance — the caret, Stop, the hidden message actions, the chart placeholders — reads
 * `answering`, so a finished reply settles at `done` instead of at the end of the connection.
 */
export interface Streaming {
  messageId: string | null
  runId: string
  abort: AbortController
  answering: boolean
  /** Seq of the last event applied: a replayed or re-delivered event at or below it is dropped. */
  seq: number
  /** Stop was pressed and the run has not yet reported its end. */
  stopping: boolean
}

/** A message sent but not yet confirmed by the run's `user_message` event; shown dimmed in the transcript. */
export interface PendingSend { key: number; text: string; at: number }
const EMPTY_PENDING: readonly PendingSend[] = Object.freeze([])
let pendingKeySeq = 0

/** One live conversation. Store-local: a running AbortController must never cross the IPC bus. */
export interface ChatSession {
  conversation: Conversation
  streaming: Streaming | null
  status: SessionStatus
  /** epoch ms the last run finished; drives the 6 s green hold */
  finishedAt: number | null
  /** tool calls waiting on the approval card */
  pendingApprovals: number
  /** assistant replies that landed while this session was not focused */
  unread: number
  /** epoch ms of the last focus or run; only used to pick eviction victims */
  touchedAt: number
  /** The run died before it opened a reply (no message to stamp the error on): shown as a notice, cleared by the next message. */
  runError: { message: string; runId: string | null; interrupted: boolean } | null
  /** Sends the run has not echoed back yet (optimistic bubbles); undefined when none. */
  pendingSends?: PendingSend[]
}

interface Toast { id: number; text: string; kind: 'info' | 'error' | 'learned'; action?: { label: string; run: () => void } }

/** Live sessions kept in memory at once. Beyond this the least recently touched are dropped. */
const MAX_SESSIONS = 12
const HOLD_MS = 6000
/** How long an Undo toast stays up. */
const UNDO_MS = 8000

/**
 * Which folders are open in the Docs tree. localStorage rather than the backend: it is this window's
 * view of the tree, not a fact about the docs, and it must survive a reload without a round trip.
 */
const EXPANDED_KEY = 'grain.docs.expandedFolders'

const readExpanded = (): string[] => {
  try {
    const raw = JSON.parse(localStorage.getItem(EXPANDED_KEY) ?? '[]')
    return Array.isArray(raw) ? raw.filter((p): p is string => typeof p === 'string') : []
  } catch {
    return []
  }
}

const writeExpanded = (paths: string[]): string[] => {
  try {
    localStorage.setItem(EXPANDED_KEY, JSON.stringify(paths))
  } catch { /* a private window still gets a working tree, it just forgets */ }
  return paths
}

/** Writers need a scope a row can live in: 'all' and 'personal' both mean the personal voice. */
const styleScope = (s: Scope): string | null => (s === 'all' || s === 'personal' ? null : s)

const newSession = (conversation: Conversation): ChatSession =>
  ({ conversation, streaming: null, status: 'idle', finishedAt: null, pendingApprovals: 0, unread: 0, touchedAt: Date.now(), runError: null })

const countApprovals = (c: Conversation): number =>
  (c.messages ?? []).reduce((n, m) => n + (m.tool_events ?? []).filter((t) => t.pending && t.needs_approval).length, 0)

export interface State {
  ready: boolean
  backendError: string | null
  /** The supervisor's view of the sidecar; anything but "ready" shows the reconnecting banner. */
  backendState: BackendState
  settings: Settings
  models: ModelInfo[]
  modelsError: string | null
  tools: ToolInfo[]
  google: GoogleStatus | null
  tasksSync: TasksSyncStatus | null
  todoCalendar: TodoCalendarStatus | null
  dashboard: TodayDashboard | null
  todos: Todo[]
  recap: Recap | null
  recapLoading: boolean
  /** The Agent Inbox on Today: what needs the user, and what the scheduled jobs did. */
  agentInbox: AgentInbox | null
  jobs: Job[]

  projects: Project[]
  personalStats: Project['stats']

  view: View
  /** The classic view the canvas was entered from; `leaveCanvas` (⌘⇧C) returns to it. */
  lastClassicView: ClassicView
  /** Layout of the Memory panel (list + graph live in one panel). */
  memoryMode: MemoryMode
  projectViewId: string | null
  /** Project the next new chat will be created in (null = personal). */
  draftProjectId: string | null
  /**
   * Reasoning effort and fast mode the next new chat will be created with. A draft chat has no row to
   * PATCH, so the picker parks its choice here and `send` applies it once the conversation exists.
   */
  draftEffort: Effort
  /** A model picked on a draft chat. Null follows `settings.defaultModel`; picking one must not rewrite that default. */
  draftModel: string | null
  draftFast: boolean
  /** The first message of a chat that has no row yet, shown until the row exists. */
  draftPendingSend: PendingSend | null
  /** A file was attached before this draft had a row. `send` marks the new chat untrusted. */
  uploadTaintTarget: 'draft' | 'page' | null
  /** Why that pending mark exists: `upload` for a file, `email` for a message someone else wrote. */
  uploadTaintSource: string
  /** Scope filter used by the Memory / Graph / Documents library views. */
  libraryScope: Scope
  /** Scope the memories/graph/documents arrays are currently loaded for. */
  dataScope: Scope

  sidebarOpen: boolean
  contextOpen: boolean
  contextTab: ContextTab
  /**
   * The page agent (⌘I): a chat pinned to whatever view is on screen. `pageContext` is republished
   * by the active view on every change; `pageAgentId` is the thread, created on the first send.
   */
  pageAgentOpen: boolean
  pageAgentId: string | null
  /** Model, effort, and fast mode for the next ⌘I thread, and the live thread once it exists. */
  pageAgentModel: string | null
  pageAgentEffort: Effort
  pageAgentFast: boolean
  pageContext: PageContext | null
  /** Message whose execution trace the Trace tab shows (null = latest assistant reply). */
  traceMessageId: string | null
  settingsOpen: boolean
  /** The tab Settings opens on. Read once when the dialog mounts. */
  settingsTab: SettingsTab
  /** Which half of Settings → Knowledge base is showing. */
  knowledgeTab: KnowledgeTab
  projectModal: { mode: 'create' } | { mode: 'edit'; project: Project } | null
  toasts: Toast[]

  conversations: Conversation[]
  /** Loaded conversations, keyed by id. Each one streams independently. */
  sessions: Record<string, ChatSession>
  /** Conversations with a reply running right now, from `/runs` and the app topic's `run_state`: the sidebar pulse for a chat with no session. */
  liveRuns: LiveRuns
  focusedConversationId: string | null

  memories: Memory[]
  graph: GraphData
  documents: Document[]

  /** Each chat's plan artifact, keyed by conversation id: the checklist the assistant works from. */
  plans: Record<string, PlanStep[]>
  /** Procedural memory — candidates and approved skills. Loaded when the review surface opens. */
  skills: Skill[]
  /** Writing style for the loaded scope: the profile a chat drafts with, and the samples behind it. */
  style: StyleState | null
  styleSamples: StyleSample[]
  /** An LLM re-read of the samples is in flight (the Learn now button). */
  styleLearning: boolean

  /** Docs: the markdown the user writes. List rows, plus the one open in the editor. */
  docs: Doc[]
  activeDoc: FullDoc | null
  /** Ids of the docs open as tabs, most recent last. */
  docTabs: string[]
  docRevisions: DocRevision[]
  /** Folders of the Files tree, nested by path inside a scope. Server-owned, so an empty one survives a reload. */
  docFolders: DocFolder[]
  /**
   * Which rows of the Files tree are unfolded, as `folderKey`/`groupShutKey` keys. Kept in
   * localStorage: a tree that forgets is a tree you refold every morning.
   */
  expandedFolders: string[]
  /** Assistant edits awaiting review, across every doc — the sidebar badge. */
  docsPending: number
  docMode: DocMode
  /** Editor buffer for the open doc: what the user has typed but autosave has not yet flushed. */
  docDraft: string | null
  /** The same for the title, which autosaves on the same debounce rather than only on blur. */
  docTitleDraft: string | null
  docSaving: boolean

  /** Meetings: recorded calls. List rows, plus the one open in the notepad. */
  meetings: Meeting[]
  activeMeeting: FullMeeting | null
  /** `null` until the first status poll lands; `.active` is the live recording, if any. */
  meetingStatus: MeetingStatusInfo | null
  /** The open meeting's transcript tail, de-duplicated by `applyCursor`. */
  meetingSegments: MeetingSegment[]
  /** Rowid cursor the next `/segments` poll resumes from. 0 = the whole tail. */
  meetingCursor: number
  meetingPreflight: MeetingPreflight | null
  /** Enhance proposals awaiting review, across every meeting — the sidebar badge. */
  meetingsPending: number
  /** The rail's search box, held here rather than in the view: `refreshMeetings` is called from the
   *  recorder bar's 5s tick and from the autosave too, and those must not drop the user's filter. */
  meetingQuery: string
  /** Notepad buffer for the open meeting: what the user has typed but autosave has not flushed. */
  meetingNotesDraft: string | null
  meetingSaving: boolean
  /** A lifecycle call (start/stop/enhance/retranscribe) is in flight; every such button disables. */
  meetingBusy: boolean
  meetingConsentOpen: boolean

  /** Activity monitor. `null` until the first status poll lands. */
  activity: ActivityStatus | null
  activityEvents: ActivityEvent[]
  activitySummaries: ActivitySummary[]
  activityContext: ActivityContextFile | null
  activityBusy: boolean
  activityInsights: ActivityInsights | null
  activityInsightsBusy: boolean

  /** Cowork: the desk rail, the open desk, and everything its detail pane shows. */
  desks: Desk[]
  /** Set the moment a desk is opened, so a slower `desks.get` cannot land on a desk since left. */
  activeDeskId: string | null
  activeDesk: FullDesk | null
  deskFiles: DeskFile[]
  deskPreview: { path: string; text: string } | null
  /** Unseen `needs_you` events across every desk: the sidebar badge and the Today card. */
  deskInbox: DeskEvent[]
  deskBusy: boolean
  /** The rail lists archived desks instead of live ones. */
  deskShowArchived: boolean
  setDeskShowArchived: (v: boolean) => Promise<void>

  init: () => Promise<void>
  restartBackend: () => Promise<void>
  loadModels: () => Promise<void>
  saveSettings: (patch: Partial<Settings>) => Promise<void>
  setView: (v: View) => void
  /** Back to `lastClassicView`. */
  leaveCanvas: () => void
  setMemoryMode: (m: MemoryMode) => void
  /** Open the Memory panel, optionally focused on one half. */
  openMemory: (m?: MemoryMode) => void
  toggleSidebar: () => void
  toggleContext: () => void
  /** ⌘I. Opening focuses the panel's composer; the thread itself waits for the first message. */
  togglePageAgent: () => void
  closePageAgent: () => void
  /** Drop the current thread and start a fresh one against the page on screen. */
  resetPageAgent: () => void
  /** Model for the ⌘I thread. Before the first send this is only a draft; it does not change the app default. */
  setPageAgentModel: (model: string) => Promise<void>
  setPageAgentParams: (patch: { effort?: Effort; fast?: boolean }) => Promise<void>
  /** Called by the active view. Passing null means "this view has nothing to say". */
  setPageContext: (ctx: PageContext | null) => void
  setContextTab: (t: ContextTab) => void
  openTrace: (messageId: string) => void
  setSettingsOpen: (o: boolean) => void
  /** Open Settings on one tab — how the rest of the app reaches memory and documents now. */
  openSettings: (tab: SettingsTab, knowledge?: KnowledgeTab) => void
  setKnowledgeTab: (t: KnowledgeTab) => void
  setProjectModal: (m: State['projectModal']) => void
  toast: (text: string, kind?: Toast['kind'], action?: Toast['action']) => void
  /** After a soft delete: a toast with Undo (~8s) that restores it from the trash. */
  offerUndo: (what: string, items: { type: TrashKind; id: string }[], note?: string) => void
  restoreTrashed: (items: { type: TrashKind; id: string }[]) => Promise<void>

  refreshProjects: () => Promise<void>
  openProject: (id: string) => void
  createProject: (p: Pick<Project, 'name' | 'description' | 'system_prompt' | 'color'>) => Promise<void>
  updateProject: (id: string, patch: Partial<Project>) => Promise<void>
  deleteProject: (id: string) => Promise<void>

  setLibraryScope: (s: Scope) => Promise<void>
  loadScope: (s: Scope) => Promise<void>

  refreshConversations: () => Promise<void>
  newChat: (projectId?: string | null) => void
  /** Create a conversation without navigating to it, so a canvas can open a chat window on it. Toasts and resolves null on failure. */
  createConversation: (projectId: string | null) => Promise<Conversation | null>
  selectChat: (id: string | null) => Promise<void>
  /** Load a conversation into `sessions` without focusing it. Concurrent calls share one fetch. */
  openSession: (conversationId: string) => Promise<void>
  /** `openSession`, plus attach to a reply already in flight elsewhere so the window paints amber. Idempotent. */
  attachSession: (conversationId: string) => Promise<void>
  /** Drop a session and abort whatever it was streaming. */
  closeSession: (conversationId: string) => void
  /** Called on focus: clears unread and maps done/error back to idle, but never needs-approval. */
  clearSessionStatus: (conversationId: string) => void
  deleteChat: (id: string) => Promise<void>
  pinChat: (id: string, on: boolean) => Promise<void>
  archiveChat: (id: string, on: boolean) => Promise<void>
  moveChat: (id: string, projectId: string | null) => Promise<void>
  /** Chat above (-1) or below (1) the focused one in the list's own order; no-op in the canvas. */
  stepChat: (dir: 1 | -1) => void
  /** ⌘⇧F: opens the sidebar and bumps the tick the Sidebar watches to show and focus its search. */
  searchChats: () => void
  sidebarSearchTick: number
  renameChat: (id: string, title: string) => Promise<void>
  /** Regenerate a chat's title with the model; the sidebar and header follow via the same patch as a rename. */
  retitleChat: (id: string) => Promise<void>
  setChatModel: (model: string, conversationId?: string) => Promise<void>
  /** Model, effort and fast in ONE request (the picker's Restore defaults); a draft parks them for `send`. Never rejects. */
  setChatConfig: (change: { model?: string; effort?: Effort; fast?: boolean }, conversationId?: string) => Promise<void>
  setChatSettings: (patch: Partial<ConversationSettings>, conversationId?: string) => Promise<void>
  /** A library file was just attached to this chat, so the next reply treats its contents as untrusted. */
  noteUntrustedUpload: (conversationId?: string, pending?: 'draft' | 'page', source?: string) => Promise<void>
  /** Open a new chat about an email. The subject is untrusted, so the chat starts tainted. */
  askAboutEmail: (id: string, subject: string | null | undefined) => Promise<boolean>
  /** `false` when the text was refused, so the caller must keep it. Never rejects. */
  send: (text: string, conversationId?: string) => Promise<boolean>
  /** Send from the ⌘I panel: same contract as `send`, plus the page snapshot and its own thread. */
  sendToPageAgent: (text: string) => Promise<boolean>
  regenerate: (conversationId?: string) => Promise<void>
  /** Replace a sent user message: it and everything after it is hidden (not deleted) in the run that answers the new text. */
  editAndResend: (messageId: string, text: string, conversationId?: string) => Promise<boolean>
  activateVariant: (conversationId: string, messageId: string) => Promise<void>
  /** Continue an interrupted reply in a new run (always the user's click). Rejects with the backend's reason when it cannot. */
  resumeRun: (conversationId: string, runId: string) => Promise<void>
  stop: (conversationId?: string) => Promise<void>

  refreshMemories: (q?: string) => Promise<void>
  addMemory: (content: string, kind: string, projectId: string | null) => Promise<void>
  updateMemory: (id: string, patch: Parameters<typeof api.memories.update>[1]) => Promise<void>
  deleteMemory: (id: string) => Promise<void>

  /** The plan of one chat. Cheap, and the `plan` stream event keeps it current after the first read. */
  loadPlan: (conversationId: string) => Promise<void>
  setPlanSteps: (conversationId: string, steps: PlanStep[]) => Promise<void>
  clearPlan: (conversationId: string) => Promise<void>

  libraryTab: LibraryTab
  setLibraryTab: (tab: LibraryTab) => void
  /** Everything the Library shows that it does not already hold. Safe to call on every entry. */
  refreshLibrary: () => Promise<void>

  refreshDesks: () => Promise<void>
  refreshDeskInbox: () => Promise<void>
  /** Load one desk whole and, when it is live, attach to the run driving it. */
  openDesk: (id: string) => Promise<void>
  /** Resolves null on failure — the new-desk card is holding the user's brief on the verdict. */
  createDesk: (p: Parameters<typeof api.cowork.desks.create>[0]) => Promise<Desk | null>
  startDesk: (id: string) => Promise<void>
  resumeDesk: (id: string, reason?: string) => Promise<void>
  pauseDesk: (id: string) => Promise<void>
  stopDesk: (id: string) => Promise<void>
  /** The steer box, awake or asleep. `false` means the text was refused, so the caller keeps it. */
  messageDesk: (id: string, text: string) => Promise<boolean>
  patchDesk: (id: string, patch: Parameters<typeof api.cowork.desks.patch>[1]) => Promise<void>
  deleteDesk: (id: string, purge?: boolean) => Promise<void>
  /** `quiet` is for the live refresh: a failed background reload must not toast every few seconds. */
  loadDeskFiles: (id: string, path?: string, quiet?: boolean) => Promise<void>
  /** Selects a file; the Files tab fetches the rich preview itself (pictures, pages, load-more). */
  previewDeskFile: (id: string, path: string) => Promise<void>
  /** The promotion verdicts, so the Output tab can show a verified tick or the write that failed. */
  acceptOutputs: (id: string, sel: Parameters<typeof api.cowork.desks.accept>[1]) => Promise<PromotionResult[]>
  rejectOutputs: (id: string, outputIds?: string[], note?: string) => Promise<void>
  /** Keyed by the plan card's `call_id`: a plan is decided through POST /approvals/{call_id}. */
  decidePlan: (callId: string, decision: PlanDecision, edits?: PlanEdit[], note?: string) => Promise<void>
  /** Allow or deny one card a desk is waiting on (live or parked), from the desk pane. */
  answerDeskCard: (callId: string, allow: boolean, note?: string) => Promise<void>
  setPlanMode: (convId: string, mode: 'off' | 'auto' | 'always') => Promise<void>
  markDeskEventSeen: (eventId: string) => Promise<void>
  /** Every unseen needs-you row of ONE desk at once — opening the desk is the acknowledgement. */
  markDeskSeen: (deskId: string) => Promise<void>

  refreshSkills: () => Promise<void>
  /** A procedure the user writes by hand. Still stored as a candidate: approval is always its own step. */
  createSkill: (s: { name: string; description?: string; procedure?: string; project_id?: string | null }) => Promise<void>
  /** Rename, edit, approve or reject. Approving is what lets a skill into the system prompt. */
  updateSkill: (id: string, patch: Parameters<typeof api.skills.update>[1]) => Promise<void>
  deleteSkill: (id: string) => Promise<void>
  /** Ask the backend to distil a chat into a candidate skill for review. */
  induceSkill: (conversationId: string, messageId?: string) => Promise<void>

  refreshGraph: () => Promise<void>
  refreshDocuments: () => Promise<void>

  /** Writing style, for the loaded scope. `saveStyle` marks the profile hand-edited server-side. */
  refreshStyle: () => Promise<void>
  saveStyle: (patch: Parameters<typeof api.style.update>[1]) => Promise<void>
  /** Re-read the samples now. Forced, so it also refreshes a hand-edited profile. */
  learnStyle: () => Promise<void>
  resetStyle: (withSamples?: boolean) => Promise<void>
  addStyleSample: (text: string) => Promise<void>
  deleteStyleSample: (id: string) => Promise<void>

  /** Status only - cheap enough to poll while the Activity panel is open. */
  refreshActivity: () => Promise<void>
  /** Status plus the event log, summaries and activity.md. */
  loadActivity: () => Promise<void>
  setActivityConfig: (patch: Partial<ActivityConfig>) => Promise<void>
  toggleActivitySignal: (signal: ActivitySignal) => Promise<void>
  startActivity: () => Promise<void>
  stopActivity: () => Promise<void>
  pauseActivity: (minutes?: number) => Promise<void>
  resumeActivity: () => Promise<void>
  rollupActivity: () => Promise<void>
  refreshActivityProfile: () => Promise<void>
  deleteActivityEvent: (id: string) => Promise<void>
  deleteActivitySummary: (id: string) => Promise<void>
  purgeActivity: (scope: 'expired' | 'events' | 'summaries' | 'all') => Promise<void>
  /** Ask macOS for one permission. Returns the note to show; '' when it went through silently. */
  grantActivityPermission: (id: string, browser?: string) => Promise<void>
  openActivitySettings: (id: string) => Promise<void>
  /** Record everything, or put back the settings palantir mode replaced. */
  setPalantirMode: (on: boolean) => Promise<void>
  /** Habits noticed and automations on offer. */
  loadActivityInsights: () => Promise<void>
  /** `deep` runs the model pass; without it the patterns are just re-mined locally, for free. */
  refreshActivityInsights: (deep?: boolean) => Promise<void>
  setInsightStatus: (id: string, status: InsightStatus, note?: string) => Promise<void>
  /** Apply one suggestion. A `prompt` action does not act: it opens a chat with the message. */
  applyInsight: (id: string) => Promise<void>
  forgetActivityHabit: (id: string) => Promise<void>
  refreshDashboard: () => Promise<void>
  refreshRecap: (force?: boolean) => Promise<void>
  refreshAgentInbox: () => Promise<void>
  refreshJobs: () => Promise<void>
  /** Schedule a task: a one-off (kind 'once' + run_at) or a repeating job (cron). True if it was created. */
  createJob: (input: Parameters<typeof api.jobs.create>[0]) => Promise<boolean>
  deleteJob: (id: string) => Promise<void>
  setJobEnabled: (id: string, enabled: boolean) => Promise<void>
  runJobNow: (id: string) => Promise<void>
  decideProposal: (id: string, accept: boolean, args?: Record<string, unknown>) => Promise<void>
  /** `opts` carries a propose_plan card's answer: the steps being authorised (with any edits) and a note. */
  approveTool: (callId: string, decision: ApprovalDecision, conversationId?: string, opts?: { steps?: PlanEdit[] | null; note?: string; rules?: string[]; arguments?: Record<string, unknown> | null }) => Promise<void>
  refreshGoogle: () => Promise<void>
  connectGoogle: () => Promise<void>
  disconnectGoogle: () => Promise<void>
  refreshTasksSync: () => Promise<void>
  refreshTodoCalendar: () => Promise<void>
  setTodoCalendar: (patch: { enabled?: boolean; calendarId?: string; keepCompleted?: boolean }) => Promise<void>
  runTodoCalendar: () => Promise<void>
  setTasksSync: (patch: { enabled?: boolean; tasklist?: string; intervalMinutes?: number }) => Promise<void>
  runTasksSync: () => Promise<void>
  refreshTodos: (scope?: Scope, includeDone?: boolean, sort?: 'due' | 'urgency') => Promise<void>
  addTodo: (t: Parameters<typeof api.todos.create>[0]) => Promise<void>
  updateTodo: (id: string, patch: Parameters<typeof api.todos.update>[1]) => Promise<void>
  deleteTodo: (id: string) => Promise<void>
  /** Stores each file and says which ones the assistant can read; the composer builds its note from that. */
  uploadDocuments: (files: FileList | File[], projectId: string | null) => Promise<UploadOutcome[]>
  deleteDocument: (id: string) => Promise<void>
  pinDocument: (id: string, pinned: boolean) => Promise<void>

  refreshDocs: (q?: string) => Promise<void>
  refreshDocsPending: () => Promise<void>
  openDoc: (id: string) => Promise<void>
  closeDocTab: (id: string) => Promise<void>
  createDoc: (d?: { title?: string; content?: string; project_id?: string | null; folder?: string }) => Promise<void>
  /** Retitle the open doc as it is typed, on the same debounce as the body. */
  editDocTitle: (title: string) => void
  /** Type into the open doc. Buffers locally and flushes to the backend on a debounce. */
  /** Today's daily note: found or created on the server, then opened. */
  openDailyNote: () => Promise<void>
  editDoc: (content: string) => void
  /** Flush the buffer now (⌘S, switching docs, leaving the view). */
  flushDoc: () => Promise<void>
  setDocStar: (id: string, starred: boolean) => Promise<void>
  setDocPin: (id: string, pinned: boolean) => Promise<void>
  /** File a doc: which project ('' is personal) and which folder in it, in one patch. */
  moveDoc: (id: string, scope: string, folder: string) => Promise<void>
  refreshDocFolders: () => Promise<void>
  createDocFolder: (path: string, scope?: string) => Promise<void>
  /** Rename or move within a scope: both rewrite a folder's path, and its subtree follows. */
  renameDocFolder: (path: string, newPath: string, scope?: string) => Promise<void>
  /** Without `deleteDocs` the folder's docs move up to its parent. */
  deleteDocFolder: (path: string, deleteDocs?: boolean, scope?: string) => Promise<void>
  /** Unfold or fold one row of the tree, by its `folderKey`/`groupShutKey`. */
  toggleFolder: (key: string) => void
  /** Open the group and every folder down to `path`, so a revealed doc is actually on screen. */
  expandTo: (scope: string, path: string) => void
  deleteDoc: (id: string) => Promise<void>
  setDocMode: (m: DocMode) => void
  refreshDocRevisions: (id?: string) => Promise<void>
  acceptRevision: (revId: string) => Promise<void>
  rejectRevision: (revId: string) => Promise<void>
  restoreRevision: (revId: string) => Promise<void>

  /** No argument re-issues the filter the rail is currently showing, never the unfiltered list. */
  refreshMeetings: (query?: string) => Promise<void>
  /** Type in the rail's search box. The view's effect does the fetch. */
  setMeetingQuery: (query: string) => void
  /** Status only — cheap enough to poll, and it keeps its own 5s tick while a recording is live. */
  refreshMeetingStatus: () => Promise<void>
  refreshMeetingsPending: () => Promise<void>
  openMeeting: (id: string) => Promise<void>
  /** No id creates a meeting first. Opens the consent modal instead when the notice is unacknowledged. */
  startRecording: (meetingId?: string) => Promise<void>
  /** There is only ever one live recording, so none of these four takes an id. */
  stopRecording: () => Promise<void>
  pauseMeeting: () => Promise<void>
  resumeMeeting: () => Promise<void>
  /** Adopt a calendar candidate (find-or-create on its event id) and start recording it. */
  recordCandidate: (candidate: MeetingCandidate) => Promise<void>
  /** Type into the open meeting's notes. Buffers locally and flushes on a debounce. */
  editMeetingNotes: (next: string) => void
  /** Flush the buffer now (⌘S, switching meetings, leaving the view, unmount). */
  flushMeetingNotes: () => Promise<void>
  /** The 2s tick: the live status plus the segment tail from `meetingCursor`. */
  pollMeetingLive: () => Promise<void>
  /** Attach to one meeting's SSE. Idempotent per meeting; a no-op stream today. */
  watchMeeting: (meetingId: string) => Promise<void>
  enhanceMeeting: (id: string, force?: boolean) => Promise<void>
  acceptMeetingRevision: (revisionId: string) => Promise<void>
  rejectMeetingRevision: (revisionId: string) => Promise<void>
  promoteActionItems: (meetingId: string, actionIds: string[]) => Promise<void>
  dismissActionItem: (meetingId: string, actionId: string) => Promise<void>
  deleteMeeting: (id: string) => Promise<void>
  setMeetingConfig: (patch: Partial<MeetingConfig>) => Promise<void>
  loadMeetingPreflight: (force?: boolean) => Promise<void>
  retranscribeMeeting: (id: string) => Promise<void>
  /** No id deletes every retained wav: there is no bulk route, so this loops the list. */
  deleteMeetingAudio: (meetingId?: string) => Promise<void>
  setMeetingConsentOpen: (open: boolean) => void
  /** Stamps `consentedAt`, closes the modal and starts what the user clicked Record on. */
  acceptMeetingConsent: () => Promise<void>
}

let toastSeq = 0
let flushChain: Promise<void> = Promise.resolve()
/** Autosave debounce for the doc editor: long enough to be one history entry, short enough to trust. */
const SAVE_DEBOUNCE_MS = 1200
let saveTimer: ReturnType<typeof setTimeout> | null = null
/** What a doc save would send: each field only when it differs from the saved copy. */
const docEdits = (doc: FullDoc, docDraft: string | null, docTitleDraft: string | null): { content?: string; title?: string } | null => {
  // A title of only whitespace is a slip of the hand, not an edit: hold the saved one.
  const title = docTitleDraft !== null && docTitleDraft.trim() && docTitleDraft !== doc.title
    ? docTitleDraft.trim() : undefined
  const content = docDraft !== null && docDraft !== doc.content ? docDraft : undefined
  return content === undefined && title === undefined ? null : { content, title }
}

/**
 * The state after a request that replaced the doc body (accept, restore). `sent` is the draft right
 * before the request and `base` the body the editor showed then, so a draft that differs now was
 * typed or dictated while the request was in flight.
 *
 * What happens to that text depends on what the server did. When the new body is the old one with
 * something added after it (an accepted recording summary), the typing is kept and the addition is
 * put back after it: keeping the draft alone would autosave over the section just accepted. When
 * the body was replaced outright there is nothing to merge the typing into, so the server wins, as
 * it always has.
 */
export function adoptServerDoc(
  draft: string | null, doc: FullDoc, sent: string | null, base: string
): { activeDoc: FullDoc; docDraft: string | null } {
  const typed = draft !== null && draft !== sent
  const stem = base.trimEnd()
  if (!typed || !doc.content.startsWith(stem)) return { activeDoc: doc, docDraft: null }
  const added = doc.content.slice(stem.length)
  const kept = draft.trimEnd()
  return { activeDoc: doc, docDraft: stem || !kept ? kept + added : `${kept}\n\n${added}` }
}
/** The same debounce for the meeting notepad, on its own timer: typing notes during a call must not
 *  be cancelled by, or cancel, an autosave in the Docs editor. */
const MEETING_SAVE_DEBOUNCE_MS = 1200
let meetingSaveTimer: ReturnType<typeof setTimeout> | null = null
/** While a recording is live the sidebar indicator is mounted in every view but only the Meetings
 *  view polls, so the status poll keeps its own tick. Cleared the moment `active` goes null. */
let meetingLiveTimer: ReturnType<typeof setInterval> | null = null
/**
 * Epoch ms the post-stop watch window closes at.
 *
 * `POST /meetings/{id}/stop` answers with the row already finalized to `ready` and only THEN
 * schedules the enhance pass, so the status poll right after a stop sees no live session and a
 * settled status, and would tear the tick down a moment before the revision, the action items and
 * the late transcriptions land. The window keeps it ticking across that gap; it is bounded so a
 * stop can never leave a poll running for the rest of the session.
 */
let meetingSettleUntil = 0
const MEETING_SETTLE_MS = 90_000

/**
 * A pop-out renderer (`?surface=widget`). Same lookup as main.tsx: dev serves the query off
 * `location.search`, and the href fallback covers one that arrived behind a hash.
 */
const isPopout = (): boolean => {
  const q = window.location.search || (window.location.href.includes('?') ? window.location.href.slice(window.location.href.indexOf('?')) : '')
  return new URLSearchParams(q).get('surface') !== null
}

/** Pending `done` → `idle` timers, keyed by conversation id. A new run cancels its own. */
const holds = new Map<string, ReturnType<typeof setTimeout>>()
const clearHold = (convId: string): void => {
  const t = holds.get(convId)
  if (t !== undefined) {
    clearTimeout(t)
    holds.delete(convId)
  }
}

/** Statuses that are still moving on their own: the capture, the transcription queue or the enhance
 *  pass is running, so one tick keeps the sidebar indicator and the open meeting from freezing. */
const SETTLING: MeetingStatus[] = ['recording', 'stopped', 'transcribing', 'enhancing']

/** A hand-started meeting has no calendar event to take a title from. */
const newMeetingTitle = (): string =>
  `Meeting ${new Date().toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })}`

/** Google's ISO timestamps as the epoch seconds `POST /meetings` wants. */
const epochSeconds = (iso: string): number | null => {
  const t = Date.parse(iso)
  return Number.isFinite(t) ? t / 1000 : null
}

/** The newest rowid in a `?since=` batch, which is what the next poll resumes from. */
const lastCursor = (rows: MeetingSegment[], from = 0): number =>
  rows.reduce((n, r) => Math.max(n, r.cursor ?? 0), from)

/**
 * A blocked `POST /meetings/{id}/start` is a 409 whose body is the capability checklist. `req()`
 * JSON-encodes a non-string `detail` into the error message, exactly as a `RunConflict` arrives, so
 * naming the first blocker and its fix is what turns "409" into "install a loopback device".
 */
const startBlockers = (e: unknown): MeetingCapability[] => {
  try {
    const d = JSON.parse((e as Error).message) as { blockers?: MeetingCapability[] }
    return Array.isArray(d?.blockers) ? d.blockers : []
  } catch {
    return []
  }
}
const startFailure = (e: unknown): string => {
  const first = startBlockers(e).find((b) => !b.ok)
  return first ? `${first.label}: ${first.detail}${first.fix ? ` — ${first.fix}` : ''}` : (e as Error).message
}

/**
 * What the consent modal does once accepted, when the Record click that opened it was for a doc.
 * `startRecording` opens the Meetings view, which a note must not do, so the doc recorder parks its
 * own start here and `acceptMeetingConsent` runs it instead. Dismissing the modal drops it, for the
 * same reason `consentIntent` is dropped. Module-level rather than state: a closure must not
 * cross the IPC bus or be serialised.
 */
export const consentResume: { run: (() => void) | null } = { run: null }

/** A 409 from `POST /chat` arrives as a `RunConflict` JSON-encoded in the error detail. */
const runConflict = (e: unknown): RunConflict | null => {
  try {
    const d = JSON.parse((e as Error).message) as RunConflict
    return typeof d?.run_id === 'string' && typeof d.seq === 'number' ? d : null
  } catch {
    return null
  }
}

/**
 * Conversations with a mounted chat surface, refcounted. The canvas view never sets
 * `focusedConversationId`, so without this the LRU would pick a victim by mount order alone and an
 * on-screen window would be a legal one.
 */
const retained = new Map<string, number>()

/** LRU by `touchedAt`, never evicting a retained, focused, streaming or unread session. */
const evict = (sessions: Record<string, ChatSession>, keepId: string | null): Record<string, ChatSession> => {
  const keep = new Set(retained.keys())
  if (keepId) keep.add(keepId)
  const victims = pickEvictions(sessions, keep, MAX_SESSIONS)
  if (!victims.length) return sessions
  const out = { ...sessions }
  for (const id of victims) {
    clearHold(id)
    delete out[id]
  }
  return out
}

/** One in-flight promise per key, so N concurrent callers share one fetch instead of racing. */
const share = (map: Map<string, Promise<void>>, key: string, fn: () => Promise<void>): Promise<void> => {
  const live = map.get(key)
  if (live) return live
  const p = fn().finally(() => { if (map.get(key) === p) map.delete(key) })
  map.set(key, p)
  return p
}

/** The draft chat being created by `send`, so a concurrent send joins it rather than making another. */
let draftCreate: Promise<string | null> | null = null
const loads = new Map<string, Promise<void>>()
const attaches = new Map<string, Promise<void>>()
/** The tail of each conversation's PATCH queue, so a send can wait for a model or effort change to land. */
const convWrites = new Map<string, Promise<unknown>>()

/** What editing `messageId` would hide: that row and every row after it, and whether any hidden reply ran tools. */
export const editCut = (messages: Message[], messageId: string): { removed: number; ranTools: boolean } => {
  const at = messages.findIndex((m) => m.id === messageId)
  if (at < 0) return { removed: 0, ranTools: false }
  const hidden = messages.slice(at)
  return { removed: hidden.length, ranTools: hidden.some((m) => m.role === 'assistant' && !!m.tool_events?.some((t) => !t.pending)) }
}

export type StopOutcome = 'accepted' | 'gone' | 'failed'

/**
 * What a Stop request amounts to. `ok: false` and a 404 both mean there was no live run left to stop, which
 * is the result the user wanted; only a request that never landed (a timeout, a refused connection, a 5xx) failed.
 */
export const stopOutcome = (call: { ok: boolean } | { error: unknown }): StopOutcome => {
  if ('error' in call) return call.error instanceof ApiError && call.error.status === 404 ? 'gone' : 'failed'
  return call.ok ? 'accepted' : 'gone'
}

/** One in-flight Stop per run: a second press (the button, then Escape) joins the first instead of sending another. */
const stops = new Map<string, Promise<StopOutcome>>()

/**
 * A reply whose run died under it: the in-flight message carries the error, every tool call that never
 * returned is marked unknown (an approval card still waiting is left, it is a decision and not an outcome),
 * and the session stops answering. Pure, for the `error` event.
 */
export const settleInterrupted = (s: ChatSession, message: string): ChatSession => {
  const mid = s.streaming?.messageId
  const msgs = (s.conversation.messages ?? []).map((m) => m.id !== mid ? m : {
    ...m,
    error: m.error ?? message,
    tool_events: m.tool_events?.map((t) => t.pending && !t.needs_approval
      ? { ...t, pending: false, error: 'Outcome unknown: the reply ended before this call returned.' }
      : t) ?? null
  })
  return {
    ...s,
    conversation: { ...s.conversation, messages: msgs },
    streaming: s.streaming && { ...s.streaming, answering: false },
    finishedAt: Date.now()
  }
}

/** Every conversation mutation a stream event makes, as one new session. No side effects — exported for store.test.ts. */
export const applyEvent = (s: ChatSession, ev: ChatEvent, focused: boolean, seq?: number | null): ChatSession => {
  // The tape is exactly-once on the wire, but an attach replay and a refetch can overlap: an event at or
  // below what this session already applied is a no-op, so deltas never append twice.
  if (seq != null && s.streaming) {
    if (seq <= s.streaming.seq) return s
    s = { ...s, streaming: { ...s.streaming, seq } }
  }
  const c = s.conversation
  const msgs = c.messages ?? []
  const withMsgs = (messages: Message[]): ChatSession => ({ ...s, conversation: { ...c, messages } })
  const mapMsg = (mid: string, fn: (m: Message) => Message): ChatSession =>
    withMsgs(msgs.map((m) => (m.id === mid ? fn(m) : m)))
  switch (ev.event) {
    case 'user_message':
      // Merge by id: a steer is persisted and published by its endpoint, so an attach replay plus the
      // live stream (or a refetch) can both carry it.
      // The optimistic bubble this message confirms goes in the same returned session (the backend stores the stripped text).
      {
        const pend = s.pendingSends
        const at = pend ? pend.findIndex((p) => p.text.trim() === ev.data.content) : -1
        const rest = at >= 0 && pend ? pend.filter((_, i) => i !== at) : pend
        const pendingSends = rest && rest.length ? rest : undefined
        if (msgs.some((m) => m.id === ev.data.id)) return at >= 0 ? { ...s, runError: null, pendingSends } : { ...s, runError: null }
        return { ...withMsgs([...msgs, ev.data]), runError: null, pendingSends }
      }
    case 'assistant_message': {
      // Merge by id: attaching to a run replays this event into a conversation row that may already
      // hold the message, and appending it twice is the duplicate the ring used to paint. The replay
      // then rebuilds the row from its deltas, so a held row is replaced wholesale, not merged.
      const held = msgs.some((m) => m.id === ev.data.id)
      return {
        ...(held
          ? mapMsg(ev.data.id, () => ev.data)
          : withMsgs([...msgs, ev.data])),
        runError: null,
        streaming: s.streaming && { ...s.streaming, messageId: ev.data.id, answering: true },
        // A steered run opens a new segment after a `done`; the green hold belongs to the real end.
        finishedAt: null
      }
    }
    case 'title':
      return { ...s, conversation: { ...c, title: ev.data.title } }
    case 'removed_message':
      return withMsgs(msgs.filter((m) => m.id !== ev.data.id))
    case 'restored_message': {
      // The answer a failed regenerate had superseded. Replace by id (the tape can replay), else slot it in by time.
      const back = ev.data.message
      const rest = msgs.filter((m) => m.id !== back.id)
      const at = rest.findIndex((m) => m.created_at > back.created_at)
      return withMsgs(at < 0 ? [...rest, back] : [...rest.slice(0, at), back, ...rest.slice(at)])
    }
    case 'status':
      return mapMsg(ev.data.id, (m) => ({ ...m, status: ev.data.kind ? { kind: ev.data.kind, attempt: ev.data.attempt, max: ev.data.max, until: ev.data.until, reason: ev.data.reason } : null }))
    case 'delta':
      return mapMsg(ev.data.id, (m) => ({ ...m, content: m.content + ev.data.text, status: null }))
    case 'reasoning':
      return mapMsg(ev.data.id, (m) => ({ ...m, reasoning: (m.reasoning ?? '') + ev.data.text, status: null }))
    case 'tool_call':
      return mapMsg(ev.data.message_id, (m) => (m.tool_events?.some((t) => t.id === ev.data.id) ? m : { ...m, status: null, tool_events: [...(m.tool_events ?? []), { id: ev.data.id, name: ev.data.name, arguments: ev.data.arguments, result_preview: '', duration_ms: 0, error: null, pending: true, needs_approval: !!ev.data.needs_approval, forced: !!ev.data.forced, permission: ev.data.permission ?? null, plan: ev.data.plan ?? null, agent: ev.data.agent }] }))
    case 'tool_result':
      return mapMsg(ev.data.message_id, (m) => ({ ...m, tool_events: (m.tool_events ?? []).map((t) => (t.id === ev.data.id ? { ...ev.data, pending: false } : t)) }))
    case 'span':
      return mapMsg(ev.data.message_id, (m) => {
        const trace = m.trace ?? []
        const i = trace.findIndex((sp) => sp.id === ev.data.span.id)
        return { ...m, trace: i >= 0 ? trace.map((sp, j) => (j === i ? ev.data.span : sp)) : [...trace, ev.data.span] }
      })
    case 'done': {
      const done = !ev.data.id ? s : mapMsg(ev.data.id, (m) => ({ ...m, status: null, error: ev.data.error, context_used: ev.data.context_used, tool_events: ev.data.tool_events?.length ? ev.data.tool_events : m.tool_events, trace: ev.data.trace?.length ? ev.data.trace : m.trace, reasoning: ev.data.reasoning ?? m.reasoning, outcome: ev.data.outcome ?? (ev.data.stopped ? 'stopped' : (ev.data.partial as Message['outcome']) ?? null), error_kind: ev.data.error_kind ?? null }))
      // The reply is whole and persisted here. The stream stays open for the auto-learn tail, so the
      // subscription is left alone and only `answering` drops.
      // `unread` counts the final done, not the first token: a chat that is mid-reply off-screen has nothing to read yet.
      // A steer segment's done is not the end, so it neither counts nor clears a Stop that is still pending.
      const final = !ev.data.segment
      return {
        ...done,
        streaming: done.streaming && { ...done.streaming, answering: false, stopping: final ? false : done.streaming.stopping },
        finishedAt: Date.now(),
        unread: final && !focused ? done.unread + 1 : done.unread
      }
    }
    case 'error': {
      // The run's terminal frame. Once `done` has gone out the reply is whole and there is nothing to settle;
      // a death mid-reply stamps the message, and one before any reply exists has no message to carry it.
      if (s.streaming && !s.streaming.answering) return s
      const held = s.streaming?.messageId ? msgs.find((m) => m.id === s.streaming?.messageId) : undefined
      if (s.streaming && held) return settleInterrupted(s, ev.data.message)
      return { ...s, runError: { message: ev.data.message, runId: s.streaming?.runId ?? ev.data.run_id ?? null, interrupted: !!ev.data.interrupted } }
    }
    default:
      return s
  }
}

/** The slice one meeting stream event can move. Nothing else, so the reducer stays testable. */
type MeetingWatch = Pick<State, 'activeMeeting' | 'meetingSegments' | 'meetingCursor' | 'meetingStatus'>

const isRecord = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null
/** `MeetingStreamEvent.data` is `unknown`, because the bus is a seam: the frames are declared here
 *  and nothing publishes them yet, so every one is checked rather than cast. */
const isSegment = (v: unknown): v is MeetingSegment => isRecord(v) && typeof v.id === 'string' && typeof v.meeting_id === 'string' && typeof v.seq === 'number'
const isStatus = (v: unknown): v is MeetingStatusInfo => isRecord(v) && isRecord(v.config) && isRecord(v.counts)
const isRevision = (v: unknown): v is NonNullable<FullMeeting['pending']> =>
  isRecord(v) && typeof v.id === 'string' && typeof v.meeting_id === 'string' && typeof v.after === 'string'

/**
 * Every meeting mutation a stream event makes, as one new slice. No side effects — a toast or a
 * refetch belongs to `watchMeeting`, which is what makes this safe to run on a replayed frame.
 */
const applyMeetingEvent = (s: MeetingWatch, ev: MeetingStreamEvent): MeetingWatch => {
  switch (ev.event) {
    case 'segment': {
      if (!isSegment(ev.data) || ev.data.meeting_id !== s.activeMeeting?.id) return s
      // `applyCursor` is the same fold the 2s poll uses, so a pushed row and a polled row that
      // describe the same segment collapse into one line rather than two.
      return { ...s, meetingSegments: applyCursor(s.meetingSegments, [ev.data]), meetingCursor: Math.max(s.meetingCursor, ev.data.cursor ?? 0) }
    }
    case 'status':
      return isStatus(ev.data) ? { ...s, meetingStatus: ev.data } : s
    case 'revision': {
      if (!isRevision(ev.data) || !s.activeMeeting || ev.data.meeting_id !== s.activeMeeting.id) return s
      // Only a proposal still waiting is `pending`; an applied one arrives with the meeting refetch.
      return { ...s, activeMeeting: { ...s.activeMeeting, pending: ev.data.status === 'pending' ? ev.data : s.activeMeeting.pending, has_pending: ev.data.status === 'pending' } }
    }
    default:
      // 'error' and 'end' are the loop's business: one toasts, the other just lets the generator finish.
      return s
  }
}

export { adjacentChatId }

export const useStore = create<State>((set, get) => {
  /**
   * App's init effect runs twice under React.StrictMode, so both of these are latched. A second
   * `window.os.onMenu` subscription would run every menu action twice, which silently kills the
   * toggles: ⌘B/⌘I/⌘⇧C flip and flip straight back, and ⌘N opens two chats. The canvas store
   * latches its own menu/bus listeners the same way.
   */
  let menuWired = false
  let inited = false
  /** The live meeting SSE subscription. Store-local, like `Streaming.abort`: a running
   *  AbortController must never cross the IPC bus. */
  let meetingWatch: { id: string; abort: AbortController } | null = null
  /** Which meeting the Record click that opened the consent modal was for; `undefined` means
   *  "create one". Kept out of state because the modal must not be able to re-target it. */
  let consentIntent: string | undefined
  /** The meeting `openMeeting` is currently fetching, so a slower response cannot clobber one the
   *  user has since switched away from. */
  let openingMeeting: string | null = null

  const wireMenu = (): void => {
    if (menuWired) return
    menuWired = true
    window.os.onMenu((action) => {
      const s = get()
      // In the canvas view ⌘N opens a chat window instead; canvas/store.ts handles it there.
      if (action === 'new-chat') {
        // Always personal: a new chat belongs to a project only when the user asked for one by
        // clicking "New chat" inside it. Inheriting the project behind the current screen meant a
        // ⌘N taken while reading a project chat silently filed the next unrelated thought under it.
        if (s.view !== 'canvas') s.newChat(null)
      } else if (action === 'settings') s.setSettingsOpen(true)
      else if (action === 'new-note') void s.createDoc({})
      else if (action === 'daily-note') { s.setView('docs'); void s.openDailyNote() }
      else if (action === 'toggle-sidebar') s.toggleSidebar()
      else if (action === 'chat:next') s.stepChat(1)
      else if (action === 'chat:prev') s.stepChat(-1)
      else if (action === 'chat:search') s.searchChats()
      else if (action === 'toggle-context') s.toggleContext()
      else if (action === 'page-agent') s.togglePageAgent()
      else if (action === 'view:graph') s.openMemory('graph')
      else if (action === 'view:memory') s.openMemory()
      else if (action === 'view:documents') s.openSettings('knowledge', 'documents')
      else if (action.startsWith('desk:')) { s.setView('cowork'); void s.openDesk(action.slice(5)) }
      else if (action.startsWith('view:')) s.setView(action.slice(5) as View)
      else if (action === 'upload') {
        s.openSettings('knowledge', 'documents')
        setTimeout(() => document.getElementById('doc-upload-input')?.click(), 100)
      }
    })
  }

  /**
   * One desk row, folded into the rail and the open detail pane. Update-only on the list: a
   * `desk_status` for a desk the current scope filter excludes must not splice it in behind the
   * filter. Everything that creates a desk refreshes the list itself.
   */
  const putDesk = (d: Desk): void =>
    set((st) => ({
      // A desk archived (or unarchived) leaves the list it no longer belongs to.
      desks: Boolean(d.archived) !== st.deskShowArchived
        ? st.desks.filter((x) => x.id !== d.id)
        : st.desks.some((x) => x.id === d.id) ? st.desks.map((x) => (x.id === d.id ? d : x)) : st.desks,
      activeDesk: st.activeDesk?.id === d.id ? { ...st.activeDesk, ...d } : st.activeDesk
    }))
  /** The conversation a desk owns, from whichever copy of the row is loaded. */
  const deskConv = (id: string): string | undefined => {
    const st = get()
    return (st.activeDesk?.id === id ? st.activeDesk : st.desks.find((d) => d.id === id))?.conversation_id
  }

  const patchSession = (convId: string, fn: (s: ChatSession) => ChatSession): void =>
    set((st) => {
      const cur = st.sessions[convId]
      if (!cur) return {}
      const next = fn(cur)
      return next === cur ? {} : { sessions: { ...st.sessions, [convId]: next } }
    })
  const addPending = (convId: string, p: PendingSend): void =>
    patchSession(convId, (s) => ({ ...s, pendingSends: [...(s.pendingSends ?? []), p] }))
  const dropPending = (convId: string, key: number): void =>
    patchSession(convId, (s) => {
      if (!s.pendingSends?.some((p) => p.key === key)) return s
      const rest = s.pendingSends.filter((p) => p.key !== key)
      return { ...s, pendingSends: rest.length ? rest : undefined }
    })
  /** A steer's response carries the stored message: applied as the event, it settles the bubble now (the stream copy is a no-op by id). */
  const settleSteer = (convId: string, message: Message | undefined): void => {
    if (message) patchSession(convId, (s) => applyEvent(s, { event: 'user_message', data: message } as ChatEvent, get().focusedConversationId === convId))
  }
  const putSession = (conversation: Conversation): void =>
    set((st) => {
      const cur = st.sessions[conversation.id]
      // A fetch that lands among the deltas must not clobber what the stream already applied: the
      // in-flight assistant message is not persisted yet, so an overwrite blanks the visible reply.
      const next = cur
        ? { ...cur, conversation: mergeConversation(cur.conversation, conversation, !!cur.streaming), touchedAt: Date.now() }
        : newSession(conversation)
      const sessions = { ...st.sessions, [conversation.id]: next }
      return { sessions: evict(sessions, st.focusedConversationId) }
    })
  const patchConversation = (convId: string, fn: (c: Conversation) => Conversation): void =>
    patchSession(convId, (s) => ({ ...s, conversation: fn(s.conversation) }))
  const hold = (convId: string): void => {
    clearHold(convId)
    holds.set(convId, setTimeout(() => {
      holds.delete(convId)
      patchSession(convId, (s) => (s.status === 'done' ? { ...s, status: 'idle', finishedAt: null } : s))
    }, HOLD_MS))
  }
  const refreshAll = (): void => {
    void get().refreshMemories()
    void get().refreshGraph()
    void get().refreshDocuments()
    void get().refreshProjects()
  }

  /** True once init has loaded the app's data at least once; a recovered backend then needs a refresh, not a re-init. */
  let loadedOnce = false
  let backendSeen: BackendState = 'ready'
  let watching = false
  let stateWired = false
  // `/events` resume cursor. The topic's seq restarts with the backend process, so a restart zeroes it.
  let eventsSince = 0

  /**
   * The main process supervises the sidecar and announces each state. While it restarts the UI stays up
   * (the banner says so); when it is back the port may have moved, so the base URL is re-pointed and the
   * data re-fetched. If the first start failed, init never loaded anything, so it runs for real now.
   */
  const onBackendState = (info: BackendInfo): void => {
    const was = backendSeen
    backendSeen = info.state
    set({ backendState: info.state })
    if (info.state === 'failed') {
      set({ ready: true, backendError: info.error ?? 'The backend stopped and could not be restarted.' })
    } else if (info.state === 'ready' && (was !== 'ready' || get().backendError)) {
      set({ backendError: null })
      if (!loadedOnce) {
        inited = false
        void get().init()
        return
      }
      setBase(info.url)
      void get().loadModels()
      void get().loadScope('all')
      void get().refreshDashboard()
      void get().refreshTodos()
      void get().refreshDocsPending()
      void get().refreshActivity()
      refreshAll()
      // Runs did not survive the process: the conversation list, the live-run map and every session
      // that was not streaming (its in-flight reply may have been closed out as interrupted) are stale.
      void get().refreshConversations().catch(() => undefined)
      eventsSince = 0
      set({ liveRuns: {} })
      void seedLiveRuns()
      for (const id of Object.keys(get().sessions)) {
        if (!get().sessions[id].streaming) void get().openSession(id).catch(() => undefined)
      }
    }
  }

  const seedLiveRuns = async (): Promise<void> => {
    const runs = await api.runs().catch(() => null)
    if (!runs) return
    const live: LiveRuns = {}
    for (const r of runs) if (r.answering) live[r.conversation_id] = { run_id: r.run_id, status: r.status }
    // Whatever a `run_state` frame already folded in is newer than this snapshot.
    set((st) => ({ liveRuns: { ...live, ...st.liveRuns } }))
  }

  /**
   * Follow `/events` for the whole session. Auto-learn runs after its reply's run has ended — that
   * is the point, the chat is free again — so its results have no conversation stream left to
   * arrive on. The connection is re-opened for as long as the window lives, resuming from the last
   * seq so a reconnect replays rather than skips, and backing off so a dead backend is not hammered.
   */
  /**
   * A desk row from the app topic. The headline alone moves about once a second while a desk works,
   * so only a status change re-reads the open desk (its plan, cards and outputs) and the inbox; the
   * row itself is folded in every time. The inbox refresh is coalesced across a burst of desks.
   */
  let inboxTimer: ReturnType<typeof setTimeout> | null = null
  const onDeskChanged = (d: Desk): void => {
    const st = get()
    const before = (st.activeDesk?.id === d.id ? st.activeDesk : st.desks.find((x) => x.id === d.id))?.status
    putDesk(d)
    if (before === d.status) return
    if (st.activeDeskId === d.id) void get().openDesk(d.id)
    if (d.status === 'review') void get().loadDeskFiles(d.id)
    if (inboxTimer === null) {
      inboxTimer = setTimeout(() => { inboxTimer = null; void get().refreshDeskInbox() }, 300)
    }
  }

  // A title written off the run (or by another window) reaches the list and any open session without a refetch.
  const applyTitle = (id: string, title: string): void => {
    if (get().sessions[id]) patchConversation(id, (c) => (c.title === title ? c : { ...c, title }))
    set((st) => ({ conversations: st.conversations.map((c) => (c.id === id && c.title !== title ? { ...c, title } : c)) }))
  }
  const watchBackgroundEvents = async (): Promise<void> => {
    let backoff = 1000
    await seedLiveRuns()
    for (;;) {
      try {
        for await (const ev of backgroundStream(eventsSince)) {
          if (ev.seq !== null) eventsSince = ev.seq
          backoff = 1000
          if (ev.event === 'learned') {
            const { memories, nodes, edges } = ev.data
            get().toast(`Learned ${memories.length} memor${memories.length === 1 ? 'y' : 'ies'}, ${nodes.length} entities, ${edges.length} relations`, 'learned')
            if (memories.length + nodes.length + edges.length) refreshAll()
          } else if (ev.event === 'learn_error') {
            get().toast(`Auto-learn failed: ${ev.data.message}`, 'error')
          } else if (ev.event === 'job_finished') {
            void get().refreshAgentInbox()
            window.dispatchEvent(new Event('grain-job-finished'))
          } else if (ev.event === 'usage_alert') {
            get().toast(`Spend ${ev.data.period === 'daily' ? 'today' : 'this month'} is $${ev.data.spent.toFixed(2)}, over your $${ev.data.limit.toFixed(2)} alert`, 'error')
          } else if (ev.event === 'desk_status') {
            onDeskChanged(ev.data)
          } else if (ev.event === 'preview') {
            const data = ev.data
            void import('./features/docrec/preview').then((m) => m.usePreview.getState().apply(data))
          } else if (ev.event === 'run_state') {
            const info = ev.data
            set((st) => ({ liveRuns: foldRunState(st.liveRuns, info) }))
            const sess = get().sessions[info.conversation_id]
            // A reply this window did not start: follow it, or, once it ends, read what it persisted.
            if (sess && sess.streaming?.runId !== info.run_id) {
              if (info.answering) void get().attachSession(info.conversation_id).catch(() => undefined)
              else if (!sess.streaming) void get().openSession(info.conversation_id).catch(() => undefined)
            }
          } else if (ev.event === 'conversation_changed') {
            applyTitle(ev.data.id, ev.data.title)
          } else if (ev.event === 'recording') {
            // Lazy: the docrec store imports this one, so a static import here would be a cycle.
            const data = ev.data
            void import('./features/docrec/store').then((m) => m.useDocRec.getState().handleEvent(data))
          }
        }
      } catch {
        // A dropped or refused connection is normal here (backend restart, sleep); just retry.
      }
      await new Promise((r) => setTimeout(r, backoff))
      backoff = Math.min(backoff * 2, 30000)
    }
  }

  /**
   * The whole segment tail, cursor reset. A `?since=` poll is keyed on rowid and an UPDATE does not
   * move one, so a segment whose TEXT changed — exactly what retranscribe does — is never
   * re-delivered incrementally. Opening a meeting and replaying one both have to reload.
   */
  const loadSegments = async (meetingId: string): Promise<void> => {
    // Paged, because one request is capped: an hour on two channels at the default 20s clips is
    // ~360 rows, and a single `?since=0` page would show the first half of the call and stop
    // mid-sentence with nothing on screen saying so.
    const page = await fetchSegmentPages((since, limit) => api.meetings.segments(meetingId, since, limit)).catch(() => null)
    if (page === null || get().activeMeeting?.id !== meetingId) return
    set({ meetingSegments: page.segments, meetingCursor: page.cursor })
  }

  /**
   * Keystrokes aimed at the meeting being left behind.
   *
   * A switch is two round trips long and the notepad accepts input for the whole of it, so the
   * flush at the top of `openMeeting`/`startRecording` is not the last word: without this second
   * flush the unconditional `meetingNotesDraft: null` that follows eats everything typed since the
   * click. Returns the saved row when it flushed one, so the caller can adopt its notes.
   */
  const flushOutgoing = async (outgoing: string | null): Promise<FullMeeting | null> => {
    if (outgoing === null || get().activeMeeting?.id !== outgoing || get().meetingNotesDraft === null) return null
    await get().flushMeetingNotes()
    const after = get().activeMeeting
    return after?.id === outgoing ? after : null
  }

  /**
   * The 5s tick that runs while anything is still settling. The Meetings view polls the segment tail
   * itself, so this exists for every OTHER view: the sidebar indicator's clock, and a meeting whose
   * transcript and enhance pass land minutes after Stop returned.
   */
  const liveTick = async (): Promise<void> => {
    await get().refreshMeetingStatus()
    const m = get().activeMeeting
    // The post-stop window counts as settling: the row is already `ready` while the enhance pass
    // and the last transcriptions are still landing on it.
    if (!m || !(SETTLING.includes(m.status) || Date.now() < meetingSettleUntil)) return
    // Never mid-autosave: a read that lands between the PUT and its merge-back would put the
    // pre-save notes back on screen and the next keystroke would diff against them.
    if (get().meetingSaving) return
    const fresh = await api.meetings.get(m.id).catch(() => null)
    if (fresh && get().activeMeeting?.id === fresh.id) {
      set({ activeMeeting: fresh })
      // A clip that transcribed after Stop kept its rowid, so `meetingCursor` will never re-deliver
      // it: the tail has to be reloaded whole or those words never appear.
      if (needsSegmentReload(get().meetingSegments, fresh.segment_count)) await loadSegments(fresh.id)
    }
    void get().refreshMeetingsPending()
  }

  /** One stream event folded into a session: the reducer plus the approval recount and the status verdict. */
  const step = (s: ChatSession, ev: ChatEvent, visible: boolean, seq: number | null): ChatSession => {
    const next = applyEvent(s, ev, visible, seq)
    if (next === s) return s
    // Only the events that open or settle a gate can move the count, and delta must stay free
    // of any recount: it is the one event that arrives per token.
    const gate = ev.event === 'tool_call' || ev.event === 'tool_result'
      || ev.event === 'plan_card' || ev.event === 'plan_decision'
    const pendingApprovals = gate ? countApprovals(next.conversation) : s.pendingApprovals
    return { ...next, pendingApprovals, status: reduceStatus(s.status, ev, pendingApprovals) }
  }

  /**
   * Tell the user a chat they are not looking at needs them, at most once per run and kind. Not for a desk's
   * conversation (the desk notifier owns those), and not while the window is in front with the chat on screen.
   */
  const announce = (convId: string, runId: string, kind: 'reply' | 'approval' | 'failed', visible: boolean, seen: Set<string>): void => {
    const key = `${runId}:${kind}`
    if (seen.has(key)) return
    seen.add(key)
    if (get().settings.chatNotify === false) return
    if (visible && typeof document !== 'undefined' && document.hasFocus()) return
    if (get().desks.some((d) => d.conversation_id === convId)) return
    const title = get().sessions[convId]?.conversation.title || get().conversations.find((c) => c.id === convId)?.title || 'Chat'
    notify(title.length > 60 ? title.slice(0, 57) + '…' : title, CHAT_NOTICE_BODY[kind], { tag: key, onClick: () => void get().selectChat(convId) })
  }

  /**
   * Consume one run's events into a session. `attached` means the run was started by someone else.
   *
   * `replay` is an attach that starts at the in-flight message: that message is blanked once, the tape up to
   * `end` is buffered and folded in a single patch (so the window never paints a half-built reply), and the
   * replayed events raise no toast, hold or refetch. A replayed `title` is dropped: the fetched row is newer.
   */
  const watchRun = async (convId: string, run: ChatRunStarted, from: { messageId: string | null; approvals: number; attached: boolean; pendingKey?: number; replay?: { messageId: string | null; end: number } }): Promise<void> => {
    // One subscription per conversation. A second subscription to the same run would apply every
    // delta twice, since `applyEvent` appends. A different run supersedes this one, so its viewer is
    // detached first: the old loop's `finally` is abort-identity guarded and will not undo us.
    const prev = get().sessions[convId]?.streaming
    if (prev?.runId === run.run_id) return
    prev?.abort.abort()
    const abort = new AbortController()
    const attached = from.attached
    const replay = from.replay
    // Set by `desk_handoff`: this turn announced a successor before it ended, so the stream closing
    // is not the end of the desk's work and the pane re-attaches rather than going idle.
    let handoff: { desk_id: string; conversation_id: string; turn: number } | null = null
    clearHold(convId)
    // Set by the reply's final `done`: what is stored is then what this window already has. A stream that
    // kept closing without ever delivering one leaves it false, and what is stored is then the truth.
    let settled = false
    patchSession(convId, (s) => {
      // Rebuilt from the tape, so what the fetch held of the in-flight message (possibly all of it, possibly a stale
      // half) is cleared first. After the dedupe guard above: blanking a message a live watcher is filling would lose it.
      const blank = replay?.messageId ?? null
      const conversation = blank
        ? { ...s.conversation, messages: (s.conversation.messages ?? []).map((m) => (m.id === blank ? { ...m, content: '', reasoning: null, tool_events: [] } : m)) }
        : s.conversation
      const approvals = blank ? countApprovals(conversation) : from.approvals
      return {
        ...s,
        conversation,
        streaming: { messageId: from.messageId, runId: run.run_id, abort, answering: true, seq: run.seq, stopping: false },
        status: settleApprovals('working', approvals),
        finishedAt: null,
        pendingApprovals: approvals,
        touchedAt: Date.now(),
        runError: null
      }
    })
    // Streamed text is applied at most once per interval. Events past the replay boundary only: the replay
    // folds the tape in one patch already. Applied without a seq, which is safe because every other event
    // flushes this first, so nothing with a seq can overtake a pending delta.
    const buf = createDeltaBuffer((ev) => {
      const visible = onScreen(convId, { view: get().view, focusedId: get().focusedConversationId, retained })
      patchSession(convId, (s) => (s.streaming?.abort !== abort ? s : step(s, ev, visible, null)))
    })
    // Once per run and kind: a run that asks for approval twice rings once for it, and its ending rings once more.
    const notified = new Set<string>()
    let backlog: { ev: ChatEvent; seq: number | null }[] | null = replay ? [] : null
    const flush = (): void => {
      const events = backlog
      backlog = null
      if (!events?.length) return
      // Abort-identity guarded like the `finally` below: a run that superseded this one mid-replay owns the session now.
      patchSession(convId, (s) => (s.streaming?.abort !== abort ? s : events.reduce((acc, e) => (e.ev.event === 'title' ? acc : step(acc, e.ev, true, e.seq)), s)))
    }
    // The events that move something other than the transcript, so a replay still has to run them.
    const track = (ev: ChatEvent): void => {
      switch (ev.event) {
        case 'plan':
          set((st) => ({ plans: { ...st.plans, [convId]: ev.data.steps } }))
          break
        case 'desk_status':
          putDesk(ev.data)
          if (ev.data.status === 'review') void get().loadDeskFiles(ev.data.id)
          if (NEEDS_YOU.includes(ev.data.status)) void get().refreshDeskInbox()
          break
        case 'desk_handoff':
          handoff = ev.data
          break
      }
    }
    try {
      for await (const ev of chatStream(convId, run.seq, abort.signal, run.run_id)) {
        // A frame with no usable body has nothing to apply; ignoring it beats throwing inside the loop.
        if (!ev || !isRecord(ev.data)) continue
        const seq = ev.seq
        if (ev.event === 'done' && !ev.data.segment) settled = true
        if (backlog && replay && seq !== null && seq > replay.end) flush()
        if (backlog) {
          backlog.push({ ev, seq })
          track(ev)
          if (seq === null || (replay && seq >= replay.end)) flush()
          continue
        }
        if (ev.event === 'delta' || ev.event === 'reasoning') {
          buf.push(ev)
          continue
        }
        buf.flush()
        const focused = onScreen(convId, { view: get().view, focusedId: get().focusedConversationId, retained })
        const before = get().sessions[convId]?.status ?? 'idle'
        patchSession(convId, (s) => step(s, ev, focused, seq))
        const kind = chatNotice(before, get().sessions[convId]?.status ?? before, ev)
        if (kind) announce(convId, run.run_id, kind, focused, notified)
        switch (ev.event) {
          case 'done':
            if (!ev.data.error) hold(convId)
            // Retried silently on the way (a provider hiccup before the first token): worth saying once, after the fact.
            if (ev.data.notice) get().toast(ev.data.notice, 'info')
            void get().refreshConversations()
            break
          case 'restored_message':
            if (ev.data.reason) get().toast(`Regenerate failed: ${ev.data.reason}. The previous answer is back.`, 'error')
            break
          // Only the `remember` tool reaches here now; auto-learn reports on `/events` instead.
          case 'learned': {
            const { memories, nodes, edges, updated = [], removed = [] } = ev.data
            const parts = [`Learned ${memories.length} memor${memories.length === 1 ? 'y' : 'ies'}`]
            if (updated.length) parts.push(`updated ${updated.length}`)
            if (removed.length) parts.push(`forgot ${removed.length}`)
            parts.push(`${nodes.length} entities, ${edges.length} relations`)
            get().toast(parts.join(', '), 'learned')
            if (memories.length + updated.length + removed.length + nodes.length + edges.length) refreshAll()
            break
          }
          case 'style_learned':
            // A banked sample is quiet; a refreshed voice profile is worth saying once.
            if (ev.data.profile) get().toast('Updated how you write', 'learned')
            void get().refreshStyle()
            break
          case 'learn_error':
            get().toast(`Auto-learn failed: ${ev.data.message}`, 'error')
            break
          case 'error': {
            // A reply row on screen carries the error itself; the toast is for a chat nobody is looking at.
            // The reducer leaves `streaming` in place on an error, so the row it stamped is still findable here.
            const s = get().sessions[convId]
            const answeredHere = !!s?.streaming?.messageId && (s.conversation.messages ?? []).some((m) => m.id === s.streaming?.messageId)
            if (!focused || !answeredHere) get().toast(ev.data.message, 'error')
            break
          }
          default:
            track(ev)
        }
      }
    } catch (e) {
      // An aborted signal is the user pressing Stop, not a failure.
      if (!abort.signal.aborted) {
        buf.flush()
        const stalled = e instanceof ApiError && e.kind === 'stalled'
        const s0 = get().sessions[convId]
        const mine = s0?.streaming?.abort === abort
        // After the reply's `done` only the auto-learn tail was lost, which leaves nothing to settle.
        const live = mine && !!s0?.streaming?.answering
        const bubble = live && !!s0?.streaming?.messageId && (s0.conversation.messages ?? []).some((m) => m.id === s0.streaming?.messageId)
        const lost = 'Interrupted: lost the connection to the backend while this reply was streaming.'
        if (live) {
          patchSession(convId, (s) => {
            if (s.streaming?.abort !== abort) return s
            const settled = settleInterrupted(s, lost)
            return { ...settled, status: 'error', runError: bubble ? s.runError : { message: lost, runId: run.run_id, interrupted: true } }
          })
        }
        const visible = onScreen(convId, { view: get().view, focusedId: get().focusedConversationId, retained })
        if (!live || !bubble || !visible) {
          // After `done` the reply is whole, so there is nothing to continue: only the auto-learn tail was lost.
          const text = !stalled ? (e as Error).message
            : live ? 'The backend stopped responding. Restart it from Settings > Support, then continue the reply.'
              : 'The backend stopped responding. Restart it from Settings > Support.'
          get().toast(text, 'error')
        }
        if (live) announce(convId, run.run_id, 'failed', visible, notified)
      }
    } finally {
      // Backstop: a stream that dies before `user_message` must not leave the dimmed bubble behind.
      if (from.pendingKey !== undefined) dropPending(convId, from.pendingKey)
      buf.flush()
      flush()
      // A stream that ended without its `done` is not a finished reply. When the run itself says it died, the
      // transcript shows that here instead of waiting on the refetch below to find out.
      if (!settled && !abort.signal.aborted && get().sessions[convId]?.streaming?.abort === abort && get().sessions[convId]?.streaming?.answering) {
        const state = await api.runState(run.run_id).catch((err: unknown) => (err instanceof ApiError && err.status === 404 ? 'gone' as const : null))
        const dead = state === 'gone' || (!!state && (state.status === 'interrupted' || state.status === 'error'))
        if (dead) {
          const msg = 'Interrupted: the reply ended before it finished.'
          patchSession(convId, (s) => {
            if (s.streaming?.abort !== abort) return s
            const held = !!s.streaming.messageId && (s.conversation.messages ?? []).some((m) => m.id === s.streaming?.messageId)
            const settledS = settleInterrupted(s, msg)
            return { ...settledS, status: 'error', runError: held ? s.runError : { message: msg, runId: run.run_id, interrupted: true } }
          })
          // Settled here rather than by an `error` frame, so the one notice that frame would have raised is raised here.
          if (get().sessions[convId]?.streaming?.abort === abort) {
            announce(convId, run.run_id, 'failed', onScreen(convId, { view: get().view, focusedId: get().focusedConversationId, retained }), notified)
          }
        }
      }
      patchSession(convId, (s) => (s.streaming?.abort === abort ? { ...s, streaming: null, status: finishStatus(s.status) } : s))
      // An attached run wrote deltas this window never saw, and a stream that ended without its `done` left
      // the reply unfinished here: the persisted message is the whole reply.
      if ((attached || !settled) && !abort.signal.aborted && get().sessions[convId]) void get().openSession(convId).catch(() => undefined)
      // A chained desk turn is a *new* run on this same conversation, and the stream for the old one
      // closes before the bus has registered it. Poll a few times rather than leave the pane dead.
      // `attachSession` is shared and this dedupes on run_id, so every extra attempt is a no-op.
      if (handoff && !abort.signal.aborted) {
        for (let i = 0; i < 6 && !get().sessions[convId]?.streaming; i++) {
          await new Promise((r) => setTimeout(r, 250))
          // Swallowed: this runs in a `finally` nobody awaits, so a failed re-attach must not
          // surface as an unhandled rejection. The next `openDesk` recovers the pane.
          await get().attachSession(convId).catch(() => undefined)
        }
      }
    }
  }

  /**
   * `false` means the backend never accepted `body`, so the caller still owns the text it sent.
   * Resolves on that verdict, not at the end of the run: a composer is holding a draft on it.
   */
  const runStream = async (convId: string, body: { content?: string; model?: string; page_context?: PageContext; replace_from?: string }, pendingKey?: number): Promise<boolean> => {
    let run: ChatRunStarted
    try {
      run = await api.chat(convId, body)
    } catch (e) {
      const conflict = runConflict(e)
      if (!conflict) {
        get().toast((e as Error).message, 'error')
        patchSession(convId, (s) => ({ ...s, status: 'error', finishedAt: Date.now() }))
        return false
      }
      // Another window is already mid-reply. Adopt that run, replaying its in-flight message so the window
      // paints the whole reply, and steer the message into it instead of dropping it.
      await get().attachSession(convId).catch(() => undefined)
      if (body.replace_from) {
        // An edit cannot be folded into a live reply: it replaces history, so it is refused whole.
        get().toast('That chat is already replying — your edit was not sent.', 'error')
        return false
      }
      if (body.content) {
        try {
          const r = await api.steer(convId, body.content)
          settleSteer(convId, r.message)
          return true
        } catch (e) {
          get().toast(e instanceof ApiError && e.kind === 'timeout' ? 'The backend did not answer, so your message was not sent.' : 'That chat is already replying — your message was not sent.', 'error')
        }
      }
      return false
    }
    // Synchronous up to its first await, so `streaming` is set before this returns.
    void watchRun(convId, run, { messageId: null, approvals: 0, attached: false, pendingKey })
    return true
  }

  /**
   * One PATCH at a time per conversation, in call order, each applying the row the server answered
   * with — so the store ends equal to the last write and two quick changes cannot overwrite each
   * other. Rejects on failure: the taint mark in `noteUntrustedUpload` depends on that.
   */
  const writeConversation = (id: string, body: { model?: string; settings?: Partial<ConversationSettings> }): Promise<Conversation> => {
    const prev = convWrites.get(id)
    const run = async (): Promise<Conversation> => {
      await prev?.catch(() => undefined)
      const c = await api.conversations.patch(id, body)
      patchConversation(id, (cur) => ({ ...cur, model: c.model, settings: c.settings }))
      return c
    }
    const p = run()
    convWrites.set(id, p)
    void p.catch(() => undefined).then(() => { if (convWrites.get(id) === p) convWrites.delete(id) })
    return p
  }

  /** Run an action whose failure should be told to the user and nothing more. */
  const guard = async (label: string, fn: () => Promise<unknown>): Promise<boolean> => {
    try {
      await fn()
      return true
    } catch (e) {
      get().toast(`${label}: ${(e as Error).message}`, 'error')
      return false
    }
  }

  /** A chat the backend no longer has: drop the session, the sidebar row and the Home row. No network call. */
  const forgetChat = (id: string): void => {
    get().closeSession(id)
    set((st) => ({
      conversations: st.conversations.filter((c) => c.id !== id),
      dashboard: st.dashboard && { ...st.dashboard, recent_conversations: st.dashboard.recent_conversations.filter((c) => c.id !== id) }
    }))
  }

  const patchChatSettings = async (patch: Partial<ConversationSettings>, conversationId?: string): Promise<void> => {
    const id = conversationId ?? get().focusedConversationId
    if (!id) {
      // No conversation to PATCH yet. Effort and fast mode are the settings a draft can still carry,
      // so park them and let `send` apply them to the conversation it is about to create.
      set((s) => ({
        draftEffort: patch.effort ?? s.draftEffort,
        draftFast: patch.fast ?? s.draftFast
      }))
      return
    }
    await writeConversation(id, { settings: patch })
  }

  return {
    ready: false,
    backendError: null,
    backendState: 'ready',
    settings: { baseUrl: '', apiKey: '', apiKeySet: false, defaultModel: '', systemPrompt: '', extractionModel: '', autoLearn: true, autoTitle: true, learnStyle: true, theme: 'dark', accent: 'sage', gatherShortcut: '', quickCaptureShortcut: '', dictationChord: '', tools: {}, maxToolRounds: 8, braveApiKey: '', tavilyApiKey: '', googleClientId: '', googleClientSecret: '', modelPrices: {} },
    models: [],
    modelsError: null,
    tools: [],
    google: null,
    tasksSync: null,
    todoCalendar: null,
    dashboard: null,
    todos: [],
    recap: null,
    recapLoading: false,
    agentInbox: null,
    jobs: [],
    projects: [],
    personalStats: undefined,
    view: 'home',
    lastClassicView: 'home',
    memoryMode: 'split',
    projectViewId: null,
    draftProjectId: null,
    draftEffort: DEFAULT_EFFORT,
    draftModel: null,
    draftFast: false,
    draftPendingSend: null,
    uploadTaintTarget: null,
    uploadTaintSource: 'upload',
    libraryScope: 'all',
    dataScope: 'all',
    docs: [],
    activeDoc: null,
    docTabs: [],
    docRevisions: [],
    docFolders: [],
    expandedFolders: readExpanded(),
    docsPending: 0,
    docMode: readDocMode(),
    docDraft: null,
    docTitleDraft: null,
    docSaving: false,
    meetings: [],
    activeMeeting: null,
    meetingStatus: null,
    meetingSegments: [],
    meetingCursor: 0,
    meetingPreflight: null,
    meetingsPending: 0,
    meetingQuery: '',
    meetingNotesDraft: null,
    meetingSaving: false,
    meetingBusy: false,
    meetingConsentOpen: false,
    sidebarOpen: true,
    sidebarSearchTick: 0,
    contextOpen: false,
    contextTab: 'last',
    libraryTab: 'skills',
    desks: [],
    activeDeskId: null,
    activeDesk: null,
    deskFiles: [],
    deskPreview: null,
    deskInbox: [],
    deskBusy: false,
    deskShowArchived: false,
    pageAgentOpen: false,
    pageAgentId: null,
    pageAgentModel: null,
    pageAgentEffort: DEFAULT_EFFORT,
    pageAgentFast: false,
    pageContext: null,
    traceMessageId: null,
    settingsOpen: false,
    settingsTab: 'provider',
    knowledgeTab: 'memory',
    projectModal: null,
    toasts: [],
    conversations: [],
    sessions: {},
    liveRuns: {},
    focusedConversationId: null,
    memories: [],
    graph: { nodes: [], edges: [] },
    documents: [],
    plans: {},
    skills: [],
    style: null,
    styleSamples: [],
    styleLearning: false,

    activity: null,
    activityEvents: [],
    activitySummaries: [],
    activityContext: null,
    activityBusy: false,
    activityInsights: null,
    activityInsightsBusy: false,

    init: async () => {
      // Before the backend check and before the guard: a dead backend must still leave the menu
      // shortcuts wired, and StrictMode's second mount must not add a second listener.
      wireMenu()
      if (inited) return
      inited = true
      if (!stateWired && typeof window.os.onBackendState === 'function') {
        stateWired = true
        window.os.onBackendState(onBackendState)
      }
      const status = await window.os.backendStatus()
      // A failed start must stay retryable from the error screen's "Try again".
      if (!status.url) { inited = false; return set({ ready: true, backendError: status.error ?? 'Backend not running' }) }
      setBase(status.url)
      try {
        await api.health()
      } catch (e) {
        inited = false
        return set({ ready: true, backendError: status.error ?? (e as Error).message })
      }
      let settings: Settings, projects: Project[], personalStats: Project['stats'], conversations: Conversation[]
      try {
        ;[settings, projects, personalStats, conversations] = await Promise.all([
          api.settings.get(), api.projects.list(), api.projects.globalStats().catch(() => undefined), api.conversations.list('all')
        ])
      } catch (e) {
        // Release the guard so a retry can run, and surface the failure instead of an empty window.
        inited = false
        return set({ ready: true, backendError: 'The backend is running, but your data could not be loaded: ' + (e as Error).message })
      }
      // Quiet while the backend restarts: the banner already says so, and every refresher would fail at once.
      installRejectionToasts((m, kind) => { if (get().backendState === 'ready') get().toast(m, kind) })
      // One-shot migration of the pre-spaces global mode: a user who left the app in canvas mode lands
      // in the canvas once, and the setting is reset so later launches open on Today. Only the main
      // window writes it back; a pop-out (`?surface=widget`) never renders App and must not touch settings.
      const { mode: legacyMode } = settings
      const legacyCanvas = legacyMode === 'canvas'
      set({ settings: withoutLegacyMode(settings), view: legacyCanvas ? 'canvas' : 'home', projects, personalStats, conversations, ready: true })
      if (legacyCanvas && !isPopout()) void get().saveSettings({ mode: 'classic' }).catch(() => undefined)
      void get().loadModels()
      void get().loadScope('all')
      void api.tools().then((t) => set({ tools: t.tools })).catch(() => undefined)
      void get().refreshDashboard()
      void get().refreshTodos()
      void get().refreshRecap()
      void get().refreshDocsPending()
      void get().refreshActivity()
      // One watcher per app: a pop-out would only duplicate every toast in another window.
      if (!isPopout() && !watching) {
        watching = true
        void watchBackgroundEvents()
      }
      loadedOnce = true
      // The sidebar's needs-you badge and the Today card read this; without it they stay empty until
      // Cowork or Home happens to mount.
      void get().refreshDeskInbox()
      // Both for the sidebar: the review badge, and the indicator that says a recording is running.
      // `refreshMeetingStatus` also starts the live tick, so a meeting a crash left running is visible.
      void get().refreshMeetingsPending()
      void get().refreshMeetingStatus()
    },

    restartBackend: async () => {
      set({ backendState: 'restarting' })
      onBackendState(await window.os.restartBackend())
    },

    loadModels: async () => {
      try {
        set({ models: await api.models(), modelsError: null })
      } catch (e) {
        set({ models: [], modelsError: (e as Error).message })
      }
    },
    saveSettings: async (patch) => {
      set({ settings: withoutLegacyMode(await api.settings.set(patch)) })
      if ('baseUrl' in patch || 'apiKey' in patch) void get().loadModels()
      if ('googleClientId' in patch || 'googleClientSecret' in patch) void get().refreshGoogle()
    },
    setView: (view) => {
      const cur = get().view
      // Leaving the editor must not drop what is still in the buffer.
      if (cur === 'docs' && view !== 'docs') void get().flushDoc()
      if (cur === 'meetings' && view !== 'meetings') void get().flushMeetingNotes()
      if (view === 'canvas' && cur !== 'canvas') set({ view, lastClassicView: cur })
      else set({ view })
      // Coming back to the chat view shows the focused conversation, so what it finished while away is read.
      if (view === 'chat') {
        const fid = get().focusedConversationId
        if (fid && (get().sessions[fid]?.unread ?? 0) > 0) get().clearSessionStatus(fid)
      }
      if (view === 'docs') {
        void get().refreshDocs()
        void get().refreshDocsPending()
      }
      if (view === 'meetings') {
        void get().refreshMeetings()
        void get().refreshMeetingStatus()
        void get().refreshMeetingsPending()
      }
      if (view === 'home') void get().refreshDashboard()
      if (view === 'todos') void get().refreshTodos()
      if (view === 'activity') void get().loadActivity()
    },
    leaveCanvas: () => {
      const s = get()
      const v = s.lastClassicView
      // The target can have gone while in the canvas: its project deleted, or the view hidden in Settings.
      const gone = v === 'project' ? !s.projectViewId || !s.projects.some((p) => p.id === s.projectViewId) : viewHidden(s.settings, v)
      s.setView(gone ? 'home' : v)
    },
    setMemoryMode: (memoryMode) => set({ memoryMode }),
    openMemory: (memoryMode) => {
      if (memoryMode) set({ memoryMode })
      get().openSettings('knowledge', 'memory')
    },
    toggleSidebar: () => set((s) => ({ sidebarOpen: !s.sidebarOpen })),
    toggleContext: () => set((s) => ({ contextOpen: !s.contextOpen })),
    togglePageAgent: () => set((s) => ({ pageAgentOpen: !s.pageAgentOpen })),
    closePageAgent: () => set({ pageAgentOpen: false }),
    resetPageAgent: () => {
      const id = get().pageAgentId
      const convo = id ? get().sessions[id]?.conversation : undefined
      // The thread stays in the chat list — the panel is a way in, not a scratchpad that eats history.
      if (id) get().closeSession(id)
      set({
        pageAgentId: null,
        ...(convo ? {
          pageAgentModel: convo.model,
          pageAgentEffort: convo.settings.effort,
          pageAgentFast: !!convo.settings.fast
        } : {})
      })
    },
    setPageAgentModel: async (model) => {
      set({ pageAgentModel: model })
      const id = get().pageAgentId
      if (!id) return
      try {
        await writeConversation(id, { model })
      } catch (e) {
        get().toast((e as Error).message, 'error')
      }
    },
    setPageAgentParams: async (patch) => {
      set((s) => ({
        pageAgentEffort: patch.effort ?? s.pageAgentEffort,
        pageAgentFast: patch.fast ?? s.pageAgentFast
      }))
      const id = get().pageAgentId
      if (!id) return
      try {
        await writeConversation(id, { settings: patch })
      } catch (e) {
        get().toast((e as Error).message, 'error')
      }
    },
    setPageContext: (pageContext) => set((s) => (s.pageContext === pageContext ? {} : { pageContext })),
    setContextTab: (contextTab) => set({ contextTab }),
    openTrace: (traceMessageId) => set({ traceMessageId, contextTab: 'trace', contextOpen: true }),
    // A plain open (⌘, or the sidebar button) starts on Provider, as it always has.
    setSettingsOpen: (settingsOpen) => set(settingsOpen ? { settingsOpen, settingsTab: 'provider' } : { settingsOpen }),
    openSettings: (settingsTab, knowledgeTab) => set(knowledgeTab ? { settingsOpen: true, settingsTab, knowledgeTab } : { settingsOpen: true, settingsTab }),
    setKnowledgeTab: (knowledgeTab) => set({ knowledgeTab }),
    setProjectModal: (projectModal) => set({ projectModal }),
    toast: (text, kind = 'info', action) => {
      const id = ++toastSeq
      set((s) => ({ toasts: [...s.toasts, { id, text, kind, action }] }))
      setTimeout(() => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })), action ? UNDO_MS : kind === 'error' ? 6000 : 3500)
    },
    offerUndo: (what, items, note) => {
      get().toast(`Deleted ${what}${note ? `. ${note}` : ''}`, 'info', { label: 'Undo', run: () => void get().restoreTrashed(items) })
    },
    restoreTrashed: async (items) => {
      try {
        const out = await Promise.all(items.map((i) => api.trash.restore(i.type, i.id)))
        await Promise.all([get().refreshProjects(), get().refreshConversations(), get().refreshDocs(), get().refreshDocsPending(),
                           get().refreshMemories(), get().refreshDocuments(), get().refreshTodos(), get().refreshDashboard()])
        if (out.some((o) => o.moved_to_personal)) get().toast('Restored to Personal: its project is still in the trash')
      } catch (e) {
        get().toast((e as Error).message, 'error')
      }
    },

    refreshProjects: async () => {
      const [projects, personalStats] = await Promise.all([api.projects.list(), api.projects.globalStats().catch(() => undefined)])
      set({ projects, personalStats })
    },
    // `draftProjectId` is deliberately not set here: opening a project is looking at it, not
    // choosing it for the next chat. Its own "New chat" buttons pass the id to `newChat` instead.
    openProject: (id) => set({ view: 'project', projectViewId: id, draftProjectId: null, settingsOpen: false }),
    createProject: async (p) => {
      const project = await api.projects.create(p)
      await get().refreshProjects()
      get().openProject(project.id)
    },
    updateProject: async (id, patch) => {
      await api.projects.update(id, patch)
      await get().refreshProjects()
    },
    deleteProject: async (id) => {
      const name = get().projects.find((p) => p.id === id)?.name
      const res = await api.projects.delete(id)
      // Each chat of the project loses its viewer too: the backend has stopped their replies.
      for (const [cid, x] of Object.entries(get().sessions)) if (x.conversation.project_id === id) get().closeSession(cid)
      set((s) => {
        const sessions = Object.fromEntries(Object.entries(s.sessions).filter(([, x]) => x.conversation.project_id !== id))
        const fid = s.focusedConversationId
        return {
          view: s.view === 'project' && s.projectViewId === id ? 'chat' : s.view,
          // Deleted from the sidebar while in the canvas: ⌘⇧C must not return to its page.
          lastClassicView: s.lastClassicView === 'project' && s.projectViewId === id ? 'chat' : s.lastClassicView,
          projectViewId: s.projectViewId === id ? null : s.projectViewId,
          draftProjectId: s.draftProjectId === id ? null : s.draftProjectId,
          sessions,
          focusedConversationId: fid && s.sessions[fid] && !sessions[fid] ? null : fid
        }
      })
      await Promise.all([get().refreshProjects(), get().refreshConversations(), get().refreshDocs(), get().refreshTodos()])
      get().offerUndo(name ? `project “${name}”` : 'project', [{ type: 'project', id }], res?.stopped ? 'Reply stopped.' : undefined)
    },

    setLibraryScope: async (libraryScope) => {
      set({ libraryScope })
      await get().loadScope(libraryScope)
    },
    loadScope: async (dataScope) => {
      set({ dataScope })
      await Promise.all([get().refreshMemories(), get().refreshGraph(), get().refreshDocuments(), get().refreshDocs(),
                         get().refreshStyle().catch(() => undefined)])
    },

    refreshConversations: async () => set({ conversations: await api.conversations.list('all') }),
    newChat: (projectId = null) => set({ focusedConversationId: null, draftProjectId: projectId, draftEffort: DEFAULT_EFFORT, draftModel: null, draftFast: false, view: 'chat', settingsOpen: false }),
    createConversation: async (projectId) => {
      try {
        const c = await api.conversations.create(projectId, get().settings.defaultModel)
        c.messages = []
        putSession(c)
        set((s) => ({ conversations: [c, ...s.conversations.filter((x) => x.id !== c.id)] }))
        void get().refreshProjects()
        return c
      } catch (e) {
        get().toast((e as Error).message, 'error')
        return null
      }
    },
    selectChat: async (id) => {
      set({ view: 'chat', settingsOpen: false, traceMessageId: null })
      if (!id) return set({ focusedConversationId: null })
      set({ focusedConversationId: id })
      get().clearSessionStatus(id)
      // A session mid-run holds content the backend has not persisted yet, so never refetch over it.
      if (get().sessions[id]?.streaming) return
      try {
        await get().attachSession(id)
      } catch (e) {
        // Never reject: the caller is a click. A slow failure for a chat the user has left is theirs no longer.
        if (get().focusedConversationId !== id) return
        if (e instanceof ApiError && e.status === 404) {
          forgetChat(id)
          get().toast('That chat was deleted', 'error')
        } else if (!get().sessions[id]) {
          set({ focusedConversationId: null })
          get().toast(`Could not open chat: ${(e as Error).message}`, 'error', { label: 'Retry', run: () => void get().selectChat(id) })
        } else {
          get().toast(`Could not refresh chat: ${(e as Error).message}`, 'error')
        }
        return
      }
      const opened = get().sessions[id]
      if (get().focusedConversationId === id && opened) set({ draftProjectId: opened.conversation.project_id })
    },
    openSession: async (conversationId) =>
      share(loads, conversationId, async () => {
        putSession(await api.conversations.get(conversationId))
      }),
    attachSession: async (conversationId) =>
      share(attaches, conversationId, async () => {
        // A run started before this window existed: `GET /runs` is the only way it can know.
        const runs = await api.runs(conversationId).catch(() => null)
        // `answering`, not `live`: a run in its auto-learn tail has nothing left to stream, and
        // attaching to one would paint a caret and a Stop button over a reply `openSession` just
        // fetched whole.
        const run = runs?.find((r) => r.conversation_id === conversationId && r.answering)
        // Already watching this run: the `run_state` frame for a reply this window just started usually
        // lands before its POST returns, so this is the common case and not worth a transcript fetch.
        if (run && get().sessions[conversationId]?.streaming?.runId === run.run_id) return
        await get().openSession(conversationId)
        const s = get().sessions[conversationId]
        if (!run || !s) return
        // Checked again after the fetch (a desk hand-off retry, a widget mount): attaching to a run already
        // being watched would blank a live message.
        if (s.streaming?.runId === run.run_id) return
        // Replay from the in-flight message's own `assistant_message`, so the window shows the whole reply and any
        // approval card published before it arrived. Not awaited: `watchRun` only resolves when the run ends, and
        // this promise gates the dedupe.
        const replay = run.message_seq != null ? { messageId: run.message_id, end: run.seq } : undefined
        void watchRun(conversationId, { run_id: run.run_id, seq: replay ? replayCursor(run) : run.seq }, { messageId: run.message_id, approvals: countApprovals(s.conversation), attached: true, replay })
      }),
    closeSession: (conversationId) => {
      clearHold(conversationId)
      get().sessions[conversationId]?.streaming?.abort.abort()
      set((s) => {
        const sessions = { ...s.sessions }
        delete sessions[conversationId]
        return { sessions, focusedConversationId: s.focusedConversationId === conversationId ? null : s.focusedConversationId }
      })
    },
    clearSessionStatus: (conversationId) => {
      clearHold(conversationId)
      patchSession(conversationId, (s) => {
        const settled = s.status === 'done' || s.status === 'error'
        return { ...s, unread: 0, touchedAt: Date.now(), status: settled ? 'idle' : s.status, finishedAt: settled ? null : s.finishedAt }
      })
    },
    deleteChat: async (id) => {
      const title = get().conversations.find((c) => c.id === id)?.title
      const res = await api.conversations.delete(id)
      get().closeSession(id)
      set((s) => ({ conversations: s.conversations.filter((c) => c.id !== id) }))
      void get().refreshProjects()
      get().offerUndo(title ? `chat “${title}”` : 'chat', [{ type: 'conversation', id }], res?.stopped ? 'Reply stopped.' : undefined)
    },
    pinChat: async (id, on) => {
      await guard(on ? 'Could not pin chat' : 'Could not unpin chat', async () => {
        const row = await api.conversations.patch(id, { pinned: on })
        set((st) => ({ conversations: st.conversations.map((c) => (c.id === id ? { ...c, pinned_at: row.pinned_at ?? null } : c)) }))
      })
    },
    archiveChat: async (id, on) => {
      const ok = await guard(on ? 'Could not archive chat' : 'Could not unarchive chat', () => api.conversations.patch(id, { archived: on }))
      if (!ok) return
      if (!on) return void get().refreshConversations().catch(() => undefined)
      get().closeSession(id)
      set((st) => ({ conversations: st.conversations.filter((c) => c.id !== id) }))
      void get().refreshProjects()
      get().toast('Archived chat', 'info', { label: 'Undo', run: () => void get().archiveChat(id, false) })
    },
    moveChat: async (id, projectId) => {
      // A 409 (live run, desk or job transcript) arrives as the server's message in the guard's toast.
      await guard('Could not move chat', async () => {
        const row = await api.conversations.patch(id, { project_id: projectId })
        const next = row.project_id ?? null
        patchConversation(id, (c) => ({ ...c, project_id: next }))
        set((st) => ({
          conversations: st.conversations.map((c) => (c.id === id ? { ...c, project_id: next } : c)),
          draftProjectId: st.focusedConversationId === id ? next : st.draftProjectId
        }))
        void get().refreshProjects()
      })
    },
    stepChat: (dir) => {
      const s = get()
      if (s.view === 'canvas') return
      const next = adjacentChatId(s.conversations, s.focusedConversationId, dir)
      if (next) void s.selectChat(next)
    },
    searchChats: () => set((s) => ({ sidebarOpen: true, sidebarSearchTick: s.sidebarSearchTick + 1 })),
    renameChat: async (id, title) => {
      const next = title.trim()
      if (!next) return
      const cur = get().sessions[id]?.conversation.title ?? get().conversations.find((c) => c.id === id)?.title
      if (next === cur) return
      // `update` does not touch updated_at, so the list order is unchanged and a local patch is the whole refresh.
      await guard('Could not rename chat', async () => {
        await api.conversations.patch(id, { title: next })
        patchConversation(id, (c) => ({ ...c, title: next }))
        set((st) => ({ conversations: st.conversations.map((c) => (c.id === id ? { ...c, title: next } : c)) }))
      })
    },
    retitleChat: async (id) => {
      await guard('Could not suggest a title', async () => {
        const c = await api.conversations.retitle(id)
        applyTitle(id, c.title)
      })
    },
    setChatModel: async (model, conversationId) => {
      const id = conversationId ?? get().focusedConversationId
      // A draft has no row yet: park the choice for `send`, as effort does, instead of changing the default.
      if (!id) return void set({ draftModel: model })
      try {
        await writeConversation(id, { model })
      } catch (e) {
        get().toast((e as Error).message, 'error')
      }
    },
    setChatConfig: async (change, conversationId) => {
      const id = conversationId ?? get().focusedConversationId
      if (!id) {
        set((st) => ({
          draftModel: change.model ?? st.draftModel,
          draftEffort: change.effort ?? st.draftEffort,
          draftFast: change.fast ?? st.draftFast
        }))
        return
      }
      const settings: Partial<ConversationSettings> = {}
      if (change.effort !== undefined) settings.effort = change.effort
      if (change.fast !== undefined) settings.fast = change.fast
      try {
        await writeConversation(id, { ...(change.model ? { model: change.model } : {}), ...(Object.keys(settings).length ? { settings } : {}) })
      } catch (e) {
        get().toast((e as Error).message, 'error')
      }
    },
    // Picker and toggle callers fire and forget, so a failure has to surface here. The two callers that
    // act on the outcome (a taint mark, plan mode) use `patchChatSettings` and handle the rejection.
    setChatSettings: async (patch, conversationId) => {
      try {
        await patchChatSettings(patch, conversationId)
      } catch (e) {
        get().toast((e as Error).message, 'error')
      }
    },
    noteUntrustedUpload: async (conversationId, pending = 'draft', source = 'upload') => {
      const id = conversationId && conversationId !== '\u0000page-agent' ? conversationId : undefined
      if (!id) {
        set({ uploadTaintTarget: pending, uploadTaintSource: source })
        return
      }
      const settings = get().sessions[id]?.conversation.settings
      const patch: Partial<ConversationSettings> = { tainted: true }
      if (settings) patch.taint_sources = [...new Set([...(settings.taint_sources ?? []), source])]
      await patchChatSettings(patch, id)
    },
    askAboutEmail: async (id, subject) => {
      get().newChat(null)
      await get().noteUntrustedUpload(undefined, 'draft', 'email')
      return get().send(emailAsk(id, subject))
    },

    send: async (text, conversationId) => {
      if (!text.trim()) return false
      // Checked first: an oversized send creates no chat and never reaches the steer-then-409 fallthrough.
      const tooLong = tooLongNotice(text.length, messageCharLimit(get().settings.contextWindow))
      if (tooLong) {
        get().toast(tooLong, 'error')
        return false
      }
      const id = conversationId ?? get().focusedConversationId
      // The bubble is on screen before the first await; every refusal below takes it back.
      const pend: PendingSend = { key: ++pendingKeySeq, text, at: Date.now() }
      if (id) {
        if (get().sessions[id]) addPending(id, pend)
        const fail = (): false => {
          dropPending(id, pend.key)
          return false
        }
        if (get().uploadTaintTarget === 'draft') {
          try {
            await get().noteUntrustedUpload(id)
            set({ uploadTaintTarget: null, uploadTaintSource: 'upload' })
          } catch (e) {
            get().toast((e as Error).message, 'error')
            return fail()
          }
        }
        // Mid-reply sends steer the run: the message lands in the conversation now and the model
        // drops the completion it was writing and answers the steer. Only a run that is still
        // *answering* can take one — in its auto-learn tail the loop is over, and a steer accepted
        // there would be stored and never replied to — so that tail takes an ordinary send instead.
        if (get().sessions[id]?.streaming?.answering) {
          try {
            const r = await api.steer(id, text)
            settleSteer(id, r.message)
            dropPending(id, pend.key)
            return true
          } catch (e) {
            // A steer that hung is not a run that ended: falling through would start a second request that hangs too.
            if (e instanceof ApiError && e.kind === 'timeout') {
              get().toast('The backend did not answer, so your message was not sent.', 'error')
              return fail()
            }
            // The run ended in the gap; fall through to a normal send.
          }
        }
        if (!get().sessions[id]) {
          try {
            await get().openSession(id)
          } catch (e) {
            get().toast((e as Error).message, 'error')
            return false
          }
          addPending(id, pend)
        }
        // A model or effort change made an instant ago is still in flight: the run reads the row.
        await convWrites.get(id)?.catch(() => undefined)
        return (await runStream(id, { content: text }, pend.key)) || fail()
      }
      // A second send while the draft's row is still being created (a quick follow-up, Enter then a
      // click on Send) used to take this branch too and make a second chat with a second run. It
      // waits for the first chat instead and goes into it as an ordinary follow-up or steer.
      if (draftCreate) {
        const cid = await draftCreate
        return cid ? get().send(text, cid) : false
      }
      set({ draftPendingSend: pend })
      let c: Conversation
      let created: (id: string | null) => void = () => undefined
      draftCreate = new Promise((r) => { created = r })
      try {
        c = await api.conversations.create(get().draftProjectId, get().draftModel ?? get().settings.defaultModel)
      } catch (e) {
        draftCreate = null
        created(null)
        set({ draftPendingSend: null })
        // `send` never rejects: a caller holding the user's draft needs a verdict, not an exception.
        get().toast((e as Error).message, 'error')
        return false
      }
      // Effort and fast mode chosen on the draft land before the first run, so they apply to this reply.
      const { draftEffort: effort, draftFast: fast, uploadTaintTarget, uploadTaintSource } = get()
      const settings: { effort?: Effort; fast?: boolean; tainted?: boolean; taint_sources?: string[] } = {}
      // Low is already what a new row hydrates to. Anything else, including the omit-the-field
      // choice, has to be written or the server would fill low back in.
      if (effort !== DEFAULT_EFFORT) settings.effort = effort
      if (fast) settings.fast = true
      const fromUpload = uploadTaintTarget === 'draft'
      if (fromUpload) {
        settings.tainted = true
        settings.taint_sources = [uploadTaintSource || 'upload']
      }
      if (effort !== DEFAULT_EFFORT || fast || fromUpload) {
        const patched = await api.conversations.patch(c.id, { settings }).catch(() => null)
        if (fromUpload && !patched?.settings?.tainted) {
          draftCreate = null
          created(null)
          set({ draftPendingSend: null })
          get().toast('Could not mark this chat untrusted after the upload', 'error')
          return false
        }
        if (patched) c = patched
      }
      c.messages = []
      putSession(c)
      addPending(c.id, pend)
      // Listed now, not when the reply ends: the sidebar should show the chat you are in while it streams.
      const { messages: _m, ...row } = c
      set((s) => ({
        draftPendingSend: null,
        focusedConversationId: c.id, view: 'chat', draftEffort: DEFAULT_EFFORT, draftModel: null, draftFast: false,
        uploadTaintTarget: fromUpload ? null : uploadTaintTarget,
        uploadTaintSource: fromUpload ? 'upload' : uploadTaintSource,
        conversations: [row as Conversation, ...s.conversations.filter((x) => x.id !== c.id)]
      }))
      void get().refreshProjects()
      // Released once the run has started, so a waiting send sees it streaming and steers it.
      const ok = await runStream(c.id, { content: text }, pend.key)
      if (!ok) dropPending(c.id, pend.key)
      draftCreate = null
      created(c.id)
      return ok
    },
    sendToPageAgent: async (text) => {
      if (!text.trim()) return false
      const tooLong = tooLongNotice(text.length, messageCharLimit(get().settings.contextWindow))
      if (tooLong) {
        get().toast(tooLong, 'error')
        return false
      }
      const ctx = get().pageContext
      // The selection is read at send time, not when the view published itself: the user highlights
      // a paragraph and *then* reaches for ⌘I.
      const selection = ctx?.selection || currentSelection()
      const page = ctx ? { ...ctx, selection: selection || undefined } : undefined
      let id = get().pageAgentId
      if (id) {
        // Mid-reply the panel steers, exactly as the composer does in a chat. The auto-learn tail
        // is still `streaming` but no longer answering, and a steer there 409s.
        if (get().sessions[id]?.streaming?.answering) {
          try {
            await api.steer(id, text)
            return true
          } catch { /* the run ended in the gap */ }
        }
        // The thread can have been deleted from the chat list since; fall back to a fresh one.
        if (!get().sessions[id]) await get().openSession(id).catch(() => { id = null })
      }
      if (id && get().uploadTaintTarget === 'page') {
        try {
          await get().noteUntrustedUpload(id, 'page')
          set({ uploadTaintTarget: null, uploadTaintSource: 'upload' })
        } catch (e) {
          get().toast((e as Error).message, 'error')
          return false
        }
      }
      if (!id) {
        let c: Conversation
        try {
          c = await api.conversations.create(get().draftProjectId, get().pageAgentModel || get().settings.defaultModel)
        } catch (e) {
          get().toast((e as Error).message, 'error')
          return false
        }
        const { pageAgentEffort, pageAgentFast, uploadTaintTarget, uploadTaintSource } = get()
        const pageSettings: { effort?: Effort; fast?: boolean; tainted?: boolean; taint_sources?: string[] } = {}
        if (pageAgentEffort !== DEFAULT_EFFORT) pageSettings.effort = pageAgentEffort
        if (pageAgentFast) pageSettings.fast = true
        const fromUpload = uploadTaintTarget === 'page'
        if (fromUpload) {
          pageSettings.tainted = true
          pageSettings.taint_sources = [uploadTaintSource || 'upload']
        }
        if (pageAgentEffort !== DEFAULT_EFFORT || pageAgentFast || fromUpload) {
          const patched = await api.conversations.patch(c.id, { settings: pageSettings }).catch(() => null)
          if (fromUpload && !patched?.settings?.tainted) {
            get().toast('Could not mark this chat untrusted after the upload', 'error')
            return false
          }
          if (patched) c = patched
        }
        if (fromUpload) set({ uploadTaintTarget: null, uploadTaintSource: 'upload' })
        c.messages = []
        putSession(c)
        id = c.id
        set({ pageAgentId: id })
        void get().refreshProjects()
      }
      // The panel's model menu writes through the same queue as the chat page's: let a change land first.
      await convWrites.get(id)?.catch(() => undefined)
      return runStream(id, { content: text, page_context: page })
    },
    regenerate: async (conversationId) => {
      const id = conversationId ?? get().focusedConversationId
      if (!id || get().sessions[id]?.streaming?.answering) return
      if (!get().sessions[id]) {
        try {
          await get().openSession(id)
        } catch (e) {
          get().toast((e as Error).message, 'error')
          return
        }
      }
      await convWrites.get(id)?.catch(() => undefined)
      await runStream(id, {})
    },
    editAndResend: async (messageId, text, conversationId) => {
      const id = conversationId ?? get().focusedConversationId
      const body = text.trim()
      if (!id || !body) return false
      if (get().sessions[id]?.streaming?.answering) {
        // The pencil is hidden while answering, but a reply can start under an open editor (another window, a steer).
        get().toast('That chat is already replying — your edit was not sent.', 'error')
        return false
      }
      await convWrites.get(id)?.catch(() => undefined)
      return runStream(id, { content: body, replace_from: messageId })
    },
    activateVariant: async (conversationId, messageId) => {
      if (get().sessions[conversationId]?.streaming?.answering) return
      try {
        const c = await api.activateMessage(conversationId, messageId)
        // Replace the list wholesale: merging would keep the swapped-out row alive.
        patchConversation(conversationId, (cur) => ({ ...cur, messages: c.messages }))
      } catch (e) {
        get().toast((e as Error).message, 'error')
      }
    },
    resumeRun: async (conversationId, runId) => {
      if (get().sessions[conversationId]?.streaming?.answering) return
      if (!get().sessions[conversationId]) await get().openSession(conversationId)
      const run = await api.resumeRun(runId)
      void watchRun(conversationId, run, { messageId: null, approvals: 0, attached: false })
    },
    stop: async (conversationId) => {
      const id = conversationId ?? get().focusedConversationId
      const st = id && get().sessions[id]?.streaming
      if (!id || !st) return
      const runId = st.runId
      // A press while one is in flight (the button, then Escape) joins it: one request, one verdict.
      const pending = stops.get(runId)
      if (pending) return void (await pending)
      // Aborting the fetch would only detach this window, so a stop is always a request to the run.
      patchSession(id, (s) => (s.streaming?.runId === runId ? { ...s, streaming: { ...s.streaming, stopping: true } } : s))
      let timedOut = false
      let detail = ''
      const call = (async (): Promise<StopOutcome> => {
        try {
          return stopOutcome(await api.stopRun(id, runId))
        } catch (e) {
          timedOut = e instanceof ApiError && e.kind === 'timeout'
          detail = (e as Error).message
          return stopOutcome({ error: e })
        }
      })()
      stops.set(runId, call)
      const outcome = await call
      stops.delete(runId)
      // Accepted keeps `stopping` up until the run's own `done` clears it. Anything else hands the button back.
      if (outcome === 'accepted') return
      patchSession(id, (s) => (s.streaming?.runId === runId ? { ...s, streaming: { ...s.streaming, stopping: false } } : s))
      if (outcome === 'failed') {
        get().toast(timedOut ? 'Stop did not reach the backend in time. The reply may still be running.' : `Could not stop: ${detail}`, 'error', { label: 'Retry', run: () => void get().stop(id) })
      }
    },

    // Always every scope: the Files tree shows Personal and each project as its own group, so a doc
    // can never be created into a scope the list is filtered away from and look like it vanished.
    refreshDocs: async (q = '') => set({ docs: await api.docs.list('all', q) }),
    refreshDocsPending: async () => {
      try {
        set({ docsPending: (await api.docs.pending()).pending })
      } catch { /* a badge is not worth a toast */ }
    },
    openDoc: async (id) => {
      await get().flushDoc()
      // A draft the flush could not save (a 409) is the user's to keep or Reload; a refetch would drop it.
      const cur = get()
      if (cur.activeDoc?.id === id && (cur.docDraft !== null || cur.docTitleDraft !== null)) return set({ view: 'docs' })
      set((st) => ({ view: 'docs', docTabs: st.docTabs.includes(id) ? st.docTabs : [...st.docTabs, id] }))
      try {
        const doc = await api.docs.get(id)
        // A slower fetch must not clobber a doc the user has since switched away from.
        if (get().docTabs.includes(id)) set({ activeDoc: doc, docDraft: null, docTitleDraft: null })
        void get().refreshDocRevisions(id)
      } catch (e) {
        get().toast((e as Error).message, 'error')
      }
    },
    closeDocTab: async (id) => {
      // Awaited: the flush is queued, and clearing activeDoc first would leave it nothing to save.
      if (get().activeDoc?.id === id) await get().flushDoc()
      const st = get()
      const tabs = st.docTabs.filter((t) => t !== id)
      set({ docTabs: tabs })
      if (st.activeDoc?.id === id) {
        const next = tabs[tabs.length - 1]
        if (next) void get().openDoc(next)
        else set({ activeDoc: null, docDraft: null, docTitleDraft: null, docRevisions: [] })
      }
    },
    createDoc: async (d = {}) => {
      try {
        const doc = await api.docs.create({ title: d.title ?? 'Untitled', content: d.content ?? '', project_id: d.project_id ?? null, folder: d.folder ?? '' })
        await Promise.all([get().refreshDocs(), get().refreshDocFolders()])
        // Unfold the group and folder chain it landed in, so a new doc is always on screen.
        get().expandTo(doc.project_id ?? '', doc.folder)
        set((st) => ({
          view: 'docs',
          docTabs: st.docTabs.includes(doc.id) ? st.docTabs : [...st.docTabs, doc.id],
          activeDoc: doc,
          docDraft: null
        }))
        void get().refreshDocRevisions(doc.id)
      } catch (e) {
        get().toast((e as Error).message, 'error')
      }
    },
    openDailyNote: async () => {
      try {
        const { doc } = await dailyNote()
        await get().refreshDocs()
        get().expandTo(doc.project_id ?? '', doc.folder)
        await get().openDoc(doc.id)
      } catch (e) {
        get().toast((e as Error).message, 'error')
      }
    },
    editDoc: (content) => {
      if (!get().activeDoc) return
      set({ docDraft: content })
      if (saveTimer) clearTimeout(saveTimer)
      saveTimer = setTimeout(() => { void get().flushDoc() }, SAVE_DEBOUNCE_MS)
    },
    editDocTitle: (title) => {
      if (!get().activeDoc) return
      set({ docTitleDraft: title })
      if (saveTimer) clearTimeout(saveTimer)
      saveTimer = setTimeout(() => { void get().flushDoc() }, SAVE_DEBOUNCE_MS)
    },
    // Flushes run one after another: an overlapping one would read the base the first is about to bump.
    flushDoc: () => {
      const run = async (): Promise<void> => {
      if (saveTimer) {
        clearTimeout(saveTimer)
        saveTimer = null
      }
      const { activeDoc: doc, docDraft, docTitleDraft } = get()
      if (!doc) return set({ docDraft: null, docTitleDraft: null })
      const edits = docEdits(doc, docDraft, docTitleDraft)
      if (!edits) return set({ docDraft: null, docTitleDraft: null })
      set({ docSaving: true })
      try {
        const saved = await api.docs.save(doc.id, { ...edits, base_updated_at: doc.updated_at })
        // Keep whatever was typed while the request was in flight as the draft. activeDoc stays the
        // server's copy, so the next flush still sees that draft differ and sends it.
        set((st) => {
          if (st.activeDoc?.id !== doc.id) return { docSaving: false }
          const newer = st.docDraft !== null && st.docDraft !== docDraft
          const newerTitle = st.docTitleDraft !== null && st.docTitleDraft !== docTitleDraft
          return {
            activeDoc: saved,
            docDraft: newer ? st.docDraft : null,
            docTitleDraft: newerTitle ? st.docTitleDraft : null,
            docSaving: false
          }
        })
        void get().refreshDocs()
        void get().refreshDocRevisions(doc.id)
      } catch (e) {
        set({ docSaving: false })
        // Stale base: another window saved first. The draft stays on screen; reloading is the user's call.
        if ((e as { status?: number }).status === 409) {
          get().toast('This doc changed elsewhere. Your edits are kept here and not saved.', 'error',
            { label: 'Reload', run: () => { set({ docDraft: null, docTitleDraft: null }); void get().openDoc(doc.id) } })
        } else get().toast(`Could not save: ${(e as Error).message}`, 'error')
      }
      }
      const p = flushChain.then(run)
      flushChain = p
      return p
    },
    setDocStar: async (id, starred) => {
      const d = await api.docs.patch(id, { starred })
      // The PATCH bumped updated_at; keep the autosave base current or the next save 409s.
      set((st) => ({ activeDoc: st.activeDoc?.id === id ? { ...st.activeDoc, starred: d.starred, updated_at: d.updated_at } : st.activeDoc }))
      await get().refreshDocs()
    },
    setDocPin: async (id, pinned) => {
      await api.docs.patch(id, { pinned })
      await get().refreshDocs()
    },
    moveDoc: async (id, scope, folder) => {
      try {
        const d = await api.docs.move(id, scope, folder.trim())
        set((st) => ({
          activeDoc: st.activeDoc?.id === id
            ? { ...st.activeDoc, folder: d.folder, project_id: d.project_id, updated_at: d.updated_at }
            : st.activeDoc
        }))
        await Promise.all([get().refreshDocs(), get().refreshDocFolders()])
        get().expandTo(d.project_id ?? '', d.folder)
      } catch (e) {
        get().toast((e as Error).message, 'error')
      }
    },
    refreshDocFolders: async () => {
      try {
        set({ docFolders: await api.docs.folders() })
      } catch { /* the tree still renders from the docs' own paths */ }
    },
    createDocFolder: async (path, scope = '') => {
      try {
        set({ docFolders: await api.docs.createFolder(path, scope) })
        get().expandTo(scope, path)
      } catch (e) {
        get().toast((e as Error).message, 'error')
      }
    },
    renameDocFolder: async (path, newPath, scope = '') => {
      try {
        set({ docFolders: await api.docs.renameFolder(path, newPath, scope) })
        // Every doc under it moved with it, and the open one's own folder is now stale.
        await get().refreshDocs()
        const open = get().activeDoc
        if (open) void get().openDoc(open.id)
        get().expandTo(scope, newPath)
      } catch (e) {
        get().toast((e as Error).message, 'error')
      }
    },
    deleteDocFolder: async (path, deleteDocs = false, scope = '') => {
      try {
        // Buffered edits go in before the folder's docs are trashed: a trashed doc can no longer be saved.
        await get().flushDoc()
        // Which docs this takes with it, so the Undo can bring each one back.
        const doomed = deleteDocs
          ? get().docs.filter((d) => (d.project_id ?? '') === scope && (d.folder === path || d.folder.startsWith(path + '/')))
          : []
        set({ docFolders: await api.docs.deleteFolder(path, deleteDocs, scope) })
        if (doomed.length) get().offerUndo(`${doomed.length} doc${doomed.length === 1 ? '' : 's'}`, doomed.map((d) => ({ type: 'doc' as const, id: d.id })))
        await get().refreshDocs()
        const open = get().activeDoc
        if (open && deleteDocs && !get().docs.some((d) => d.id === open.id)) await get().closeDocTab(open.id)
        else if (open) void get().openDoc(open.id)
      } catch (e) {
        get().toast((e as Error).message, 'error')
      }
    },
    toggleFolder: (key) => set((st) => ({
      expandedFolders: writeExpanded(st.expandedFolders.includes(key)
        ? st.expandedFolders.filter((p) => p !== key)
        : [...st.expandedFolders, key])
    })),
    expandTo: (scope, path) => set((st) => {
      // A group is open unless its shut-key is present, so revealing something means dropping that
      // key as well as adding the chain of folders down to it.
      const want = chainTo(path).map((p) => folderKey(scope, p)).filter((k) => !st.expandedFolders.includes(k))
      const shut = groupShutKey(scope)
      const next = st.expandedFolders.filter((k) => k !== shut)
      if (!want.length && next.length === st.expandedFolders.length) return {}
      return { expandedFolders: writeExpanded([...next, ...want]) }
    }),
    deleteDoc: async (id) => {
      const title = get().docs.find((d) => d.id === id)?.title
      // Save buffered edits first: once trashed the doc 404s on save, and Undo should bring them back.
      if (get().activeDoc?.id === id) await get().flushDoc()
      await api.docs.delete(id)
      await get().closeDocTab(id)
      await Promise.all([get().refreshDocs(), get().refreshDocsPending()])
      get().offerUndo(title ? `“${title}”` : 'doc', [{ type: 'doc', id }])
    },
    setDocMode: (docMode) => {
      try { localStorage.setItem(DOC_MODE_KEY, docMode) } catch { /* private window */ }
      set({ docMode })
    },
    refreshDocRevisions: async (id) => {
      const docId = id ?? get().activeDoc?.id
      if (!docId) return
      try {
        const revs = await api.docs.revisions(docId)
        if (get().activeDoc?.id === docId) set({ docRevisions: revs })
      } catch { /* history is supplementary */ }
    },
    acceptRevision: async (revId) => {
      try {
        // Buffered typing is saved first, so accepting lands on top of it instead of losing it.
        await get().flushDoc()
        const sent = get().docDraft
        const base = sent ?? get().activeDoc?.content ?? ''
        const doc = await api.docs.accept(revId)
        set((st) => adoptServerDoc(st.docDraft, doc, sent, base))
        get().toast('Revision applied')
        await Promise.all([get().refreshDocRevisions(doc.id), get().refreshDocs(), get().refreshDocsPending()])
      } catch (e) {
        get().toast((e as Error).message, 'error')
      }
    },
    rejectRevision: async (revId) => {
      try {
        const doc = await api.docs.reject(revId)
        set({ activeDoc: doc })
        await Promise.all([get().refreshDocRevisions(doc.id), get().refreshDocs(), get().refreshDocsPending()])
      } catch (e) {
        get().toast((e as Error).message, 'error')
      }
    },
    restoreRevision: async (revId) => {
      try {
        await get().flushDoc()
        const sent = get().docDraft
        const base = sent ?? get().activeDoc?.content ?? ''
        const doc = await api.docs.restore(revId)
        set((st) => adoptServerDoc(st.docDraft, doc, sent, base))
        get().toast('Document restored')
        await Promise.all([get().refreshDocRevisions(doc.id), get().refreshDocs()])
      } catch (e) {
        get().toast((e as Error).message, 'error')
      }
    },

    loadPlan: async (conversationId) => {
      const plan = await api.plan.get(conversationId).catch(() => null)
      if (plan) set((s) => ({ plans: { ...s.plans, [conversationId]: plan.steps } }))
    },
    setPlanSteps: async (conversationId, steps) => {
      // Optimistic: ticking a step off must feel like a checkbox, and the model reads the stored plan
      // at the top of its next round either way.
      const before = get().plans[conversationId]
      set((s) => ({ plans: { ...s.plans, [conversationId]: steps } }))
      const plan = await api.plan.set(conversationId, steps).catch((e: Error) => {
        get().toast(e.message, 'error')
        // Roll the checkbox back: the stored plan is what the model will read.
        if (before) set((s) => ({ plans: { ...s.plans, [conversationId]: before } }))
        return null
      })
      if (plan) set((s) => ({ plans: { ...s.plans, [conversationId]: plan.steps } }))
    },
    clearPlan: async (conversationId) => {
      set((s) => ({ plans: { ...s.plans, [conversationId]: [] } }))
      await api.plan.clear(conversationId).catch(() => undefined)
    },

    setLibraryTab: (libraryTab) => set({ libraryTab }),
    // The docs list is already kept live elsewhere; this is for the two things the Library reads
    // that nothing else refreshes on its behalf.
    refreshLibrary: async () => {
      await Promise.all([get().refreshSkills().catch(() => undefined), get().refreshDocs().catch(() => undefined)])
    },

    refreshDesks: async () => {
      try {
        set({ desks: await api.cowork.desks.list(get().libraryScope, '', get().deskShowArchived) })
      } catch (e) {
        get().toast((e as Error).message, 'error')
      }
    },
    setDeskShowArchived: async (v) => {
      set({ deskShowArchived: v })
      await get().refreshDesks()
    },
    refreshDeskInbox: async () => {
      try {
        set({ deskInbox: await api.cowork.inbox.list() })
      } catch { /* a badge is not worth a toast */ }
    },
    openDesk: async (id) => {
      // The files and the preview belong to the desk that was open, so they go now rather than
      // after the fetch: the Files tab must never paint another desk's workspace for a frame.
      if (get().activeDeskId !== id) set({ activeDeskId: id, activeDesk: null, deskFiles: [], deskPreview: null })
      try {
        const desk = await api.cowork.desks.get(id)
        // A slower fetch must not clobber a desk the user has since switched away from.
        if (get().activeDeskId !== id) return
        set({ activeDesk: desk })
        putDesk(desk)
        // `retainSession` is the detail pane's own effect pair: the 12-session LRU evicts by
        // `touchedAt`, and a desk pane is never `focusedConversationId`.
        if (desk.live) await get().attachSession(desk.conversation_id)
        else await get().openSession(desk.conversation_id)
      } catch (e) {
        get().toast((e as Error).message, 'error')
      }
    },
    createDesk: async (p) => {
      set({ deskBusy: true })
      try {
        const { desk, conversation_id, run_id, seq } = await api.cowork.desks.create(p)
        await get().refreshDesks()
        await get().openDesk(desk.id)
        // Start returns the run outright, so the pane paints without waiting for `GET /runs` to
        // notice it. `watchRun` dedupes on run_id against whatever `openDesk` already attached.
        if (run_id) void watchRun(conversation_id, { run_id, seq: seq ?? 0 }, { messageId: null, approvals: 0, attached: true })
        return desk
      } catch (e) {
        // Never rejects: the new-desk card is holding the user's brief on this verdict.
        get().toast((e as Error).message, 'error')
        return null
      } finally {
        set({ deskBusy: false })
      }
    },
    startDesk: async (id) => {
      try {
        const { run_id, seq, conversation_id } = await api.cowork.desks.start(id)
        await get().openDesk(id)
        void watchRun(conversation_id, { run_id, seq }, { messageId: null, approvals: 0, attached: true })
      } catch (e) {
        get().toast((e as Error).message, 'error')
      }
    },
    resumeDesk: async (id, reason) => {
      const convId = deskConv(id)
      try {
        const { run_id, seq } = await api.cowork.desks.resume(id, reason)
        await get().openDesk(id)
        if (convId) void watchRun(convId, { run_id, seq }, { messageId: null, approvals: 0, attached: true })
      } catch (e) {
        get().toast((e as Error).message, 'error')
      }
    },
    pauseDesk: async (id) => {
      try {
        putDesk(await api.cowork.desks.pause(id))
      } catch (e) {
        get().toast((e as Error).message, 'error')
      }
    },
    stopDesk: async (id) => {
      try {
        putDesk(await api.cowork.desks.stop(id))
      } catch (e) {
        get().toast((e as Error).message, 'error')
      }
    },
    messageDesk: async (id, text) => {
      if (!text.trim()) return false
      try {
        await api.cowork.desks.message(id, text.trim())
        // Awake it steered the live reply, asleep it woke a new turn — either way the row has moved
        // (the question is answered, the status is live again), and `openDesk` re-attaches.
        await get().openDesk(id)
        return true
      } catch (e) {
        get().toast((e as Error).message, 'error')
        return false
      }
    },
    patchDesk: async (id, patch) => {
      try {
        putDesk(await api.cowork.desks.patch(id, patch))
      } catch (e) {
        get().toast((e as Error).message, 'error')
      }
    },
    deleteDesk: async (id, purge = false) => {
      const convId = deskConv(id)
      try {
        await api.cowork.desks.delete(id, purge)
      } catch (e) {
        return get().toast((e as Error).message, 'error')
      }
      if (convId) get().closeSession(convId)
      set((st) => ({
        desks: st.desks.filter((d) => d.id !== id),
        activeDeskId: st.activeDeskId === id ? null : st.activeDeskId,
        activeDesk: st.activeDesk?.id === id ? null : st.activeDesk,
        deskFiles: st.activeDeskId === id ? [] : st.deskFiles,
        deskPreview: st.activeDeskId === id ? null : st.deskPreview
      }))
      void get().refreshConversations()
    },
    loadDeskFiles: async (id, path = '', quiet = false) => {
      try {
        const { files } = await api.cowork.desks.files(id, path)
        // `desk_status` loads these for whichever desk reached review, which need not be the open one.
        if (get().activeDeskId === id) set({ deskFiles: files })
      } catch (e) {
        if (!quiet) get().toast((e as Error).message, 'error')
      }
    },
    previewDeskFile: async (_id, path) => {
      set({ deskPreview: { path, text: '' } })
    },
    acceptOutputs: async (id, sel) => {
      if (!sel.length) return []
      set({ deskBusy: true })
      try {
        const { results } = await api.cowork.desks.accept(id, sel)
        // `verified` is read from the response, never assumed: a promotion the backend could not
        // read back is a red row, not a tick.
        const bad = results.filter((r) => !r.ok || !r.verified).length
        get().toast(bad ? `${bad} of ${results.length} could not be verified` : `Promoted ${results.length} output${results.length === 1 ? '' : 's'}`, bad ? 'error' : 'info')
        await get().openDesk(id)
        return results
      } catch (e) {
        get().toast((e as Error).message, 'error')
        return []
      } finally {
        set({ deskBusy: false })
      }
    },
    rejectOutputs: async (id, outputIds, note) => {
      set({ deskBusy: true })
      try {
        putDesk(await api.cowork.desks.reject(id, outputIds, note))
        await get().openDesk(id)
      } catch (e) {
        get().toast((e as Error).message, 'error')
      } finally {
        set({ deskBusy: false })
      }
    },
    /**
     * A plan is answered through the approvals route, like every other card, so it is keyed by the
     * card's `call_id` rather than by the plan. One decision path means a plan cannot be approved by
     * a route that skips the approval row, the edited-digest re-derivation or the single-use claim.
     */
    decidePlan: async (callId, decision, edits, note) => {
      set({ deskBusy: true })
      try {
        await api.approve(callId, decision === 'reject' ? 'deny' : 'allow',
                          { steps: decision === 'edit' ? edits ?? null : null, note })
        // The card's own stream carries `plan_decision`; a desk pane that is open re-reads the desk,
        // because answering may also have woken it.
        const open = get().activeDeskId
        if (open) await get().openDesk(open)
      } catch (e) {
        get().toast((e as Error).message, 'error')
      } finally {
        set({ deskBusy: false })
      }
    },
    answerDeskCard: async (callId, allow, note) => {
      set({ deskBusy: true })
      try {
        await api.approve(callId, allow ? 'allow' : 'deny', { steps: null, note })
        const open = get().activeDeskId
        if (open) await get().openDesk(open)
      } catch (e) {
        get().toast((e as Error).message, 'error')
      } finally {
        set({ deskBusy: false })
      }
    },
    setPlanMode: (convId, mode) => get().setChatSettings({ planMode: mode }, convId),
    markDeskEventSeen: async (eventId) => {
      // Optimistic: the badge is the whole point, so it must not wait on a round trip.
      set((st) => ({ deskInbox: st.deskInbox.filter((e) => e.id !== eventId) }))
      try {
        await api.cowork.inbox.seen(eventId)
      } catch {
        void get().refreshDeskInbox()
      }
    },
    markDeskSeen: async (deskId) => {
      // Optimistic for the same reason as the single-event version: the badge must not wait on a
      // round trip. The route answers with the refreshed desk row, so the rail follows it too.
      set((st) => ({ deskInbox: st.deskInbox.filter((e) => e.desk_id !== deskId) }))
      try {
        putDesk(await api.cowork.desks.seen(deskId))
      } catch {
        void get().refreshDeskInbox()
      }
    },

    refreshSkills: async () => set({ skills: await api.skills.list() }),
    createSkill: async (draft) => {
      try {
        const s = await api.skills.create(draft)
        set((st) => ({ skills: [s, ...st.skills] }))
      } catch (e) {
        get().toast((e as Error).message, 'error')
      }
    },
    updateSkill: async (id, patch) => {
      const s = await api.skills.update(id, patch)
      set((st) => ({ skills: st.skills.map((x) => (x.id === id ? s : x)) }))
    },
    deleteSkill: async (id) => {
      await api.skills.delete(id)
      set((st) => ({ skills: st.skills.filter((x) => x.id !== id) }))
    },
    induceSkill: async (conversationId, messageId) => {
      try {
        const { candidate, reason } = await api.skills.induce(conversationId, messageId)
        if (candidate) {
          set((st) => ({ skills: [candidate, ...st.skills] }))
          get().toast(`Candidate skill “${candidate.name}” is waiting for your review`, 'learned')
        } else get().toast(reason ?? 'Nothing reusable to propose', 'info')
      } catch (e) {
        get().toast((e as Error).message, 'error')
      }
    },

    // ---- meetings ----
    refreshMeetings: async (query) => {
      // Defaulting to the stored query rather than '' is the whole point: the recorder bar's 5s
      // tick and the notes autosave both call this with no argument, and an unfiltered answer
      // would replace the list under a search box that still reads "budget".
      const q = query ?? get().meetingQuery
      if (q !== get().meetingQuery) set({ meetingQuery: q })
      try {
        set({ meetings: await api.meetings.list(get().dataScope, q) })
      } catch { /* the recorder bar re-runs this every 5s; one flaky request must not toast */ }
    },
    setMeetingQuery: (query) => set({ meetingQuery: query }),
    refreshMeetingStatus: async () => {
      try {
        const meetingStatus = await api.meetings.status()
        set({ meetingStatus })
        const m = get().activeMeeting
        // `counts.pending` covers the one path the 90s window cannot: when `stop` abandons the
        // drain, the row is finalized 'ready' straight away and a background watcher waits up to
        // 15 minutes for the segments to settle before re-rolling the transcript and enhancing.
        // 'ready' is not in SETTLING, so without this the tick dies at 90s and neither the late
        // transcript nor the enhanced-notes proposal ever reaches the open meeting.
        const draining = meetingStatus.counts.pending > 0
        const settling = !!meetingStatus.active || draining || (m !== null && SETTLING.includes(m.status)) || Date.now() < meetingSettleUntil
        if (settling && !meetingLiveTimer) meetingLiveTimer = setInterval(() => void liveTick(), 5000)
        if (!settling && meetingLiveTimer) {
          clearInterval(meetingLiveTimer)
          meetingLiveTimer = null
        }
      } catch { /* the panel shows whatever it last had; a failed poll is not worth a toast */ }
    },
    refreshMeetingsPending: async () => {
      try {
        set({ meetingsPending: (await api.meetings.pending()).pending })
      } catch { /* a badge is not worth a toast */ }
    },
    openMeeting: async (id) => {
      const outgoing = get().activeMeeting?.id ?? null
      if (outgoing !== id) await get().flushMeetingNotes()
      openingMeeting = id
      set({ view: 'meetings' })
      try {
        const m = await api.meetings.get(id)
        if (openingMeeting !== id) return
        // Anything typed during the two round trips still belongs to the outgoing meeting, and the
        // reset below is about to drop it.
        const late = await flushOutgoing(outgoing)
        if (openingMeeting !== id) return
        // Reopening the row that is already open: the flush just saved newer notes than the GET
        // above returned, and a draft that arrived during the PUT is still unsaved.
        const same = late !== null && late.id === id
        const draft = get().meetingNotesDraft
        set({
          activeMeeting: same ? { ...m, notes: late.notes } : m,
          meetingNotesDraft: same ? draft : null,
          meetingSegments: [],
          meetingCursor: 0
        })
        await loadSegments(id)
        // A meeting that is still recording or settling gets the tick and the stream; one that is
        // finished needs neither, and would otherwise poll a row nothing is writing to.
        if (SETTLING.includes(m.status)) {
          void get().refreshMeetingStatus()
          void get().watchMeeting(id)
        }
      } catch (e) {
        get().toast((e as Error).message, 'error')
      }
    },
    startRecording: async (meetingId) => {
      // Asked once per install, and nothing records until it is acknowledged. The click is
      // remembered rather than dropped, so accepting the notice finishes what the user pressed.
      if (!get().meetingStatus?.consented) {
        consentIntent = meetingId
        return set({ meetingConsentOpen: true })
      }
      set({ meetingBusy: true })
      try {
        // Whatever is buffered belongs to the meeting being left behind, so it goes first.
        const outgoing = get().activeMeeting?.id ?? null
        await get().flushMeetingNotes()
        const id = meetingId ?? (await api.meetings.create({ title: newMeetingTitle() })).id
        const m = await api.meetings.start(id)
        // Typing carried on through create+start; the reset below would otherwise discard it.
        const late = await flushOutgoing(outgoing)
        const same = late !== null && late.id === id
        const draft = get().meetingNotesDraft
        // Claims the open slot: an `openMeeting` still in flight for another row must not land on
        // top of the one that just started recording.
        openingMeeting = id
        set({
          view: 'meetings',
          activeMeeting: same ? { ...m, notes: late.notes } : m,
          meetingNotesDraft: same ? draft : null,
          meetingSegments: [],
          meetingCursor: 0
        })
        void get().watchMeeting(id)
        await Promise.all([get().refreshMeetings(), get().refreshMeetingStatus()])
      } catch (e) {
        get().toast(startFailure(e), 'error')
        // Whatever blocked it is now ten minutes stale in the cached checklist; re-probe so the
        // panel's rows agree with the toast the user just read.
        void get().loadMeetingPreflight(true)
      } finally {
        set({ meetingBusy: false })
      }
    },
    stopRecording: async () => {
      const id = get().meetingStatus?.active?.meeting_id
      if (!id) return
      set({ meetingBusy: true })
      try {
        // Blocks while the transcription backlog drains, so the button stays disabled for as long
        // as the request runs rather than looking idle with ffmpeg still up.
        const m = await api.meetings.stop(id)
        set((st) => (st.activeMeeting?.id === id ? { activeMeeting: m } : {}))
        get().toast('Recording stopped. The transcript and the enhanced notes finish in the background.')
        // /stop answers with the row already `ready` and schedules the enhance pass afterwards, so
        // without this the status poll below would conclude nothing is settling and stop the tick
        // seconds before the proposal, the action items and the last transcriptions arrive.
        meetingSettleUntil = Date.now() + MEETING_SETTLE_MS
        await loadSegments(id)
        await Promise.all([get().refreshMeetings(), get().refreshMeetingStatus(), get().refreshMeetingsPending()])
      } catch (e) {
        get().toast((e as Error).message, 'error')
        void get().refreshMeetingStatus()
      } finally {
        set({ meetingBusy: false })
      }
    },
    pauseMeeting: async () => {
      const id = get().meetingStatus?.active?.meeting_id
      if (!id) return
      try {
        set({ meetingStatus: await api.meetings.pause(id) })
      } catch (e) {
        get().toast((e as Error).message, 'error')
      }
    },
    resumeMeeting: async () => {
      const id = get().meetingStatus?.active?.meeting_id
      if (!id) return
      try {
        set({ meetingStatus: await api.meetings.resume(id) })
      } catch (e) {
        get().toast((e as Error).message, 'error')
      }
    },
    recordCandidate: async (candidate) => {
      let id = candidate.meeting_id
      if (!id) {
        set({ meetingBusy: true })
        try {
          // Find-or-create: the partial unique index on calendar_event_id makes a second POST for
          // one event hand back the row that already exists, so there is no adopt route to call.
          const m = await api.meetings.create({
            title: candidate.title,
            calendar_event_id: candidate.event_id,
            calendar_id: candidate.calendar_id,
            conference_link: candidate.conference_link,
            scheduled_start: epochSeconds(candidate.start),
            scheduled_end: epochSeconds(candidate.end)
          })
          id = m.id
        } catch (e) {
          return get().toast((e as Error).message, 'error')
        } finally {
          set({ meetingBusy: false })
        }
      }
      await get().startRecording(id)
    },
    editMeetingNotes: (next) => {
      if (!get().activeMeeting) return
      set({ meetingNotesDraft: next })
      if (meetingSaveTimer) clearTimeout(meetingSaveTimer)
      meetingSaveTimer = setTimeout(() => { void get().flushMeetingNotes() }, MEETING_SAVE_DEBOUNCE_MS)
    },
    flushMeetingNotes: async () => {
      if (meetingSaveTimer) {
        clearTimeout(meetingSaveTimer)
        meetingSaveTimer = null
      }
      const { activeMeeting: m, meetingNotesDraft: draft } = get()
      if (!m || draft === null || draft === m.notes) return set({ meetingNotesDraft: null })
      set({ meetingSaving: true })
      try {
        const saved = await api.meetings.patch(m.id, { notes: draft })
        // Keep whatever was typed while the request was in flight; adopt only the server's metadata.
        set((st) => {
          if (st.activeMeeting?.id !== m.id) return { meetingSaving: false }
          const newer = st.meetingNotesDraft !== null && st.meetingNotesDraft !== draft
          return {
            activeMeeting: newer ? { ...saved, notes: st.meetingNotesDraft as string } : saved,
            meetingNotesDraft: newer ? st.meetingNotesDraft : null,
            meetingSaving: false
          }
        })
        void get().refreshMeetings()
      } catch (e) {
        set({ meetingSaving: false })
        get().toast(`Could not save: ${(e as Error).message}`, 'error')
      }
    },
    pollMeetingLive: async () => {
      try {
        await get().refreshMeetingStatus()
        const id = get().activeMeeting?.id
        if (!id) return
        const rows = await api.meetings.segments(id, get().meetingCursor)
        if (!rows.length || get().activeMeeting?.id !== id) return
        set((st) => ({ meetingSegments: applyCursor(st.meetingSegments, rows), meetingCursor: lastCursor(rows, st.meetingCursor) }))
      } catch { /* a 2s poll that toasts would paper the screen over one flaky request */ }
    },
    watchMeeting: async (meetingId) => {
      // One subscription per meeting: a second would fold every pushed frame in twice. Nothing
      // publishes to the meeting bus yet, so today this opens and ends at once — the transcript
      // pane is carried by `pollMeetingLive`, and this is the seam that replaces it.
      if (meetingWatch?.id === meetingId) return
      meetingWatch?.abort.abort()
      const abort = new AbortController()
      meetingWatch = { id: meetingId, abort }
      try {
        for await (const ev of meetingStream(meetingId, 0, abort.signal)) {
          set((st) => {
            const next = applyMeetingEvent(st, ev)
            return next === st ? {} : next
          })
          if (ev.event === 'error') {
            const d = ev.data
            get().toast(isRecord(d) && typeof d.message === 'string' ? d.message : 'The recording reported an error', 'error')
          } else if (ev.event === 'end') {
            void get().refreshMeetings()
            void get().refreshMeetingsPending()
          }
        }
      } catch (e) {
        // An aborted signal is a newer subscription or a view teardown, not a failure.
        if (!abort.signal.aborted) get().toast((e as Error).message, 'error')
      } finally {
        // Abort-identity guarded: a newer subscription has already replaced this one.
        if (meetingWatch?.abort === abort) meetingWatch = null
      }
    },
    enhanceMeeting: async (id, force = false) => {
      set({ meetingBusy: true })
      try {
        // The pass reads the notes, so an unflushed paragraph would be missing from the proposal.
        await get().flushMeetingNotes()
        const rev = await api.meetings.enhance(id, force)
        // An auto-applied revision comes back `applied` with the meeting's `pending` already null,
        // so the meeting is re-fetched rather than patched from the revision.
        const m = await api.meetings.get(id)
        if (get().activeMeeting?.id === id) set({ activeMeeting: m })
        if (rev.degraded) get().toast('The model call failed — these are mechanically enhanced notes', 'error')
        else get().toast(rev.status === 'pending' ? 'Enhanced notes are waiting for review' : 'Enhanced notes ready')
        await Promise.all([get().refreshMeetings(), get().refreshMeetingsPending()])
      } catch (e) {
        get().toast((e as Error).message, 'error')
      } finally {
        set({ meetingBusy: false })
      }
    },
    acceptMeetingRevision: async (revisionId) => {
      try {
        // No flush first, unlike `acceptRevision`: accepting writes `enhanced` and nothing else, so
        // a buffered note is in no danger and stays buffered.
        const m = await api.meetings.accept(revisionId)
        if (get().activeMeeting?.id === m.id) set({ activeMeeting: m })
        get().toast('Enhanced notes applied')
        await Promise.all([get().refreshMeetings(), get().refreshMeetingsPending()])
      } catch (e) {
        get().toast((e as Error).message, 'error')
      }
    },
    rejectMeetingRevision: async (revisionId) => {
      try {
        const m = await api.meetings.reject(revisionId)
        if (get().activeMeeting?.id === m.id) set({ activeMeeting: m })
        await Promise.all([get().refreshMeetings(), get().refreshMeetingsPending()])
      } catch (e) {
        get().toast((e as Error).message, 'error')
      }
    },
    promoteActionItems: async (meetingId, actionIds) => {
      set({ meetingBusy: true })
      try {
        const was = (get().activeMeeting?.actions ?? []).filter((a) => a.status === 'added').length
        const actions = await api.meetings.addTodos(meetingId, actionIds)
        set((st) => (st.activeMeeting?.id === meetingId ? { activeMeeting: { ...st.activeMeeting, actions } } : {}))
        const added = actions.filter((a) => a.status === 'added').length - was
        get().toast(added > 0 ? `${added} action item${added === 1 ? '' : 's'} added to your todos` : 'Those items are already todos')
        await Promise.all([get().refreshTodos(), get().refreshDashboard()])
      } catch (e) {
        get().toast((e as Error).message, 'error')
      } finally {
        set({ meetingBusy: false })
      }
    },
    dismissActionItem: async (meetingId, actionId) => {
      try {
        const item = await api.meetings.dismissAction(meetingId, actionId)
        set((st) => (st.activeMeeting?.id === meetingId
          ? { activeMeeting: { ...st.activeMeeting, actions: st.activeMeeting.actions.map((a) => (a.id === item.id ? item : a)) } }
          : {}))
      } catch (e) {
        get().toast((e as Error).message, 'error')
      }
    },
    deleteMeeting: async (id) => {
      try {
        // Stop first. DELETE only drops the row, and the recorder bar — the only Stop, Pause and
        // Resume in the app — is mounted on the open meeting, so deleting the live row would leave
        // ffmpeg capturing with no control anywhere that can reach it.
        if (get().meetingStatus?.active?.meeting_id === id) await get().stopRecording()
        await api.meetings.del(id)
        if (get().activeMeeting?.id === id) {
          // The buffered notes belong to a row that no longer exists.
          if (meetingSaveTimer) {
            clearTimeout(meetingSaveTimer)
            meetingSaveTimer = null
          }
          openingMeeting = null
          set({ activeMeeting: null, meetingNotesDraft: null, meetingSegments: [], meetingCursor: 0 })
        }
        await Promise.all([get().refreshMeetings(), get().refreshMeetingsPending()])
      } catch (e) {
        get().toast((e as Error).message, 'error')
      }
    },
    setMeetingConfig: async (patch) => {
      try {
        set({ meetingStatus: await api.meetings.setConfig(patch) })
        // A device or backend change invalidates what preflight concluded up to ten minutes ago.
        if (['micDevice', 'outputDevice', 'sources', 'sttBackend', 'sttModel', 'whisperModelPath'].some((k) => k in patch)) {
          void get().loadMeetingPreflight(true)
        }
      } catch (e) {
        get().toast((e as Error).message, 'error')
      }
    },
    loadMeetingPreflight: async (force = false) => {
      try {
        set({ meetingPreflight: await api.meetings.preflight(force) })
      } catch (e) {
        // Only the Re-check button passes `force`; the mount-time probe stays quiet and the panel
        // keeps whatever checklist it last had.
        if (force) get().toast((e as Error).message, 'error')
      }
    },
    retranscribeMeeting: async (id) => {
      set({ meetingBusy: true })
      try {
        const { settled, meeting } = await api.meetings.retranscribe(id)
        if (get().activeMeeting?.id === id) set({ activeMeeting: meeting })
        if (settled > 0) get().toast(`${settled} clip${settled === 1 ? '' : 's'} transcribed`)
        else get().toast('Nothing could be transcribed — the audio is gone, or the provider is still failing', 'error')
        // The replayed rows kept their rowids, so the cursor would never re-deliver them.
        await loadSegments(id)
        await get().refreshMeetings()
      } catch (e) {
        get().toast((e as Error).message, 'error')
      } finally {
        set({ meetingBusy: false })
      }
    },
    deleteMeetingAudio: async (meetingId) => {
      set({ meetingBusy: true })
      try {
        if (meetingId) {
          const m = await api.meetings.deleteAudio(meetingId)
          if (get().activeMeeting?.id === meetingId) set({ activeMeeting: m })
          get().toast('Recorded audio deleted')
        } else {
          // There is no bulk route — the audio directory is per meeting — so the sweep is a loop
          // over a freshly loaded rail rather than over whatever it happened to be showing.
          await get().refreshMeetings()
          const rows = get().meetings
          for (const r of rows) await api.meetings.deleteAudio(r.id).catch(() => undefined)
          const open = get().activeMeeting?.id
          if (open) {
            const m = await api.meetings.get(open).catch(() => null)
            if (m && get().activeMeeting?.id === open) set({ activeMeeting: m })
          }
          get().toast(`Deleted the recorded audio of ${rows.length} meeting${rows.length === 1 ? '' : 's'}`)
        }
        await get().refreshMeetings()
      } catch (e) {
        get().toast((e as Error).message, 'error')
      } finally {
        set({ meetingBusy: false })
      }
    },
    setMeetingConsentOpen: (open) => {
      // Dismissing the notice drops the Record click it was gating: a recording never starts by
      // default, and a remembered intent would make the next acknowledgement record something
      // the user did not just ask for.
      if (!open) { consentIntent = undefined; consentResume.run = null }
      set({ meetingConsentOpen: open })
    },
    acceptMeetingConsent: async () => {
      try {
        set({ meetingStatus: await api.meetings.consent() })
      } catch (e) {
        return get().toast((e as Error).message, 'error')
      }
      const intent = consentIntent
      consentIntent = undefined
      set({ meetingConsentOpen: false })
      // A doc's Record click resumes as a doc recording, not as a meeting in the Meetings view.
      const resume = consentResume.run
      consentResume.run = null
      if (resume) return resume()
      await get().startRecording(intent)
    },

    refreshMemories: async (q = '') => set({ memories: await api.memories.list(get().dataScope, q) }),
    addMemory: async (content, kind, projectId) => {
      await api.memories.create({ project_id: projectId, content, kind })
      await Promise.all([get().refreshMemories(), get().refreshProjects()])
    },
    updateMemory: async (id, patch) => {
      const m = await api.memories.update(id, patch)
      set((s) => ({ memories: s.memories.map((x) => (x.id === id ? m : x)) }))
      void get().refreshProjects()
    },
    deleteMemory: async (id) => {
      await api.memories.delete(id)
      set((s) => ({ memories: s.memories.filter((m) => m.id !== id) }))
      void get().refreshProjects()
      get().offerUndo('memory', [{ type: 'memory', id }])
    },

    refreshGraph: async () => set({ graph: await api.graph.get(get().dataScope) }),
    refreshDocuments: async () => set({ documents: await api.documents.list(get().dataScope) }),

    // ---- writing style ----
    // The scope here is the data scope the Memory panel loaded. 'all' has no voice of its own, so the
    // API maps it to the personal one — the voice every chat falls back to anyway.
    refreshStyle: async () => {
      const scope = get().dataScope
      const [style, styleSamples] = await Promise.all([
        api.style.get(scope),
        api.style.samples(scope).catch(() => [] as StyleSample[])
      ])
      set({ style, styleSamples })
    },
    saveStyle: async (patch) => {
      try {
        set({ style: await api.style.update(styleScope(get().dataScope), patch) })
      } catch (e) {
        get().toast((e as Error).message, 'error')
      }
    },
    learnStyle: async () => {
      if (get().styleLearning) return
      set({ styleLearning: true })
      try {
        const style = await api.style.learn(styleScope(get().dataScope), get().settings.extractionModel || undefined)
        set({ style })
        await get().refreshStyle()
        get().toast('Re-read your writing', 'learned')
      } catch (e) {
        get().toast((e as Error).message, 'error')
      } finally {
        set({ styleLearning: false })
      }
    },
    resetStyle: async (withSamples = false) => {
      try {
        set({ style: await api.style.reset(styleScope(get().dataScope), withSamples) })
        await get().refreshStyle()
      } catch (e) {
        get().toast((e as Error).message, 'error')
      }
    },
    addStyleSample: async (text) => {
      try {
        await api.style.addSample(styleScope(get().dataScope), text)
        await get().refreshStyle()
      } catch (e) {
        get().toast((e as Error).message, 'error')
      }
    },
    deleteStyleSample: async (id) => {
      await api.style.deleteSample(id)
      set((s) => ({ styleSamples: s.styleSamples.filter((x) => x.id !== id) }))
      await get().refreshStyle()
    },

    // ---- activity monitor ----
    refreshActivity: async () => {
      try {
        set({ activity: await api.activity.status() })
      } catch {
        /* the panel shows whatever it last had; a failed poll is not worth a toast */
      }
    },
    loadActivity: async () => {
      await get().refreshActivity()
      const [events, summaries, context] = await Promise.all([
        api.activity.events(24, 300).catch(() => []),
        api.activity.summaries(7).catch(() => []),
        api.activity.context().catch(() => null)
      ])
      set({ activityEvents: events, activitySummaries: summaries, activityContext: context })
    },
    setActivityConfig: async (patch) => {
      try {
        set({ activity: await api.activity.config(patch) })
      } catch (e) {
        get().toast((e as Error).message, 'error')
      }
    },
    toggleActivitySignal: async (signal) => {
      const cur = get().activity?.config.signals
      if (!cur) return
      await get().setActivityConfig({ signals: { ...cur, [signal]: !cur[signal] } })
    },
    startActivity: async () => {
      try {
        set({ activity: await api.activity.start() })
        get().toast('Activity monitor on')
      } catch (e) {
        get().toast((e as Error).message, 'error')
      }
    },
    stopActivity: async () => {
      set({ activity: await api.activity.stop() })
      get().toast('Activity monitor off')
    },
    grantActivityPermission: async (id, browser = '') => {
      try {
        const { result, status } = await api.activity.requestPermission(id, browser)
        set({ activity: status })
        // macOS shows each of these at most once per app, so the note matters more than the state:
        // it is what tells the user to go to the pane by hand, or to restart the app.
        if (result.note) get().toast(result.note, result.prompted ? 'info' : 'error')
      } catch (e) {
        get().toast((e as Error).message, 'error')
      }
    },
    openActivitySettings: async (id) => {
      try {
        await api.activity.openPermissionSettings(id)
      } catch (e) {
        get().toast((e as Error).message, 'error')
      }
    },
    setPalantirMode: async (on) => {
      try {
        set({ activity: await api.activity.palantir(on) })
        get().toast(on ? 'Palantir mode on — recording everything' : 'Palantir mode off — previous settings restored')
      } catch (e) {
        get().toast((e as Error).message, 'error')
      }
    },
    pauseActivity: async (minutes = 30) => set({ activity: await api.activity.pause(minutes) }),
    resumeActivity: async () => set({ activity: await api.activity.resume() }),
    rollupActivity: async () => {
      set({ activityBusy: true })
      try {
        const { summary, status } = await api.activity.rollup()
        set({ activity: status })
        get().toast(summary ? `Summarized: ${summary.headline}` : 'Nothing new to summarize')
        await get().loadActivity()
      } catch (e) {
        get().toast((e as Error).message, 'error')
      } finally {
        set({ activityBusy: false })
      }
    },
    refreshActivityProfile: async () => {
      set({ activityBusy: true })
      try {
        await api.activity.refreshProfile()
        get().toast('Rebuilt the work profile')
        await get().loadActivity()
      } catch (e) {
        get().toast((e as Error).message, 'error')
      } finally {
        set({ activityBusy: false })
      }
    },
    deleteActivityEvent: async (id) => {
      await api.activity.deleteEvent(id)
      set((s) => ({ activityEvents: s.activityEvents.filter((e) => e.id !== id) }))
    },
    deleteActivitySummary: async (id) => {
      await api.activity.deleteSummary(id)
      set((s) => ({ activitySummaries: s.activitySummaries.filter((x) => x.id !== id) }))
      set({ activityContext: await api.activity.context().catch(() => get().activityContext) })
    },
    purgeActivity: async (scope) => {
      const { deleted, status } = await api.activity.purge(scope)
      set({ activity: status })
      get().toast(`Deleted ${deleted.events} samples and ${deleted.summaries} summaries`)
      await Promise.all([get().loadActivity(), get().loadActivityInsights()])
    },
    loadActivityInsights: async () => {
      try {
        set({ activityInsights: await api.activity.insights() })
      } catch {
        /* same as the status poll: the panel keeps what it had */
      }
    },
    refreshActivityInsights: async (deep = false) => {
      set({ activityInsightsBusy: true })
      try {
        const out = deep ? await api.activity.refreshInsights() : await api.activity.mineInsights()
        set({ activityInsights: out })
        const open = out.counts?.open ?? 0
        get().toast(deep
          ? (open ? `${open} suggestion${open === 1 ? '' : 's'} waiting` : 'Nothing new worth suggesting')
          : `Re-read ${out.patterns.length} patterns`)
        if (deep) await get().refreshMemories()   // habits land in the memory panel
      } catch (e) {
        get().toast((e as Error).message, 'error')
      } finally {
        set({ activityInsightsBusy: false })
      }
    },
    setInsightStatus: async (id, status, note = '') => {
      try {
        await api.activity.setInsightStatus(id, status, note)
        await get().loadActivityInsights()
      } catch (e) {
        get().toast((e as Error).message, 'error')
      }
    },
    applyInsight: async (id) => {
      try {
        const out = await api.activity.applyInsight(id)
        await get().loadActivityInsights()
        if (out.type === 'prompt' && out.prompt) {
          // Setting the thing up is a conversation with tool approvals in it, so the suggestion
          // hands the message over rather than acting: a fresh chat, pre-loaded, nothing sent yet
          // until the user is looking at it.
          get().newChat(null)
          await get().send(out.prompt)
          return
        }
        if (out.type === 'todo' && out.todo) {
          await get().refreshTodos()
          get().toast(`Added todo: ${out.todo.title}`)
        } else if (out.type === 'memory' && out.memory) {
          await get().refreshMemories()
          get().toast('Saved to memory')
        } else {
          get().toast(out.how || 'Marked as accepted')
        }
      } catch (e) {
        get().toast((e as Error).message, 'error')
      }
    },
    forgetActivityHabit: async (id) => {
      try {
        await api.activity.forgetHabit(id)
        await Promise.all([get().loadActivityInsights(), get().refreshMemories()])
        get().toast('Forgotten, memory and all')
      } catch (e) {
        get().toast((e as Error).message, 'error')
      }
    },
    uploadDocuments: async (files, projectId) => {
      const saved: UploadOutcome[] = []
      for (const f of Array.from(files)) {
        try {
          const doc = (await api.documents.upload(projectId, f)) as UploadResult
          // An older backend says nothing about readability; its files count as readable, as before.
          const r: UploadOutcome = { name: doc.name || f.name, readable: doc.readable !== false, reason: doc.reason ?? null }
          saved.push(r)
          const t = uploadToast(r)
          get().toast(t.text, t.kind)
        } catch (e) {
          get().toast(`${f.name}: ${(e as Error).message}`, 'error')
        }
      }
      await Promise.all([get().refreshDocuments(), get().refreshProjects()])
      return saved
    },
    pinDocument: async (id, pinned) => {
      const flip = (v: boolean): void => set((s) => ({ documents: s.documents.map((d) => (d.id === id ? { ...d, pinned: v ? 1 : 0 } : d)) }))
      flip(pinned)
      try { await api.documents.pin(id, pinned) } catch (e) { flip(!pinned); get().toast((e as Error).message, 'error') }
    },
    deleteDocument: async (id) => {
      const name = get().documents.find((d) => d.id === id)?.name
      await api.documents.delete(id)
      set((s) => ({ documents: s.documents.filter((d) => d.id !== id) }))
      void get().refreshProjects()
      get().offerUndo(name ? `“${name}”` : 'document', [{ type: 'document', id }])
    },

    refreshDashboard: async () => {
      void get().refreshAgentInbox()
      try {
        const dashboard = await api.dashboard()
        set({ dashboard, google: dashboard.google })
      } catch (e) {
        get().toast(`Dashboard: ${(e as Error).message}`, 'error')
      }
    },
    refreshRecap: async (force = false) => {
      set({ recapLoading: true })
      try {
        set({ recap: await api.recap(force) })
      } catch (e) {
        if (force) get().toast(`Recap: ${(e as Error).message}`, 'error')
      } finally {
        set({ recapLoading: false })
      }
    },
    refreshAgentInbox: async () => {
      try {
        set({ agentInbox: await api.inbox() })
      } catch (e) {
        /* the inbox is a card on Today, not the shell: a failed read must not toast on every refresh */
        void e
      }
    },
    refreshJobs: async () => {
      try {
        set({ jobs: await api.jobs.list() })
      } catch (e) {
        get().toast(`Jobs: ${(e as Error).message}`, 'error')
      }
    },
    createJob: async (input) => {
      try {
        const job = await api.jobs.create(input)
        set((s) => ({ jobs: [...s.jobs, job].sort((a, b) => a.name.localeCompare(b.name)) }))
        get().toast(`Scheduled: ${job.name}`, 'info')
        void get().refreshAgentInbox()
        return true
      } catch (e) {
        get().toast(`Jobs: ${(e as Error).message}`, 'error')
        return false
      }
    },
    deleteJob: async (id) => {
      try {
        await api.jobs.delete(id)
        set((s) => ({ jobs: s.jobs.filter((j) => j.id !== id) }))
        void get().refreshAgentInbox()
      } catch (e) {
        get().toast(`Jobs: ${(e as Error).message}`, 'error')
      }
    },
    setJobEnabled: async (id, enabled) => {
      try {
        const job = await api.jobs.update(id, { enabled })
        set((s) => ({ jobs: s.jobs.map((x) => (x.id === id ? job : x)) }))
        void get().refreshAgentInbox()
      } catch (e) {
        get().toast(`Jobs: ${(e as Error).message}`, 'error')
      }
    },
    runJobNow: async (id) => {
      try {
        const { run_id } = await api.jobs.runNow(id)
        get().toast(run_id ? 'Job started. It will show up under “While you were away”.' : 'Job did not start', run_id ? 'info' : 'error')
        void get().refreshJobs()
      } catch (e) {
        get().toast(`Jobs: ${(e as Error).message}`, 'error')
      }
    },
    decideProposal: async (id, accept, args) => {
      try {
        const res = accept ? await api.proposals.accept(id, args) : await api.proposals.reject(id)
        if (accept && !res.ok) get().toast(`That did not go through: ${res.proposal.error ?? 'unknown error'}`, 'error')
        else get().toast(accept ? 'Done — that one actually ran.' : 'Dropped.', 'info')
      } catch (e) {
        get().toast((e as Error).message, 'error')
      } finally {
        void get().refreshAgentInbox()
      }
    },
    approveTool: async (callId, decision, conversationId, opts) => {
      const id = conversationId ?? get().focusedConversationId
      if (!id) return
      // Clears the card's pending state. The tool_result event fills in the rest, and the count settles
      // now rather than when the tool returns, since an external action can take seconds.
      const clear = (approval?: ApprovalDecision): void =>
        patchSession(id, (s) => {
          const conversation = { ...s.conversation, messages: (s.conversation.messages ?? []).map((m) => ({ ...m, tool_events: (m.tool_events ?? []).map((t) => (t.id === callId
            ? { ...t, needs_approval: false, ...(approval ? { approval } : {}), ...(approval && opts?.arguments && approval !== 'deny' ? { arguments: opts.arguments, original_arguments: t.arguments, edited_arguments: opts.arguments, edited_by: 'user' as const } : {}) }
            : t)) })) }
          const pendingApprovals = countApprovals(conversation)
          return { ...s, conversation, pendingApprovals, status: settleApprovals(s.status, pendingApprovals) }
        })
      try {
        await api.approve(callId, decision, opts)
        clear(decision)
        // A card answered from a desk pane is also in that desk's `approvals`; re-read the desk so it leaves the list
        // (answerDeskCard does the same for the old banner path).
        const open = get().activeDeskId
        if (open && get().activeDesk?.approvals?.some((a) => a.call_id === callId)) void get().openDesk(open)
      } catch (e) {
        // A 404 means the approval is already answered (a double click, another window) or its run is
        // gone: the card is stale, so drop its buttons quietly instead of toasting an error per click.
        if ((e as { status?: number }).status === 404) clear()
        else get().toast((e as Error).message, 'error')
      }
    },
    refreshGoogle: async () => {
      try {
        set({ google: await api.google.status() })
        void api.tools().then((t) => set({ tools: t.tools })).catch(() => undefined)
        void get().refreshTasksSync()
        void get().refreshTodoCalendar()
      } catch { /* ignore */ }
    },
    refreshTasksSync: async () => {
      try {
        set({ tasksSync: await api.google.tasksSync() })
      } catch { /* ignore */ }
    },
    refreshTodoCalendar: async () => {
      try {
        set({ todoCalendar: await api.google.todoCalendar() })
      } catch { /* ignore */ }
    },
    setTodoCalendar: async (patch) => {
      try {
        set({ todoCalendar: await api.google.todoCalendarConfig(patch) })
        // Turning it on mirrors in the background; show the result as soon as it lands.
        if (patch.enabled) void get().runTodoCalendar()
      } catch (e) {
        get().toast((e as Error).message, 'error')
      }
    },
    runTodoCalendar: async () => {
      set((s) => ({ todoCalendar: s.todoCalendar && { ...s.todoCalendar, syncing: true } }))
      try {
        set({ todoCalendar: await api.google.todoCalendarRun() })
        await get().refreshTodos()
      } catch (e) {
        get().toast((e as Error).message, 'error')
        void get().refreshTodoCalendar()
      }
    },
    setTasksSync: async (patch) => {
      try {
        set({ tasksSync: await api.google.tasksSyncConfig(patch) })
      } catch (e) {
        get().toast((e as Error).message, 'error')
      }
    },
    runTasksSync: async () => {
      set((s) => ({ tasksSync: s.tasksSync && { ...s.tasksSync, syncing: true } }))
      try {
        set({ tasksSync: await api.google.tasksSyncRun() })
        await get().refreshTodos()
        void get().refreshDashboard()
      } catch (e) {
        get().toast((e as Error).message, 'error')
        void get().refreshTasksSync()
      }
    },
    connectGoogle: async () => {
      try {
        // Re-authing an already-connected account looks identical unless we watch for a new
        // token, so remember which one we had before opening the browser.
        const before = get().google?.connected_at ?? null
        const { url } = await api.google.start()
        window.open(url, '_blank')
        // poll until the callback lands
        const started = Date.now()
        const timer = setInterval(async () => {
          const st = await api.google.status().catch(() => null)
          const fresh = !!st?.connected && st.connected_at !== before
          if (fresh || Date.now() - started > 180_000) {
            clearInterval(timer)
            if (fresh && st) {
              clearViews()
              set({ google: st })
              get().toast(`Connected ${st.email ?? 'Google account'}`)
              void get().refreshDashboard()
              void api.tools().then((t) => set({ tools: t.tools })).catch(() => undefined)
            }
          }
        }, 1500)
      } catch (e) {
        get().toast((e as Error).message, 'error')
      }
    },
    disconnectGoogle: async () => {
      set({ google: await api.google.disconnect() })
      clearViews()
      void get().refreshDashboard()
      void api.tools().then((t) => set({ tools: t.tools })).catch(() => undefined)
    },

    refreshTodos: async (scope = 'all', includeDone = false, sort = 'due') => set({ todos: await api.todos.list(scope, includeDone, '', sort) }),
    addTodo: async (t) => {
      await api.todos.create(t)
      await Promise.all([get().refreshTodos(), get().refreshDashboard()])
    },
    updateTodo: async (id, patch) => {
      const recurs = patch.done === true && !!get().todos.find((x) => x.id === id)?.repeat
      const t = await api.todos.update(id, patch)
      if (recurs) void get().refreshTodos()  // the next instance was just created server-side
      set((s) => ({ todos: s.todos.map((x) => (x.id === id ? t : x)), dashboard: s.dashboard && { ...s.dashboard, todos: s.dashboard.todos.map((x) => (x.id === id ? t : x)).filter((x) => !x.done) } }))
    },
    deleteTodo: async (id) => {
      const title = get().todos.find((x) => x.id === id)?.title
      await api.todos.delete(id)
      set((s) => ({ todos: s.todos.filter((x) => x.id !== id), dashboard: s.dashboard && { ...s.dashboard, todos: s.dashboard.todos.filter((x) => x.id !== id) } }))
      get().offerUndo(title ? `todo “${title}”` : 'todo', [{ type: 'todo', id }])
    }
  }
})

/**
 * Pin a session for as long as a surface is showing it, and release it on unmount. A retained
 * session is never an LRU victim, which is the only protection a canvas window has: the canvas view
 * does not focus conversations, and a widget's loader effect only re-runs when its `ref_id` changes.
 */
/**
 * pagehide cannot await, and a plain fetch dies with the page, so the buffered doc edits go out as a
 * keepalive PUT. ponytail: keepalive bodies cap at 64 KB, so a longer doc still loses its last
 * second on reload/quit; a main-process flush on before-quit would lift that.
 */
export const flushDocOnUnload = (): void => {
  if (saveTimer) { clearTimeout(saveTimer); saveTimer = null }
  const { activeDoc: doc, docDraft, docTitleDraft } = useStore.getState()
  const edits = doc && docEdits(doc, docDraft, docTitleDraft)
  if (!doc || !edits) return
  void fetch(`${getBase()}/docs/${doc.id}`, {
    method: 'PUT',
    keepalive: true,
    headers: { 'Content-Type': 'application/json', ...(getToken() ? { 'X-Personal-OS-Token': getToken() } : {}) },
    body: JSON.stringify({ ...edits, base_updated_at: doc.updated_at })
  }).catch(() => undefined)
}

// Open doc tabs survive a reload: written whenever the tabs or the active doc change.
const DOC_TABS_KEY = 'grain.docTabs'
useStore.subscribe((s, prev) => {
  if (s.docTabs === prev.docTabs && s.activeDoc?.id === prev.activeDoc?.id) return
  try { localStorage.setItem(DOC_TABS_KEY, JSON.stringify({ tabs: s.docTabs, active: s.activeDoc?.id ?? null })) } catch { /* private window */ }
})

/** Reopens the tabs saved by the subscriber above, dropping any doc that no longer exists. */
export const restoreDocTabs = async (): Promise<void> => {
  if (useStore.getState().docTabs.length) return
  let saved: { tabs?: unknown; active?: unknown }
  try { saved = JSON.parse(localStorage.getItem(DOC_TABS_KEY) ?? '{}') } catch { return }
  if (!Array.isArray(saved.tabs) || !saved.tabs.length) return
  const live = new Set((await api.docs.list('all', '').catch(() => [] as Doc[])).map((d) => d.id))
  const tabs = saved.tabs.filter((t): t is string => typeof t === 'string' && live.has(t))
  if (!tabs.length || useStore.getState().docTabs.length) return
  useStore.setState({ docTabs: tabs })
  await useStore.getState().openDoc(typeof saved.active === 'string' && tabs.includes(saved.active) ? saved.active : tabs[tabs.length - 1])
}

export const retainSession = (conversationId: string): (() => void) => {
  const first = !retained.has(conversationId)
  retained.set(conversationId, (retained.get(conversationId) ?? 0) + 1)
  // A surface mounting this conversation is where the user reads it, so what finished while it was away is read.
  if (first && (useStore.getState().sessions[conversationId]?.unread ?? 0) > 0) useStore.getState().clearSessionStatus(conversationId)
  return () => {
    const n = (retained.get(conversationId) ?? 0) - 1
    if (n > 0) return void retained.set(conversationId, n)
    retained.delete(conversationId)
    // A window that was open until now is the most recently touched, not the stalest.
    useStore.setState((s) => (s.sessions[conversationId] ? { sessions: { ...s.sessions, [conversationId]: { ...s.sessions[conversationId], touchedAt: Date.now() } } } : {}))
  }
}

export const useProject = (id: string | null | undefined): Project | undefined =>
  useStore((s) => (id ? s.projects.find((p) => p.id === id) : undefined))

const pick = (s: State, convId?: string): ChatSession | undefined => s.sessions[convId ?? s.focusedConversationId ?? '']

/** The focused conversation: what `active` used to be. Every selector below returns state as-is. */
export const selectActive = (s: State): Conversation | null => pick(s)?.conversation ?? null

export const useSession = (convId?: string): ChatSession | undefined => useStore((s) => pick(s, convId))
export const useConversation = (convId?: string): Conversation | null => useStore((s) => pick(s, convId)?.conversation ?? null)
export const useSessionStatus = (convId?: string): SessionStatus => useStore((s) => pick(s, convId)?.status ?? 'idle')
/** `useSessionStatus`, falling back to the app topic's live run for a chat with no session in this window. */
export const useChatPulse = (convId?: string): SessionStatus =>
  useStore((s) => pulseStatus(pick(s, convId)?.status ?? 'idle', convId ? s.liveRuns[convId] : undefined))
/** Sends the run has not confirmed yet, for the dimmed bubbles under the transcript. */
export const usePendingSends = (convId?: string): readonly PendingSend[] => useStore((s) => pick(s, convId)?.pendingSends ?? EMPTY_PENDING)

export const useIsStreaming = (convId?: string): boolean => useStore((s) => !!pick(s, convId)?.streaming?.answering)
export const useStreamingMessageId = (convId?: string): string | null =>
  useStore((s) => {
    const st = pick(s, convId)?.streaming
    return st?.answering ? st.messageId : null
  })
export const useIsStopping = (convId?: string): boolean => useStore((s) => !!pick(s, convId)?.streaming?.stopping)
export const useUnread = (convId?: string): number => useStore((s) => pick(s, convId)?.unread ?? 0)

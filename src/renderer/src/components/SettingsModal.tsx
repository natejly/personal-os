import { useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { X, Search, Plug, Cpu, Bot, Palette, ShieldCheck, SlidersHorizontal, KeyRound, Gauge, Brain, PanelLeft, type Icon } from '../icons'
import { useStore } from '../store'
import { modeOf, MODES } from '../lib/permissionMode'
import { resolveTab, searchSettings, type SettingsTab } from '../lib/settingsTabs'
import { useOnboarding } from './onboarding/onboardingStore'
import { api } from '../lib/api'
import { useModal } from '../lib/useModal'
import { applyAppearance } from '../lib/appearance'
import { modelLabel } from '../lib/modelLabel'
import { chatRows } from '../lib/pickerModels'
import type { Settings } from '@shared/types'
import ModelTab from './settings/ModelTab'
import UsageTab from './settings/UsageTab'
import PermissionsTab from './settings/PermissionsTab'
import IntegrationsTab from './settings/IntegrationsTab'
import AgentsTab from './settings/AgentsTab'
import MemoryTab from './settings/MemoryTab'
import GeneralTab, { THEMES } from './settings/GeneralTab'
import SidebarTab from './settings/SidebarTab'
import SystemTab from './settings/SystemTab'
import AdvancedTab from './settings/AdvancedTab'
import { useShortcuts } from './settings/useShortcuts'

type Tab = SettingsTab

const TABS: { id: Tab; label: string; icon: Icon }[] = [
  { id: 'appearance', label: 'General', icon: Palette },
  { id: 'model', label: 'Models', icon: Cpu },
  { id: 'agents', label: 'Agents', icon: Bot },
  { id: 'memory', label: 'Memory', icon: Brain },
  { id: 'integrations', label: 'Integrations', icon: Plug },
  { id: 'permissions', label: 'Permissions', icon: ShieldCheck },
  { id: 'system', label: 'System access', icon: KeyRound },
  { id: 'sidebar', label: 'Sidebar', icon: PanelLeft },
  { id: 'usage', label: 'Usage', icon: Gauge },
  { id: 'advanced', label: 'Advanced', icon: SlidersHorizontal }
]

/**
 * One save model: every control that writes /settings edits `draft`, and Save writes what changed and closes.
 * Closing with changes asks first. What stays immediate is an action rather than a preference: the Google client
 * credentials (the sign-in opens the browser and needs them stored), and the panels that call their own endpoints
 * (key rows, pairing, voice, planner, grants, System access, Usage prices).
 */
export default function SettingsModal(): JSX.Element {
  const settings = useStore((s) => s.settings)
  const models = useStore((s) => s.models)
  const view = useStore((s) => s.view)
  const { saveSettings, setSettingsOpen, setView, toast } = useStore()
  const [draft, setDraft] = useState<Settings>(settings)
  const [saving, setSaving] = useState(false)
  // Set while the footer asks whether to throw the draft away; it remembers what the answer leads to.
  const [pending, setPending] = useState<'close' | 'setup' | null>(null)
  const { shortcuts, apply: applyShortcuts } = useShortcuts()
  const storeTab = useStore((s) => s.settingsTab)
  const storeGroup = useStore((s) => s.settingsGroup)
  const [tab, setTabState] = useState<Tab>(() => { const t = resolveTab(storeTab).tab; return TABS.some((x) => x.id === t) ? t : 'appearance' })
  // The store keeps the shown tab, so openSettings from a toast or a link switches the open modal too.
  const setTab = (t: Tab): void => { setTabState(t); useStore.setState({ settingsTab: t }) }
  // Advanced groups start collapsed; one opens when Settings was asked for a topic inside it.
  const [openGroups, setOpenGroups] = useState<ReadonlySet<string>>(() => new Set(useStore.getState().settingsGroup ? [useStore.getState().settingsGroup as string] : []))
  const toggleGroup = (id: string, open: boolean): void => setOpenGroups((g) => { const n = new Set(g); if (open) n.add(id); else n.delete(id); return n })
  const gp = { openGroups, toggle: toggleGroup }
  const [q, setQ] = useState('')
  const [jump, setJump] = useState<string | null>(null)
  const hits = searchSettings(q)
  const google = useStore((s) => s.google)
  /** Go to a search hit: its tab, any collapsed group around it opened, then scrolled to once it has rendered. */
  const goTo = (h: { tab: Tab; sec: string }): void => {
    setTab(h.tab)
    if (h.tab === 'advanced' || h.sec === 'search') setOpenGroups((g) => new Set(g).add(h.sec))
    setJump(h.sec)
    setQ('')
  }
  useEffect(() => {
    if (!jump) return
    const el = document.getElementById(`sec-${jump}`)
    if (!el) return
    el.scrollIntoView({ block: 'start', behavior: 'smooth' })
    el.classList.add('set-flash')
    const t = window.setTimeout(() => el.classList.remove('set-flash'), 1600)
    setJump(null)
    return () => window.clearTimeout(t)
  }, [jump, tab, openGroups])
  useEffect(() => {
    if (TABS.some((x) => x.id === storeTab)) setTabState(storeTab)
    if (storeGroup) setOpenGroups((g) => new Set(g).add(storeGroup))
  }, [storeTab, storeGroup])
  const mode = modeOf(settings)
  const tabRefs = useRef<Partial<Record<Tab, HTMLButtonElement | null>>>({})
  const patch = (p: Partial<Settings>): void => setDraft((d) => ({ ...d, ...p }))

  // The backend changes settings on its own (an "always allow" writes a permission rule, a tool toggle),
  // so the store's copy can be stale. Re-read it, and seed the draft from it unless editing has begun.
  // `base` is what the draft was seeded from, so Save sends only the fields edited here, and it is
  // also what "unsaved changes" is measured against.
  const base = useRef(settings)
  useEffect(() => {
    const initial = base.current
    api.settings.get().then((fresh) => {
      useStore.setState({ settings: fresh })
      setDraft((d) => {
        if (d !== initial) return d
        base.current = fresh
        return fresh
      })
    }).catch(() => undefined)
  }, [])

  const changedFields = (next: Settings): Partial<Settings> => {
    const seed = base.current as unknown as Record<string, unknown>
    return Object.fromEntries(Object.entries(next).filter(([k, v]) => JSON.stringify(v) !== JSON.stringify(seed[k]))) as Partial<Settings>
  }
  const dirty = Object.keys(changedFields(draft)).length > 0

  /** The Google client id and secret, written ahead of Save: the sign-in opens the browser and reads them from storage. */
  const saveGoogleCreds = async (): Promise<void> => {
    const p = { googleClientId: draft.googleClientId, googleClientSecret: draft.googleClientSecret }
    await saveSettings(p)
    base.current = { ...base.current, ...p }
    setDraft((d) => ({ ...d, ...p }))
  }

  /** Reset the onboarding stamp, then show the wizard over the app. The modal's draft is dropped with it. */
  const rerunSetup = async (): Promise<void> => {
    try {
      await useOnboarding.getState().rerun()
      setSettingsOpen(false)
    } catch (e) {
      toast((e as Error).message, 'error')
    }
  }
  // Every way out (X, Escape, the backdrop, Cancel) comes through here, so a changed draft is never
  // dropped without the footer asking first.
  const requestClose = (): void => {
    if (dirty) setPending((p) => p ?? 'close')
    else setSettingsOpen(false)
  }
  const discard = (): void => {
    const then = pending
    setPending(null)
    if (then === 'setup') void rerunSetup()
    else setSettingsOpen(false)
  }
  const { titleId, backdrop, modal } = useModal(requestClose)
  // The question's buttons unmount with it; park focus on the dialog so the next Tab starts inside it.
  const keepEditing = (): void => {
    setPending(null)
    modal.ref.current?.focus()
  }

  // In a narrow window the tabs are a horizontal strip; keep the selected one on screen.
  useEffect(() => { tabRefs.current[tab]?.scrollIntoView({ block: 'nearest', inline: 'nearest' }) }, [tab])
  // Preview the appearance options on the page while the modal is open; discard restores saved values.
  useEffect(() => {
    applyAppearance(draft)
  }, [draft.theme, draft.accent, draft.textTone, draft.surfaceTone, draft.uiFont, draft.codeFont, draft.accentCustom])
  useEffect(() => () => applyAppearance(useStore.getState().settings), [])

  /** A rejected accelerator keeps the modal open: it is the only place the reason is readable. */
  const save = async (): Promise<void> => {
    setSaving(true)
    try {
      const bound = await applyShortcuts(draft, settings)
      if (!bound) { setTab('appearance'); setJump('shortcuts'); return }
      // Only what was edited here: a whole-draft PUT would put back anything the backend changed since it was taken.
      const changed = changedFields({ ...draft, ...bound })
      try {
        if (Object.keys(changed).length) await saveSettings(changed)
      } catch (e) {
        // The dialog stays open with the draft intact, so nothing typed is lost.
        return toast((e as Error).message, 'error')
      }
      base.current = { ...base.current, ...changed }
      // The app is not left parked on a view that just went away.
      if ((changed.hiddenViews as string[] | undefined)?.includes(view)) setView('chat')
      setSettingsOpen(false)
    } finally {
      setSaving(false)
    }
  }

  /** One line under a tab's name, from what the store already holds. */
  const hintOf = (id: Tab): string | null => {
    if (id === 'model') return settings.defaultModel ? modelLabel(settings.defaultModel) : null
    if (id === 'permissions') return MODES.find((m) => m.id === mode)?.label ?? null
    if (id === 'integrations') return google?.connected ? google.email ?? 'Google connected' : 'Google not connected'
    if (id === 'appearance') return THEMES.find((t) => t.id === settings.theme)?.label ?? null
    return null
  }

  // Vertical tablist: arrows move and select, Home/End jump to the ends.
  const onTabKey = (e: KeyboardEvent<HTMLDivElement>): void => {
    if ((e.target as HTMLElement).tagName === 'INPUT') return
    const i = TABS.findIndex((t) => t.id === tab)
    const next = e.key === 'ArrowDown' ? (i + 1) % TABS.length
      : e.key === 'ArrowUp' ? (i - 1 + TABS.length) % TABS.length
        : e.key === 'Home' ? 0 : e.key === 'End' ? TABS.length - 1 : -1
    if (next < 0) return
    e.preventDefault()
    setTab(TABS[next].id)
    tabRefs.current[TABS[next].id]?.focus()
  }
  // Escape answers the footer's question with "keep editing" rather than asking it again.
  const onModalKey = (e: KeyboardEvent<HTMLDivElement>): void => {
    if (e.key !== 'Escape' || !pending) return
    e.preventDefault()
    keepEditing()
  }

  const tabProps = { draft, patch }

  return (
    <div className="modal-backdrop" {...backdrop}>
      <div className="modal settings-modal" {...modal} onKeyDown={onModalKey}>
        <header><h2 id={titleId}>Settings</h2><button className="icon-btn" aria-label="Close settings" title="Close" onClick={requestClose}><X size={16} /></button></header>

        <div className="settings-body">
          <div className="settings-rail">
            {/* modal-free keeps the generic `.modal label` / `.modal input` form rules off the search field. */}
            <div className="settings-search modal-free">
              <Search size={14} aria-hidden />
              <input type="search" value={q} aria-label="Search settings" placeholder="Search settings" spellCheck={false}
                onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter' && hits[0]) goTo(hits[0]) }} />
            </div>
            {q.trim() ? (
              <div className="settings-hits" role="list" aria-label="Search results">
                {hits.length ? hits.map((h) => (
                  <button key={`${h.tab}-${h.sec}`} type="button" role="listitem" onClick={() => goTo(h)}>
                    <span>{h.title}</span><small>{TABS.find((t) => t.id === h.tab)?.label}</small>
                  </button>
                )) : <p className="muted small">No settings match.</p>}
              </div>
            ) : (
              <nav className="settings-tabs" role="tablist" aria-orientation="vertical" aria-label="Settings sections" onKeyDown={onTabKey}>
                {TABS.map(({ id, label, icon: Icon }) => {
                  const hint = hintOf(id)
                  return (
                    <button key={id} ref={(el) => { tabRefs.current[id] = el }} role="tab" id={`settings-tab-${id}`} aria-controls="settings-pane"
                      aria-selected={tab === id} tabIndex={tab === id ? 0 : -1} className={tab === id ? 'active' : undefined} onClick={() => setTab(id)}>
                      <Icon size={15} /><span>{label}{hint && <small className="tab-hint">{hint}</small>}</span>
                    </button>
                  )
                })}
              </nav>
            )}
          </div>
          <div className="settings-pane" id="settings-pane" role="tabpanel" aria-labelledby={`settings-tab-${tab}`}>
            {tab === 'model' && <ModelTab {...tabProps} onRunSetup={() => (dirty ? setPending('setup') : void rerunSetup())} />}
            {tab === 'usage' && <UsageTab />}
            {tab === 'permissions' && <PermissionsTab {...tabProps} />}
            {tab === 'integrations' && <IntegrationsTab {...tabProps} onSaveCreds={saveGoogleCreds} />}
            {tab === 'agents' && <AgentsTab {...tabProps} />}
            {tab === 'memory' && <MemoryTab {...tabProps} {...gp} />}
            {tab === 'appearance' && <GeneralTab {...tabProps} shortcuts={shortcuts} />}
            {tab === 'sidebar' && <SidebarTab {...tabProps} />}
            {tab === 'system' && <SystemTab />}
            {tab === 'advanced' && <AdvancedTab {...tabProps} {...gp} />}

            {/* Outside the tabs: the model fields of several sections list from it. */}
            <datalist id="model-options">{chatRows(models).map((m) => <option key={m.id} value={m.id} label={m.provider ? `${m.id} — ${m.provider}` : undefined} />)}</datalist>
          </div>
        </div>

        <footer>
          {pending ? (
            <>
              <span className="footer-note ask" role="alert">Discard unsaved changes?</span>
              <button className="ghost-btn" autoFocus onClick={keepEditing}>Keep editing</button>
              <button className="ghost-btn danger" onClick={discard}>Discard</button>
            </>
          ) : dirty ? (
            <>
              <span className="footer-note">Unsaved changes</span>
              <button className="ghost-btn" onClick={requestClose}>Cancel</button>
              <button className="primary-btn" disabled={saving} onClick={() => void save()}>Save</button>
            </>
          ) : (
            <button className="primary-btn" onClick={requestClose}>Done</button>
          )}
        </footer>
      </div>
    </div>
  )
}

/** The tab list, for the command palette. */
export { TABS as SETTINGS_TABS }

import { Fragment, useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { useSlidingPill } from "../lib/useSlidingPill"
import { X, Search } from '../icons'
import { useShallow } from 'zustand/react/shallow'
import { useStore, setSettingsGuard } from '../store'
import { useTodos } from '../extensions/todos/store'
import { modeOf, MODES } from '../lib/permissionMode'
import { RUNTIME_EXT, resolveTab, searchSettings, type SettingsSectionId, type SettingsTab } from '../lib/settingsTabs'
import { SETTINGS_SECTIONS } from '../lib/settingsSections'
import { hintOf, locate, panelsFor, startsCluster } from '../lib/settingsShell'
import { api } from '../lib/api'
import { useModal } from '../lib/useModal'
import { applyAppearance } from '../lib/appearance'
import { modelLabel } from '../lib/modelLabel'
import { settingsPanels } from '../extensions/host'
import { viewHidden } from '../moduleToggles'
import type { ExtensionInfo, RuntimeView, Settings } from '@shared/types'
import { runtimeSlotsShown } from '../lib/runtime'
import ModelTab from './settings/ModelTab'
import { SETTINGS_JUMP } from './settings/Disclosure'
import PermissionsTab from './settings/PermissionsTab'
import IntegrationsTab from './settings/IntegrationsTab'
import AgentsTab from './settings/AgentsTab'
import ContextTab from './settings/ContextTab'
import GeneralTab, { THEMES } from './settings/GeneralTab'
import SidebarTab from './settings/SidebarTab'
import SystemTab from './settings/SystemTab'
import AdvancedTab from './settings/AdvancedTab'
import { EffectiveProvider } from './settings/useEffective'
import { Sec } from './settings/shared'
import './settings/RuntimeTab'
import '../styles/settings-shell.css'
import './settings/ExtensionsSection'
import { useShortcuts } from './settings/useShortcuts'

type Tab = SettingsSectionId

/**
 * One save model: every control that writes /settings edits `draft`, and Save writes what changed and closes.
 * Closing with changes asks first. What stays immediate is an action rather than a preference: the Google client
 * credentials (the sign-in opens the browser and needs them stored), and the panels that call their own endpoints
 * (key rows, pairing, voice, planner, grants, System access, Usage prices).
 */
export default function SettingsModal(): JSX.Element {
  const tabsPill = useSlidingPill<HTMLElement>()
  const settings = useStore((s) => s.settings)
  const view = useStore((s) => s.view)
  const { saveSettings, setSettingsOpen, setView, toast } = useStore(useShallow((s) => ({ saveSettings: s.saveSettings, setSettingsOpen: s.setSettingsOpen, setView: s.setView, toast: s.toast })))
  // Panels that extensions contribute, shown inside their section while their extension is on.
  const panels = settingsPanels.filter((p) => !viewHidden(settings, p.extension))
  const [draft, setDraft] = useState<Settings>(settings)
  const [saving, setSaving] = useState(false)
  // Set while the footer asks whether to throw the draft away; it remembers what the answer leads to.
  const [pending, setPending] = useState<'close' | null>(null)
  const { shortcuts, apply: applyShortcuts } = useShortcuts()
  const storeTab = useStore((s) => s.settingsTab)
  const storeGroup = useStore((s) => s.settingsGroup)
  /** Any tab id the store may hold (a legacy name, a section, a registered panel's `ext:` id) as a section plus the anchor to scroll to. */
  const place = (id: string): { section: Tab; anchor?: string } => {
    const r = resolveTab(id)
    const at = locate(r.tab, panels)
    return { section: at.section, anchor: at.anchor ?? r.group }
  }
  const [tab, setTabState] = useState<Tab>(() => place(storeTab).section)
  // The store keeps the shown section, so openSettings from a toast or a link switches the open modal too.
  const setTab = (t: Tab): void => { setTabState(t); useStore.setState({ settingsTab: t }) }
  // Groups that start collapsed; one opens when Settings was asked for a topic inside it.
  const [openGroups, setOpenGroups] = useState<ReadonlySet<string>>(() => new Set(useStore.getState().settingsGroup ? [useStore.getState().settingsGroup as string] : []))
  const toggleGroup = (id: string, open: boolean): void => setOpenGroups((g) => { const n = new Set(g); if (open) n.add(id); else n.delete(id); return n })
  const gp = { openGroups, toggle: toggleGroup }
  const [q, setQ] = useState('')
  const [jump, setJump] = useState<string | null>(() => place(storeTab).anchor ?? null)
  const [extList, setExtList] = useState<readonly ExtensionInfo[] | null>(null)
  useEffect(() => {
    api.devExtensions.list().then((r) => setExtList(r.extensions)).catch(() => undefined)
  }, [])
  const hiddenViews = useStore((s) => s.settings.hiddenViews)
  const [runtimeView, setRuntimeView] = useState<RuntimeView | null>(null)
  useEffect(() => {
    api.runtime.get(null, null).then(setRuntimeView).catch(() => undefined)
  }, [])
  const offExtensions: ReadonlySet<string> = new Set([
    ...(extList ?? []).filter((e) => !e.enabled).map((e) => e.id), ...(hiddenViews ?? []),
    ...(runtimeSlotsShown(runtimeView).length ? [] : [RUNTIME_EXT])
  ])
  const hits = searchSettings(q, panels.map((p) => ({ tab: p.tab, sec: p.id, title: p.label, keys: p.keys ?? '' })), offExtensions)
  const google = useStore((s) => s.google)
  /** A search hit as the section it lives in (a registered panel's hit lands in its group) and the anchor inside it. */
  const hitAt = (h: { tab: SettingsTab; sec: string }): { section: Tab; sec: string } => ({ section: locate(h.tab, panels).section, sec: h.sec })
  /** Go to a search hit: its section, any collapsed group around it opened, then scrolled to once it has rendered. */
  const goTo = (h: { tab: SettingsTab; sec: string; open?: string }): void => {
    const at = hitAt(h)
    setTab(at.section)
    setOpenGroups((g) => new Set(g).add(h.open ?? at.sec))
    setJump(at.sec)
    setQ('')
  }
  useEffect(() => {
    if (!jump) return
    // A disclosure holding the block opens first (its rows mount on the next render); then the block scrolls into view and flashes.
    window.dispatchEvent(new CustomEvent(SETTINGS_JUMP, { detail: jump }))
    const target = jump
    let frames = 0
    const land = (): void => {
      const el = document.getElementById(`sec-${target}`)
      if (!el) { if (++frames < 20) requestAnimationFrame(land); return }
      document.querySelectorAll<HTMLDetailsElement>(`details[data-opens~="${target}"]`).forEach((d) => { d.open = true })
      for (let d = el.parentElement?.closest('details'); d; d = d.parentElement?.closest('details')) d.open = true
      el.scrollIntoView({ block: 'start', behavior: 'smooth' })
      el.classList.add('set-flash')
      window.setTimeout(() => el.classList.remove('set-flash'), 1600)
    }
    requestAnimationFrame(land)
    setJump(null)
  }, [jump, tab, openGroups])
  useEffect(() => {
    const at = place(storeTab)
    setTabState(at.section)
    if (at.anchor) setJump(at.anchor)
    if (storeGroup) { setOpenGroups((g) => new Set(g).add(storeGroup)); if (!at.anchor) setJump(storeGroup) }
  }, [storeTab, storeGroup])
  const mode = modeOf(settings)
  const tabRefs = useRef<Partial<Record<Tab, HTMLButtonElement | null>>>({})
  const paneRef = useRef<HTMLDivElement>(null)
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

  /** A provider was added, changed or removed (saved at once by its own routes): take the new settings and models, keeping any edit still unsaved. */
  const providersChanged = async (): Promise<void> => {
    const fresh = await api.settings.get()
    useStore.setState({ settings: fresh })
    void useStore.getState().loadModels()
    const old = base.current as unknown as Record<string, unknown>
    base.current = fresh
    setDraft((d) => Object.fromEntries(Object.entries(fresh).map(([k, v]) => [k, JSON.stringify((d as unknown as Record<string, unknown>)[k]) === JSON.stringify(old[k]) ? v : (d as unknown as Record<string, unknown>)[k]])) as unknown as Settings)
  }
  /** A profile was turned on or off (applied at once by its own routes): the same refresh, and off the view it may have hidden. */
  const profileApplied = async (): Promise<void> => {
    await providersChanged()
    void api.profiles.list().then(useStore.getState().applyProfiles).catch(() => undefined)  // the layout and theme tokens of whichever profiles are now on
    void useStore.getState().refreshDashboard()  // a view the profile turned on has no data loaded yet
    void useTodos.getState().refreshTodos()
    if (viewHidden(useStore.getState().settings, view)) setView('chat')
  }
  // Every way out (X, Escape, the backdrop, Cancel) comes through here, so a changed draft is never
  // dropped without the footer asking first.
  const requestClose = (): void => {
    if (dirty) setPending((p) => p ?? 'close')
    else setSettingsOpen(false)
  }
  // A link inside Settings navigates through the store; with a dirty draft the store asks through here instead of closing.
  useEffect(() => {
    setSettingsGuard(dirty ? () => setPending((p) => p ?? 'close') : null)
    return () => setSettingsGuard(null)
  }, [dirty])
  const discard = (): void => {
    setPending(null)
    setSettingsOpen(false)
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
      if (!bound) { setTab('general'); setJump('shortcuts'); return }
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

  /** One line under a section's name, from what the store and the extension list already hold. */
  const hint = (id: Tab): string | null => hintOf(id, {
    themeLabel: THEMES.find((t) => t.id === settings.theme)?.label,
    modelLabel: settings.defaultModel ? modelLabel(settings.defaultModel) : null,
    modeLabel: MODES.find((m) => m.id === mode)?.label,
    google,
    autoCompact: settings.autoCompact ?? true,
    extensionsOn: extList ? extList.filter((e) => e.enabled).length : null
  })

  // Vertical tablist: arrows move and select, Home/End jump to the ends.
  const onTabKey = (e: KeyboardEvent<HTMLDivElement>): void => {
    if ((e.target as HTMLElement).tagName === 'INPUT') return
    const tabs = SETTINGS_SECTIONS
    const i = tabs.findIndex((t) => t.id === tab)
    const next = e.key === 'ArrowDown' ? (i + 1) % tabs.length
      : e.key === 'ArrowUp' ? (i - 1 + tabs.length) % tabs.length
        : e.key === 'Home' ? 0 : e.key === 'End' ? tabs.length - 1 : -1
    if (next < 0) return
    e.preventDefault()
    setTab(tabs[next].id as Tab)
    tabRefs.current[tabs[next].id as Tab]?.focus()
  }
  // Escape answers the footer's question with "keep editing" rather than asking it again.
  const onModalKey = (e: KeyboardEvent<HTMLDivElement>): void => {
    if (e.key !== 'Escape' || !pending) return
    e.preventDefault()
    keepEditing()
  }

  const tabProps = { draft, patch }
  const meta = SETTINGS_SECTIONS.find((x) => x.id === tab) ?? SETTINGS_SECTIONS[0]
  // Panels that extensions registered into the shown section, each a titled group at its end.
  const sectionPanels = panelsFor(tab, panels)
  useEffect(() => { paneRef.current?.scrollTo?.({ top: 0 }) }, [tab])
  const content = (): JSX.Element | null => {
    switch (tab) {
      case 'general': return <><GeneralTab {...tabProps} shortcuts={shortcuts} /><SidebarTab {...tabProps} onProfileApplied={profileApplied} /></>
      case 'models': return <ModelTab {...tabProps} onProvidersChanged={providersChanged} />
      case 'assistant': return <><AgentsTab {...tabProps} /><h3 className="set-part">Memory and context</h3><ContextTab {...tabProps} {...gp} /></>
      case 'permissions': return <><PermissionsTab {...tabProps} onPresetApplied={providersChanged} /><SystemTab /></>
      case 'extensions': return null  // the Extensions panel registers into this section's group
      case 'integrations': return <IntegrationsTab {...tabProps} onSaveCreds={saveGoogleCreds} />
      case 'advanced': return <AdvancedTab {...tabProps} {...gp} onExtensionsChanged={profileApplied} />
      default: return null
    }
  }

  return (
    <div className="modal-backdrop" {...backdrop}>
      <div className="modal settings-modal" {...modal} onKeyDown={onModalKey}>
        <header><h2 id={titleId}>Settings</h2><button className="icon-btn" aria-label="Close settings" title="Close" onClick={requestClose}><X size={16} /></button></header>

        <EffectiveProvider>
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
                    <span>{h.title}</span><small>{SETTINGS_SECTIONS.find((t) => t.id === hitAt(h).section)?.label}</small>
                  </button>
                )) : <p className="muted small">No settings match.</p>}
              </div>
            ) : (
              <nav className="settings-tabs pill-group" ref={tabsPill} role="tablist" aria-orientation="vertical" aria-label="Settings sections" onKeyDown={onTabKey}>
                {SETTINGS_SECTIONS.map(({ id, label, icon: Icon }) => {
                  const sid = id as Tab
                  const line = hint(sid)
                  return (
                    <Fragment key={id}>
                      {startsCluster(sid) && <div className="settings-tab-divider" role="separator" aria-orientation="horizontal" />}
                      <button ref={(el) => { tabRefs.current[sid] = el }} role="tab" id={`settings-tab-${id}`} aria-controls="settings-pane"
                        aria-selected={tab === id} tabIndex={tab === id ? 0 : -1} className={tab === id ? 'active' : undefined} onClick={() => setTab(sid)}>
                        <Icon size={15} /><span>{label}{line && <small className="tab-hint">{line}</small>}</span>
                      </button>
                    </Fragment>
                  )
                })}
              </nav>
            )}
          </div>
          <div className="settings-pane" id="settings-pane" role="tabpanel" aria-labelledby={`settings-tab-${tab}`} ref={paneRef}>
            <section className="set-head">
              <h3>{meta.label}</h3>
            </section>
            {content()}
            {sectionPanels.length > 0 && (
              <section className="set-panels">
                {sectionPanels.map((p) => <Sec key={p.tab} id={p.id}>{p.group !== 'extensions' && <h4>{p.label}</h4>}<p.Component /></Sec>)}
              </section>
            )}
          </div>
        </div>
        </EffectiveProvider>

        {(pending || dirty) && <footer>
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
          ) : null}
        </footer>}
      </div>
    </div>
  )
}


import type { View } from '../../store'
import { navEntries } from '../../shell/nav'
import ProfilesSection from './ProfilesSection'
import Switch from './Switch'
import { Sec, type TabProps } from './shared'

const FILES: [string, string][] = [['docs', 'Files']]
const SECTIONS: [string, string][] = [['projects', 'Projects']]

export default function SidebarTab({ draft, patch, onProfileApplied }: TabProps & { onProfileApplied: () => Promise<void> }): JSX.Element {
  const hidden = draft.hiddenViews ?? []
  const sbHidden = draft.sidebarHidden ?? []
  /** A row that only leaves the sidebar (still reachable by ⌘K and shortcuts). */
  const sectionRow = ([k, label]: [string, string]): JSX.Element => (
    <Switch key={k} plain={false} title={label} label={label} checked={!sbHidden.includes(k)}
      onChange={(on) => patch({ sidebarHidden: [...sbHidden.filter((x) => x !== k), ...(on ? [] : [k])] })} />
  )
  /** A view on or off. */
  const setRowShown = (v: View | 'spaces', on: boolean): void =>
    patch({ hiddenViews: [...hidden.filter((x) => x !== v), ...(on ? [] : [v])], ...(on ? { sidebarHidden: sbHidden.filter((x) => x !== v) } : {}) })
  return (
    <section>
      <h3>Sidebar</h3>
      <p className="muted small">New chat, your chats and Settings always show. Hiding Files, Projects or Jobs only removes the row: ⌘K and shortcuts still open them. Hiding Lists, Calendar, Mail, Health, Canvases or Library turns that view off, and the assistant stops using its tools.</p>
      <Sec id="sidebar"><div className="setting-list">
        {FILES.map(sectionRow)}
        {navEntries().map((e) => (
          <Switch key={e.view} plain={false} title={e.label} label={e.label} checked={!hidden.includes(e.view) && !sbHidden.includes(e.view)} onChange={(on) => setRowShown(e.view, on)} />
        ))}
        <Switch plain={false} title="Canvases" label="Canvases" checked={!hidden.includes('spaces') && !sbHidden.includes('spaces')} onChange={(on) => setRowShown('spaces', on)} />
        {SECTIONS.map(sectionRow)}
      </div></Sec>
      <ProfilesSection onApplied={onProfileApplied} />
    </section>
  )
}

import Switch from './Switch'
import { Sec, type TabProps } from './shared'

/** Rows that have no extension switch of their own: Files and Projects only leave the sidebar; Library is a view that can be turned off. */
const ROWS: [string, string][] = [['docs', 'Files'], ['projects', 'Projects']]

export default function SidebarTab({ draft, patch }: TabProps): JSX.Element {
  const hidden = draft.hiddenViews ?? []
  const sbHidden = draft.sidebarHidden ?? []
  /** A row that only leaves the sidebar (still reachable by ⌘K and shortcuts). */
  const sectionRow = ([k, label]: [string, string]): JSX.Element => (
    <Switch key={k} plain={false} title={label} label={label} checked={!sbHidden.includes(k)}
      onChange={(on) => patch({ sidebarHidden: [...sbHidden.filter((x) => x !== k), ...(on ? [] : [k])] })} />
  )
  return (
    <section>
      <Sec id="sidebar">
      <h4>Sidebar rows</h4>
      <div className="setting-list">
        {sectionRow(ROWS[0])}
        <Switch plain={false} title="Library" label="Library" checked={!hidden.includes('library') && !sbHidden.includes('library')}
          onChange={(on) => patch({ hiddenViews: [...hidden.filter((x) => x !== 'library'), ...(on ? [] : ['library'])], ...(on ? { sidebarHidden: sbHidden.filter((x) => x !== 'library') } : {}) })} />
        {sectionRow(ROWS[1])}
      </div></Sec>
    </section>
  )
}

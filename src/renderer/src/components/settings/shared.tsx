import type { ReactNode } from 'react'
import type { Settings } from '@shared/types'
import type { AdvancedGroup } from '../../lib/settingsTabs'

/** What every tab gets: the modal's draft and the way to edit it. Nothing is written until Save. */
export type TabProps = { draft: Settings; patch: (p: Partial<Settings>) => void }

/** The collapsed groups of the Advanced and Memory tabs: which are open, and how to flip one. */
export type GroupProps = { openGroups: ReadonlySet<string>; toggle: (id: string, open: boolean) => void }

/** A block of a tab the search can jump to (`sec-<id>` in SETTINGS_INDEX). */
export function Sec({ id, children }: { id: string; children: ReactNode }): JSX.Element {
  return <div className="set-sec" id={`sec-${id}`}>{children}</div>
}

/** One collapsed group. */
export function AdvGroup({ id, title, openGroups, toggle, children }: GroupProps & { id: AdvancedGroup; title: string; children: ReactNode }): JSX.Element {
  return (
    <details className="adv-group" open={openGroups.has(id)} onToggle={(e) => toggle(id, (e.currentTarget as HTMLDetailsElement).open)}>
      <summary>{title}</summary>
      {openGroups.has(id) && children}
    </details>
  )
}

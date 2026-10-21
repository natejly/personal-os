import type { ReactNode } from 'react'

/** A file a tool produced, as one line: icon, title and an Open action. */
export default function FileLine({ icon, title, onOpen }: { icon: ReactNode; title: string; onOpen?: () => void }): JSX.Element {
  return (
    <div className="tool-event tc-card tc-fileline" role="group" aria-label={title}>
      <span className="tc-icon">{icon}</span>
      <span className="tc-fileline-title" title={title}>{title}</span>
      {onOpen && <button type="button" className="ghost-btn sm" onClick={onOpen}>Open</button>}
    </div>
  )
}

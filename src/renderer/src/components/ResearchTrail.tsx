import { useState } from 'react'
import { ChevronRight, Telescope } from 'lucide-react'
import type { TrailView } from '../lib/researchTrail'

/** The research trail above a reply: the plan with each step's outcome, then the sources the researchers considered. Closed by default. */
export default function ResearchTrail({ trail }: { trail: TrailView }): JSX.Element {
  const [open, setOpen] = useState(false)
  const [showSources, setShowSources] = useState(false)
  return (
    <div className="reasoning research-trail">
      <button className="reasoning-head" onClick={() => setOpen(!open)} aria-expanded={open}>
        <ChevronRight size={12} className={open ? 'rot90' : ''} />
        <Telescope size={13} />
        <span className="reasoning-label">Researched {trail.steps.length} question{trail.steps.length === 1 ? '' : 's'} · {trail.sources.length} source{trail.sources.length === 1 ? '' : 's'}</span>
      </button>
      {open && (
        <div className="research-body">
          <ol>
            {trail.steps.map((s, i) => <li key={i}><span>{s.q}</span> <small className={`research-${s.status}`}>{s.label}</small></li>)}
          </ol>
          {trail.dropped > 0 && <div className="muted">Dropped {trail.dropped} unsupported claim{trail.dropped === 1 ? '' : 's'}</div>}
          {trail.sources.length > 0 && (
            <>
              <button className="research-sources-head" onClick={() => setShowSources(!showSources)} aria-expanded={showSources}>
                <ChevronRight size={11} className={showSources ? 'rot90' : ''} /> Sources considered ({trail.sources.length})
              </button>
              {showSources && <ul>{trail.sources.map((s) => <li key={s.url}>{s.n ? `[${s.n}] ` : ''}<a href={s.url} target="_blank" rel="noreferrer">{s.title || s.url}</a></li>)}</ul>}
            </>
          )}
        </div>
      )}
    </div>
  )
}

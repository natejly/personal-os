import { Mic } from 'lucide-react'
import { useDocRec } from './store'
import { statusLabel } from './format'

/** A recording block in the rendered doc: its label, plus the recording's state read live by id. */
export default function RecordingChip({ id, label, onOpen }: { id: string; label: string; onOpen: (id: string) => void }): JSX.Element {
  const row = useDocRec((s) => {
    for (const rows of Object.values(s.recordings)) {
      const r = rows.find((x) => x.id === id)
      if (r) return r
    }
    return undefined
  })
  return (
    <a className={`rec-chip${row ? '' : ' unknown'}`} href="#" title={row ? 'Open this recording' : 'Recording unavailable'}
      onClick={(e) => { e.preventDefault(); if (row) onOpen(id) }}>
      <Mic size={12} /> {label}<span className="rec-chip-state"> · {row ? statusLabel(row.status) : 'recording unavailable'}</span>
    </a>
  )
}

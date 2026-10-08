import { useState } from 'react'
import { CalendarClock, X } from 'lucide-react'
import { api } from '../lib/api'
import { describeCron } from '../lib/cron'
import { LOOP_PRESETS, ONCE_PRESETS, customTiming, jobTiming } from '../lib/scheduleWhen'
import { useStore } from '../store'

const field = { background: 'var(--hover)', border: '1px solid var(--border)', borderRadius: 6, padding: '3px 6px', fontSize: 12 }

/**
 * `/schedule` and `/loop` picked from the menu, or sent without a time: one row (task, when, Create) that makes
 * a real job through POST /jobs, the same store Library → Automations lists. Once made it folds to a chip.
 */
export default function ScheduleForm({ loop, task: initial, projectId, onClose }: {
  loop: boolean
  task: string
  projectId: string | null
  onClose: () => void
}): JSX.Element {
  const presets = loop ? LOOP_PRESETS : ONCE_PRESETS
  const [task, setTask] = useState(initial)
  const [preset, setPreset] = useState(presets[0].id)
  const [hours, setHours] = useState(2)
  const [custom, setCustom] = useState('')
  const [busy, setBusy] = useState(false)
  const [made, setMade] = useState<string | null>(null)

  const openAutomations = (): void => {
    const s = useStore.getState()
    s.setLibraryTab('automations')
    s.setView('library')
    onClose()
  }

  const create = async (): Promise<void> => {
    const s = useStore.getState()
    const now = new Date()
    const timing = preset === 'custom' ? customTiming(loop, custom, now) : jobTiming(preset, now, { hours })
    if ('error' in timing) return s.toast(timing.error, 'error')
    const prompt = task.trim()
    if (!prompt) return s.toast('Say what should run.', 'error')
    setBusy(true)
    try {
      await api.jobs.create({ name: prompt.split('\n')[0].slice(0, 60), prompt, ...timing, project_id: projectId,
        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone, enabled: true })
      setMade(timing.kind === 'cron' ? describeCron(timing.cron)
        : new Date(timing.run_at * 1000).toLocaleString(undefined, { weekday: 'short', hour: 'numeric', minute: '2-digit' }))
    } catch (e) {
      s.toast(`Could not schedule: ${(e as Error).message}`, 'error')
    } finally {
      setBusy(false)
    }
  }

  if (made) {
    return (
      <div className="skill-hints" aria-label="Scheduled">
        <span className="file-chip"><CalendarClock size={12} /><span>Scheduled · {made}</span>
          <button className="link xs" onClick={openAutomations}>Open</button>
          <button className="file-chip-x" aria-label="Dismiss" onClick={onClose}><X size={11} /></button>
        </span>
      </div>
    )
  }

  return (
    <form className="skill-hints input-row" aria-label={loop ? 'Repeat a task' : 'Schedule a task'}
      onSubmit={(e) => { e.preventDefault(); void create() }}
      onKeyDown={(e) => { if (e.key === 'Escape') { e.preventDefault(); onClose() } }}>
      <CalendarClock size={13} aria-hidden style={{ flexShrink: 0, color: 'var(--text-muted)' }} />
      <input autoFocus aria-label="Task" placeholder={loop ? 'What to repeat' : 'What to do'} value={task}
        onChange={(e) => setTask(e.target.value)} style={{ ...field, flex: 1, minWidth: 0 }} />
      <select aria-label="When" value={preset} onChange={(e) => setPreset(e.target.value)} style={field}>
        {presets.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
      </select>
      {preset === 'hours' && (
        <input type="number" aria-label="Hours" min={1} max={23} value={hours} onChange={(e) => setHours(+e.target.value)} style={{ ...field, width: 48 }} />
      )}
      {preset === 'custom' && (loop
        ? <input aria-label="Cron" placeholder="0 9 * * 1-5" value={custom} onChange={(e) => setCustom(e.target.value)} style={{ ...field, width: 110 }} />
        : <input type="datetime-local" aria-label="Date and time" value={custom} onChange={(e) => setCustom(e.target.value)} style={field} />)}
      <button type="submit" className="ghost-btn xs" disabled={busy || !task.trim()}>{busy ? 'Creating…' : 'Create'}</button>
      <button type="button" className="link xs" onClick={onClose}>Cancel</button>
    </form>
  )
}

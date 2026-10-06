import { useEffect, useRef, useState } from 'react'
import { ArrowDown, ArrowUp, Check, Circle, Plus, Sparkles, Square, Trash2, Upload, X } from 'lucide-react'
import { useStore } from '../store'
import { api } from '../lib/api'
import type { TeachDraft, TeachRecording } from '@shared/types'
import { blankStep, cleanDraft, formatInputs, moveStep, parseInputs, pick } from '../lib/teachSteps'
import { DAYS, DEFAULT_SCHEDULE, presetCron, type Schedule } from '../lib/jobSchedule'

/** One recording frame, fetched with the auth header and released on unmount. */
function Frame({ id, n }: { id: string; n: number }): JSX.Element {
  const [url, setUrl] = useState<string | null>(null)
  useEffect(() => {
    let u: string | null = null
    let live = true
    void api.teach.frame(id, n).then((x) => { if (live) setUrl((u = x)); else URL.revokeObjectURL(x) }).catch(() => undefined)
    return () => { live = false; if (u) URL.revokeObjectURL(u) }
  }, [id, n])
  return url ? <img className="teach-frame" src={url} alt={`Frame ${n}`} title={`Frame ${n}`} /> : <span className="teach-frame" />
}

const EMPTY: TeachDraft = { title: '', goal: '', inputs: [], steps: [] }

/** Teach a task: record (or import) doing it once, extract the steps, edit them, save as a candidate skill, optionally schedule it. */
export default function TeachTaskPanel({ onClose }: { onClose: () => void }): JSX.Element {
  const { toast, refreshSkills, refreshJobs, updateSkill, selectChat } = useStore()
  const skills = useStore((s) => s.skills)
  const [rec, setRec] = useState<TeachRecording | null>(null)
  const [needsPerm, setNeedsPerm] = useState(false)
  const [busy, setBusy] = useState<string | null>(null)
  const [draft, setDraft] = useState<TeachDraft | null>(null)
  const [inputsText, setInputsText] = useState('')
  const [sched, setSched] = useState<Schedule>(DEFAULT_SCHEDULE)
  const [test, setTest] = useState(true)
  const [testConv, setTestConv] = useState<string | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  const load = (r: TeachRecording): void => {
    setRec(r)
    if (r.steps) { setDraft(r.steps); setInputsText(formatInputs(r.steps.inputs)) }
  }

  // Pick up where the last unsaved recording left off.
  useEffect(() => {
    void api.teach.list().then((rows) => { const r = rows.find((x) => x.status !== 'saved'); if (r) load(r) }).catch(() => undefined)
  }, [])

  // While recording, the frame count and clock come from a 1 s poll; the backend stops itself at its cap.
  useEffect(() => {
    if (rec?.status !== 'recording') return
    const t = setInterval(() => { void api.teach.get(rec.id).then(setRec).catch(() => undefined) }, 1000)
    return () => clearInterval(t)
  }, [rec?.id, rec?.status])

  const run = async (label: string, fn: () => Promise<void>): Promise<void> => {
    setBusy(label)
    try { await fn() } catch (e) { toast((e as Error).message, 'error') } finally { setBusy(null) }
  }

  const start = (): Promise<void> => run('start', async () => {
    const r = await api.teach.start()
    if ('needs_permission' in r) { setNeedsPerm(true); return }
    setNeedsPerm(false); setDraft(null); setTestConv(null); setRec(r)
  })
  const grant = (): Promise<void> => run('grant', async () => {
    const { result } = await api.activity.requestPermission('screen_recording')
    if (result.note) toast(result.note)
  })
  const importFile = (f: File): Promise<void> => run('import', async () => { setDraft(null); setTestConv(null); load(await api.teach.importVideo(f)) })
  const extract = (): Promise<void> => run('extract', async () => load(await api.teach.extract(rec!.id)))
  const discard = (): Promise<void> => run('discard', async () => { await api.teach.delete(rec!.id); setRec(null); setDraft(null); setTestConv(null) })

  const edited = (): TeachDraft => cleanDraft({ ...(draft ?? EMPTY), inputs: parseInputs(inputsText) })
  const save = (): Promise<void> => run('save', async () => {
    await api.teach.setSteps(rec!.id, edited())
    const r = await api.teach.save(rec!.id)
    load(r.recording)
    await refreshSkills()
    const notes = r.findings.map((f) => f.message)
    toast(`Saved “${r.skill.name}” to Skills as a candidate.${notes.length ? ' ' + notes.join(' ') : ''}`, notes.length ? 'error' : undefined)
  })
  const schedule = (): Promise<void> => run('schedule', async () => {
    const r = await api.teach.schedule(rec!.id, { kind: 'cron', cron: presetCron(sched), test })
    setRec(r.recording)
    await refreshJobs()
    setTestConv(r.test?.conversation_id ?? null)
    toast(`Scheduled “${r.job.name}”.${test ? (r.test?.ok ? ' A test run started.' : ' The test run did not start.') : ''}`)
  })

  const skill = rec?.skill_id ? skills.find((s) => s.id === rec.skill_id) : undefined
  const recording = rec?.status === 'recording'
  const d = draft ?? EMPTY
  const setStep = (i: number, patch: Partial<TeachDraft['steps'][number]>): void =>
    setDraft({ ...d, steps: d.steps.map((s, k) => (k === i ? { ...s, ...patch } : s)) })

  return (
    <div className="skill-body standalone teach-panel">
      <div className="row-actions">
        <strong><Sparkles size={14} /> Teach a task</strong>
        <button className="icon-btn ghost" aria-label="Close" style={{ marginLeft: 'auto' }} onClick={onClose}><X size={14} /></button>
      </div>
      <p className="muted small">
        Do the task once while Grain records the screen (one still a second, at most 10 minutes, nothing while a password field is focused),
        or import a screen recording. Frames stay on this Mac; Extract sends a dozen of them and the app/window list to your model once.
        Discard deletes the frames.
      </p>

      {needsPerm && (
        <div className="row-actions">
          <span className="small">macOS has not given Grain Screen Recording.</span>
          <button className="primary-btn sm" disabled={!!busy} onClick={() => void grant()}>Grant</button>
          <button className="ghost-btn sm" onClick={() => void api.activity.openPermissionSettings('screen_recording')}>Open System Settings</button>
          <span className="muted small">After switching it on, quit and reopen Grain.</span>
        </div>
      )}

      <div className="row-actions">
        {recording
          ? <button className="primary-btn sm danger" disabled={!!busy} onClick={() => void run('stop', async () => load(await api.teach.stop()))}><Square size={13} /> Stop</button>
          : <button className="primary-btn sm" disabled={!!busy} onClick={() => void start()}><Circle size={13} /> Record</button>}
        <button className="ghost-btn sm" disabled={!!busy || recording} onClick={() => fileRef.current?.click()}>
          <Upload size={13} /> {busy === 'import' ? 'Importing…' : 'Import video'}</button>
        <input ref={fileRef} type="file" accept="video/*" hidden onChange={(e) => { const f = e.target.files?.[0]; if (f) void importFile(f); e.target.value = '' }} />
        {rec && <span className="muted small">
          {recording ? `Recording · ${Math.floor(rec.elapsed ?? 0)} s · ` : ''}{rec.frame_count} frame{rec.frame_count === 1 ? '' : 's'}
        </span>}
        {rec && !recording && <button className="ghost-btn sm danger" disabled={!!busy} onClick={() => void discard()}><Trash2 size={13} /> Discard</button>}
      </div>

      {rec && !recording && rec.frame_count > 0 && (
        <>
          <div className="teach-strip">{pick(rec.frame_count, 12).map((i) => <Frame key={i} id={rec.id} n={i + 1} />)}</div>
          <div className="row-actions">
            <button className="primary-btn sm" disabled={!!busy} onClick={() => void extract()}>
              <Sparkles size={13} /> {busy === 'extract' ? 'Reading the frames…' : draft ? 'Extract again' : 'Extract steps'}</button>
            {!draft && <button className="ghost-btn sm" onClick={() => setDraft({ ...EMPTY, steps: [blankStep(1)] })}>Write the steps myself</button>}
          </div>
        </>
      )}

      {rec && draft && (
        <>
          <label>Name<input value={d.title} placeholder="File a receipt" onChange={(e) => setDraft({ ...d, title: e.target.value })} /></label>
          <label>When it applies<input value={d.goal} placeholder="when a receipt arrives by mail" onChange={(e) => setDraft({ ...d, goal: e.target.value })} /></label>
          <label>Inputs, one per line as <code>name: example</code> (examples are not saved into the steps)
            <textarea rows={3} value={inputsText} onChange={(e) => setInputsText(e.target.value)} />
          </label>
          <div className="teach-steps">
            {d.steps.map((s, i) => (
              <div key={i} className="teach-step">
                <span className="muted">{i + 1}.</span>
                {s.frame && <Frame id={rec.id} n={s.frame} />}
                <input value={s.action} placeholder="What you did" aria-label={`Step ${i + 1} action`} onChange={(e) => setStep(i, { action: e.target.value })} />
                <input value={s.app} placeholder="App" aria-label={`Step ${i + 1} app`} onChange={(e) => setStep(i, { app: e.target.value })} />
                <input value={s.detail} placeholder="Detail" aria-label={`Step ${i + 1} detail`} onChange={(e) => setStep(i, { detail: e.target.value })} />
                <button className="icon-btn ghost" aria-label="Move up" onClick={() => setDraft({ ...d, steps: moveStep(d.steps, i, -1) })}><ArrowUp size={12} /></button>
                <button className="icon-btn ghost" aria-label="Move down" onClick={() => setDraft({ ...d, steps: moveStep(d.steps, i, 1) })}><ArrowDown size={12} /></button>
                <button className="icon-btn ghost" aria-label="Remove step" onClick={() => setDraft({ ...d, steps: d.steps.filter((_, k) => k !== i) })}><X size={12} /></button>
              </div>
            ))}
            <button className="ghost-btn sm" style={{ alignSelf: 'flex-start' }} onClick={() => setDraft({ ...d, steps: [...d.steps, blankStep(d.steps.length + 1)] })}><Plus size={13} /> Add step</button>
          </div>
          <div className="row-actions">
            <button className="primary-btn sm" disabled={!!busy || !edited().steps.length} onClick={() => void save()}>
              {rec.skill_id ? 'Save as a new skill' : 'Save to Skills'}</button>
            <span className="muted small">It lands as a candidate and stays off until you approve it.</span>
          </div>
        </>
      )}

      {skill && (
        <div className="row-actions">
          <span className="small">Skill “{skill.name}” is {skill.status === 'approved' ? 'approved' : 'waiting for approval'}.</span>
          {skill.status !== 'approved' && (
            <button className="ghost-btn sm" onClick={() => void updateSkill(skill.id, { status: 'approved' })}><Check size={13} /> Approve</button>
          )}
        </div>
      )}

      {skill?.status === 'approved' && (
        <div className="row-actions">
          <span className="small">Run it as a routine</span>
          <select value={sched.preset} onChange={(e) => setSched({ ...sched, preset: e.target.value as Schedule['preset'] })}>
            <option value="hourly">Every hour</option>
            <option value="daily">Every day</option>
            <option value="weekdays">Weekdays</option>
            <option value="weekly">Every week</option>
          </select>
          {sched.preset === 'weekly' && (
            <select value={sched.day} onChange={(e) => setSched({ ...sched, day: Number(e.target.value) })}>
              {DAYS.map((name, i) => <option key={name} value={i}>{name}</option>)}
            </select>
          )}
          {sched.preset !== 'hourly' && <input type="time" value={sched.time} onChange={(e) => setSched({ ...sched, time: e.target.value })} />}
          <label className="small"><input type="checkbox" checked={test} onChange={(e) => setTest(e.target.checked)} /> Run a test (read-only)</label>
          <button className="primary-btn sm" disabled={!!busy || !presetCron(sched)} onClick={() => void schedule()}>
            {busy === 'schedule' ? 'Scheduling…' : 'Schedule'}</button>
          {rec?.job_id && <span className="muted small">Scheduled; it is listed with your jobs on Home.</span>}
          {testConv && <button className="link small" onClick={() => void selectChat(testConv)}>Open the test run</button>}
        </div>
      )}
    </div>
  )
}

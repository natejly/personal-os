import { useCallback, useEffect, useReducer, useRef, useState, type KeyboardEvent } from 'react'
import { ArrowLeft, ArrowRight, Check, ExternalLink, Eye, EyeOff, Loader2, Lock, RefreshCw } from 'lucide-react'
import GrainLogo from '../GrainLogo'
import { api } from '../../lib/api'
import { useStore } from '../../store'
import { useOnboarding } from './onboardingStore'
import { ABOUT_EXAMPLES, STEPS, initialState, modelOptions, reduce, showsBaseUrl, stepBlocker, type ProviderInfo, type WizardAction, type WizardState } from './steps'
import './onboarding.css'

/** Opens in the real browser: the main process turns window.open into shell.openExternal. */
const openExternal = (url: string): void => void window.open(url, '_blank')

const TITLES: Record<WizardState['step'], string> = {
  welcome: 'Welcome to Grain',
  provider: 'Choose your AI provider',
  key: 'Connect your account',
  test: 'Testing the connection',
  google: 'Connect Google',
  about: 'Tell Grain about you',
  done: 'You are all set'
}

/** Full-window first-run flow. Rendered by App while the onboarding store says so. */
export default function Onboarding(): JSX.Element {
  const [providers, setProviders] = useState<ProviderInfo[] | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [showKey, setShowKey] = useState(false)
  const [saved, setSaved] = useState<{ state: 'idle' | 'saving' | 'ok' | 'fail'; error?: string }>({ state: 'idle' })
  const root = useRef<HTMLDivElement>(null)
  const closeWizard = useOnboarding((s) => s.closeWizard)
  const google = useStore((s) => s.google)
  const { refreshGoogle, connectGoogle } = useStore()

  const [state, rawDispatch] = useReducer(
    (s: WizardState, a: WizardAction & { provider_?: ProviderInfo }) => reduce(s, a, a.provider_),
    undefined,
    initialState
  )
  const provider = providers?.find((p) => p.id === state.providerId)
  // The reducer needs the provider for the key-step check; it is looked up here rather than stored twice.
  const dispatch = useCallback((a: WizardAction) => rawDispatch({ ...a, provider_: provider }), [provider])

  useEffect(() => {
    api.setup.providers().then(async (r) => {
      setProviders(r.providers)
      // On "Run setup again", start from what is configured now. A fresh install has no provider yet.
      const st = await api.setup.status().catch(() => null)
      const p = r.providers.find((x) => x.id === st?.provider)
      if (st && p) rawDispatch({ type: 'seed', provider: p, baseUrl: st.baseUrl || p.baseUrl, model: st.model || p.defaultModel })
    }).catch((e: Error) => setLoadError(e.message))
  }, [])

  // Focus follows the step: the first input if there is one, else the heading.
  useEffect(() => {
    const el = root.current?.querySelector<HTMLElement>('[data-autofocus]') ?? root.current?.querySelector<HTMLElement>('h1')
    el?.focus()
  }, [state.step, providers])

  // The test runs as the step opens. A counter drops the verdict of a run the user has already left.
  const testRun = useRef(0)
  const runTest = useCallback(async (): Promise<void> => {
    if (!provider) return
    const run = ++testRun.current
    dispatch({ type: 'test', test: { state: 'testing' } })
    try {
      const r = await api.setup.test({ provider: provider.id, baseUrl: state.baseUrl.trim(), apiKey: state.apiKey.trim() || null, model: state.model.trim() })
      if (run !== testRun.current) return
      dispatch({ type: 'test', test: r.ok ? { state: 'ok', latencyMs: r.latencyMs ?? undefined, models: r.models ?? undefined } : { state: 'fail', error: r.error ?? 'The connection failed.', models: r.models ?? undefined } })
    } catch (e) {
      if (run === testRun.current) dispatch({ type: 'test', test: { state: 'fail', error: `Could not run the test: ${(e as Error).message}` } })
    }
  }, [provider, state.baseUrl, state.apiKey, state.model, dispatch])
  useEffect(() => {
    if (state.step === 'test') void runTest()
    else testRun.current++
    // Only the step change starts a test; edits happen on the key step, which cannot be on screen here.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.step])

  useEffect(() => { if (state.step === 'google') void refreshGoogle() }, [state.step, refreshGoogle])

  const save = useCallback(async (): Promise<void> => {
    if (!provider) return
    setSaved({ state: 'saving' })
    try {
      await api.setup.complete({ provider: provider.id, baseUrl: state.baseUrl.trim(), apiKey: state.apiKey.trim() || null, model: state.model.trim() })
      // The backend persisted through the normal settings path; pull it into the store rather than re-PUT it.
      useStore.setState({ settings: await api.settings.get() })
      void useStore.getState().loadModels()
      // A pinned memory, so the assistant knows who it is talking to from the very first message.
      // A failure here is not worth blocking setup on: the user can add it in Memory later.
      const about = state.about.trim()
      if (about) await api.memories.create({ project_id: null, content: about, kind: 'fact', pinned: true }).catch(() => undefined)
      setSaved({ state: 'ok' })
    } catch (e) {
      setSaved({ state: 'fail', error: (e as Error).message })
    }
  }, [provider, state.baseUrl, state.apiKey, state.model, state.about])
  // Only arriving at `done` saves; `save` itself changes as the user types on the about step.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { if (state.step === 'done') void save() }, [state.step])

  const finish = (): void => {
    useStore.getState().newChat()
    useOnboarding.getState().setFirstPrompts(true)
    closeWizard()
  }

  const blocker = stepBlocker(state, provider)
  const canNext = !blocker && state.step !== 'done'
  // Leaving the Google step unconnected, or the about step empty, is a skip, and the one forward button
  // says so: it steps back to a quiet style so the step's own action stays the main one.
  const skipping = (state.step === 'google' && !google?.connected) || (state.step === 'about' && !state.about.trim())
  const advance = (): void => {
    if (state.step === 'done') { if (saved.state === 'ok') finish(); return }
    if (state.step === 'test' && state.test.state === 'fail') return void runTest()
    if (canNext) dispatch({ type: 'next' })
  }

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>): void => {
    if (e.nativeEvent.isComposing) return
    if (e.key === 'Escape') {
      e.preventDefault()
      if (state.step !== 'welcome' && state.step !== 'done') dispatch({ type: 'back' })
    } else if (e.key === 'Enter' && !e.shiftKey) {
      // A focused button handles its own Enter (it is its click); everything else means "continue".
      const t = e.target as HTMLElement
      if (t.tagName === 'BUTTON' || t.tagName === 'A' || t.tagName === 'SUMMARY' || t.tagName === 'TEXTAREA') return
      e.preventDefault()
      advance()
    }
  }

  const stepIndex = STEPS.indexOf(state.step)
  const field = (patch: Partial<Pick<WizardState, 'baseUrl' | 'apiKey' | 'model'>>): void => dispatch({ type: 'field', patch })

  return (
    <div className="onboarding" ref={root} role="dialog" aria-modal="true" aria-labelledby="ob-title" onKeyDown={onKeyDown}>
      <div className="ob-drag drag" />
      <div className="ob-card">
        <div className="ob-progress" role="progressbar" aria-label="Setup progress" aria-valuemin={1} aria-valuemax={STEPS.length} aria-valuenow={stepIndex + 1}>
          {STEPS.map((s, i) => <span key={s} className={i <= stepIndex ? 'on' : ''} />)}
        </div>
        {state.step === 'welcome' && <GrainLogo size={44} />}
        <h1 id="ob-title" tabIndex={-1}>{TITLES[state.step]}</h1>

        {state.step === 'welcome' && (
          <>
            <p className="ob-lead">Grain is your personal AI workspace: chat, files, calendar, mail and tasks in one place, with an assistant that can work across all of it. Spaces lay out chats, files, the web and your apps side by side, and any window can pop out on top of other apps.</p>
            <p className="ob-privacy"><Lock size={13} /> Your data is stored on this Mac. Your messages, the context Grain adds to them (memories, files, mail and calendar the assistant reads) and background learning go to the AI provider you choose. Web search and page reading use outside services.</p>
            <p className="muted">Setup takes about two minutes. You will need an API key from an AI provider (or a local model).</p>
          </>
        )}

        {state.step === 'provider' && (
          <>
            <p className="muted">Pick where the AI models come from. You can change this later in Settings.</p>
            {loadError && <p className="ob-error" role="alert">Could not load providers: {loadError}</p>}
            {!providers && !loadError && <p className="muted"><Loader2 size={13} className="spin" /> Loading…</p>}
            <div className="ob-cards" role="radiogroup" aria-label="AI provider">
              {providers?.map((p) => {
                const on = p.id === state.providerId
                return (
                  <button
                    key={p.id} type="button" role="radio" aria-checked={on} className={`ob-card-opt${on ? ' on' : ''}`}
                    data-autofocus={on ? '' : undefined}
                    onClick={() => dispatch({ type: 'pick', provider: p })}
                    onKeyDown={(e) => { if (e.key === 'Enter' && !e.nativeEvent.isComposing) { e.preventDefault(); e.stopPropagation(); dispatch({ type: 'pick', provider: p }); rawDispatch({ type: 'next', provider_: p }) } }}
                  >
                    <b>{p.name}</b>
                    <small>{p.note ?? (p.needsKey ? 'Needs an API key' : 'Runs without a key')}</small>
                  </button>
                )
              })}
            </div>
          </>
        )}

        {state.step === 'key' && provider && (
          <>
            {provider.keyUrl && (
              <p className="muted">
                Need a key? <button type="button" className="link" onClick={() => openExternal(provider.keyUrl as string)}>Get one from {provider.name} <ExternalLink size={11} /></button>
              </p>
            )}
            {provider.note && <p className="muted">{provider.note}</p>}
            {showsBaseUrl(provider.id) && (
              <label><span>Base URL</span>
                <input data-autofocus value={state.baseUrl} onChange={(e) => field({ baseUrl: e.target.value })} placeholder={provider.baseUrl} spellCheck={false} />
              </label>
            )}
            <label><span>{provider.needsKey ? 'API key' : 'API key (optional)'}</span>
              <div className="input-row">
                <input data-autofocus={showsBaseUrl(provider.id) ? undefined : ''} type={showKey ? 'text' : 'password'} value={state.apiKey} onChange={(e) => field({ apiKey: e.target.value })} placeholder={provider.needsKey ? 'Paste your key' : 'Only if your server needs one'} spellCheck={false} autoComplete="off" />
                <button className="icon-btn" type="button" aria-label={showKey ? 'Hide API key' : 'Show API key'} aria-pressed={showKey} onClick={() => setShowKey((v) => !v)}>{showKey ? <EyeOff size={14} /> : <Eye size={14} />}</button>
              </div>
            </label>
            <label><span>Model</span>
              <input list="ob-models" value={state.model} onChange={(e) => field({ model: e.target.value })} placeholder={provider.defaultModel} spellCheck={false} />
              <datalist id="ob-models">{modelOptions(provider, state.test.models).map((m) => <option key={m} value={m} />)}</datalist>
            </label>
            <p className="muted small"><Lock size={11} /> Your key is stored on this Mac only.</p>
          </>
        )}

        {state.step === 'test' && (
          <div className="ob-test" role="status" aria-live="polite">
            {state.test.state === 'testing' && <p><Loader2 size={15} className="spin" /> Contacting {provider?.name}…</p>}
            {state.test.state === 'ok' && <p className="ob-ok"><Check size={15} /> Connected{state.test.latencyMs != null ? ` in ${state.test.latencyMs} ms` : ''}. {state.model} is ready.</p>}
            {state.test.state === 'fail' && (
              <>
                <p className="ob-error" role="alert">{state.test.error}</p>
                <p className="muted">Go back to fix the key, model or address, or try again.</p>
              </>
            )}
          </div>
        )}

        {state.step === 'google' && (
          <>
            <p className="muted">Optional. Connecting Google lets the assistant read your Calendar, Gmail, Tasks and Drive. You can skip this and do it later in Settings.</p>
            <p className="muted">Connecting also turns on two-way sync between Todos and Google Tasks, and creates a &ldquo;Grain Todos&rdquo; calendar that shows todos with a due date. Both can be switched off in Settings → Integrations.</p>
            {google?.connected ? (
              <p className="ob-ok"><Check size={15} /> Signed in as {google.email}</p>
            ) : google?.configured ? (
              <button type="button" className="primary-btn" data-autofocus onClick={() => void connectGoogle()}>Sign in with Google</button>
            ) : (
              <p className="muted">Google sign-in needs a Google Cloud OAuth client today, which takes about two minutes to create. Settings → Integrations walks you through it whenever you are ready.</p>
            )}
          </>
        )}

        {state.step === 'about' && (
          <>
            <p className="muted">Optional. Anything here is saved as a memory the assistant reads in every chat — your role, how you like replies, what it should keep in mind.</p>
            <label><span>About you</span>
              <textarea data-autofocus rows={4} value={state.about} onChange={(e) => dispatch({ type: 'about', about: e.target.value })} placeholder="I am a…" />
            </label>
            <div className="ob-examples">
              {ABOUT_EXAMPLES.map((t) => (
                <button key={t} type="button" className="ghost-btn" onClick={() => dispatch({ type: 'about', about: t })}>{t}</button>
              ))}
            </div>
            <p className="muted small"><Lock size={11} /> Stored on this Mac. Edit or delete it any time in Memory.</p>
          </>
        )}

        {state.step === 'done' && (
          <div role="status" aria-live="polite">
            {saved.state === 'saving' && <p className="muted"><Loader2 size={13} className="spin" /> Saving…</p>}
            {saved.state === 'ok' && <p className="ob-lead">Grain is connected to {provider?.name}. Start a chat and ask it anything.</p>}
            {saved.state === 'fail' && (
              <>
                <p className="ob-error" role="alert">Could not save your setup: {saved.error}</p>
                <button type="button" className="ghost-btn" onClick={() => void save()}><RefreshCw size={13} /> Try again</button>
              </>
            )}
          </div>
        )}

        {blocker && state.step !== 'test' && state.step !== 'welcome' && <p className="ob-hint">{blocker}</p>}

        <div className="ob-actions">
          {(state.step !== 'done' || saved.state === 'fail') && <button type="button" className="ghost-btn" onClick={closeWizard}>Set up later</button>}
          {state.step !== 'welcome' && state.step !== 'done' && (
            <button type="button" className="ghost-btn" onClick={() => dispatch({ type: 'back' })}><ArrowLeft size={13} /> Back</button>
          )}
          <span className="ob-spacer" />
          {state.step === 'test' && state.test.state === 'fail' && <button type="button" className="primary-btn" data-autofocus onClick={() => void runTest()}><RefreshCw size={13} /> Try again</button>}
          {state.step === 'done' ? (
            <button type="button" className="primary-btn" data-autofocus disabled={saved.state !== 'ok'} onClick={finish}>Start chatting <ArrowRight size={13} /></button>
          ) : !(state.step === 'test' && state.test.state === 'fail') && (
            <button type="button" className={skipping ? 'ghost-btn' : 'primary-btn'} disabled={!canNext} onClick={advance}>
              {state.step === 'welcome' ? 'Get started' : skipping ? 'Skip for now' : 'Continue'} <ArrowRight size={13} />
            </button>
          )}
        </div>
        <p className="ob-keys muted small">Enter to continue{state.step !== 'welcome' && state.step !== 'done' ? ', Esc to go back' : ''}</p>
      </div>
    </div>
  )
}

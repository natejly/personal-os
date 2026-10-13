import { useCallback, useEffect, useReducer, useRef, useState, type KeyboardEvent } from 'react'
import { ArrowLeft, ArrowRight, Loader2, Lock } from '../../icons'
import GrainLogo from '../GrainLogo'
import { api } from '../../lib/api'
import { useStore } from '../../store'
import { useOnboarding } from './onboardingStore'
import { STEPS, initialState, modelOptions, reduce, stepBlocker, type ProviderInfo, type WizardAction, type WizardState } from './steps'
import ProviderForm from '../ProviderForm'
import './onboarding.css'

const TITLES: Record<WizardState['step'], string> = {
  welcome: 'Welcome to Grain',
  provider: 'Choose your AI provider',
  key: 'Connect your account',
  done: 'You are all set'
}

/** Full-window first-run flow. Rendered by App while the onboarding store says so. */
export default function Onboarding(): JSX.Element {
  const [providers, setProviders] = useState<ProviderInfo[] | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const root = useRef<HTMLDivElement>(null)
  const closeWizard = useOnboarding((s) => s.closeWizard)
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

  // Continue on the key step: test the connection, and only when it works save it and move on.
  // A counter drops the verdict of a run the user has already left.
  const run = useRef(0)
  const submit = useCallback(async (): Promise<void> => {
    if (!provider) return
    const me = ++run.current
    const body = { provider: provider.id, baseUrl: state.baseUrl.trim(), apiKey: state.apiKey.trim() || null, model: state.model.trim() }
    dispatch({ type: 'test', test: { state: 'testing' } })
    try {
      const r = await api.setup.test(body)
      if (me !== run.current) return
      if (!r.ok) return dispatch({ type: 'test', test: { state: 'fail', error: r.error ?? 'The connection failed.', models: r.models ?? undefined } })
      await api.setup.complete(body)
      // The backend persisted through the normal settings path; pull it into the store rather than re-PUT it.
      useStore.setState({ settings: await api.settings.get() })
      void useStore.getState().loadModels()
      dispatch({ type: 'test', test: { state: 'idle', models: r.models ?? undefined } })
      dispatch({ type: 'next' })
    } catch (e) {
      if (me === run.current) dispatch({ type: 'test', test: { state: 'fail', error: `Could not connect: ${(e as Error).message}` } })
    }
  }, [provider, state.baseUrl, state.apiKey, state.model, dispatch])

  const finish = (): void => {
    useStore.getState().newChat()
    useOnboarding.getState().setFirstPrompts(true)
    closeWizard()
  }

  const blocker = stepBlocker(state, provider)
  const testing = state.test.state === 'testing'

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>): void => {
    if (e.nativeEvent.isComposing) return
    if (e.key === 'Escape') {
      e.preventDefault()
      if (state.step !== 'welcome' && state.step !== 'done' && !testing) dispatch({ type: 'back' })
    } else if (e.key === 'Enter' && !e.shiftKey && state.step === 'key') {
      // Only the key step's Continue answers to Enter; a focused button handles its own.
      const t = e.target as HTMLElement
      if (t.tagName === 'BUTTON' || t.tagName === 'A') return
      e.preventDefault()
      if (!blocker && !testing) void submit()
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
            <p className="ob-lead">Chat, files, calendar, mail and tasks in one place, with an assistant that works across all of it.</p>
            <p className="ob-privacy"><Lock size={13} /> Your data is stored on this Mac. Your messages, the context Grain adds to them (memories, files, mail and calendar the assistant reads) and background learning go to the AI provider you choose. Web search and page reading use outside services.</p>
            <p className="muted">Most providers need an API key. Local models do not.</p>
          </>
        )}

        {state.step === 'provider' && (
          <>
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
            <ProviderForm
              providers={providers ?? []} provider={provider} providerId={provider.id}
              baseUrl={state.baseUrl} onBaseUrl={(v) => field({ baseUrl: v })}
              apiKey={state.apiKey} onApiKey={(v) => field({ apiKey: v })}
              model={state.model} onModel={(v) => field({ model: v })} modelListId="ob-models"
            />
            <datalist id="ob-models">{modelOptions(provider, state.test.models).map((m) => <option key={m} value={m} />)}</datalist>
            {state.test.state === 'fail' && <p className="ob-error" role="alert">{state.test.error}</p>}
            <p className="muted small"><Lock size={11} /> Your key is stored on this Mac only.</p>
          </>
        )}

        {state.step === 'done' && (
          <p className="ob-lead" role="status">Grain is connected to {provider?.name}. Start a chat and ask it anything.</p>
        )}

        {blocker && state.step === 'key' && <p className="ob-hint">{blocker}</p>}

        <div className="ob-actions">
          {state.step !== 'done' && <button type="button" className="ghost-btn" onClick={closeWizard}>Set up later</button>}
          {state.step !== 'welcome' && state.step !== 'done' && (
            <button type="button" className="ghost-btn" disabled={testing} onClick={() => dispatch({ type: 'back' })}><ArrowLeft size={13} /> Back</button>
          )}
          <span className="ob-spacer" />
          {state.step === 'done' ? (
            <button type="button" className="primary-btn" data-autofocus onClick={finish}>Start chatting <ArrowRight size={13} /></button>
          ) : (
            <button type="button" className="primary-btn" disabled={state.step === 'welcome' ? false : !!blocker || testing} onClick={() => state.step === 'key' ? void submit() : dispatch({ type: 'next' })}>
              {state.step === 'welcome' ? 'Get started' : testing ? 'Connecting…' : 'Continue'} <ArrowRight size={13} />
            </button>
          )}
        </div>
        {state.step === 'key' && <p className="ob-keys muted small">Enter to continue, Esc to go back</p>}
      </div>
    </div>
  )
}

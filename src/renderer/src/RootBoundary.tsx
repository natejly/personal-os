import { Component, type ErrorInfo, type ReactNode } from 'react'

interface State {
  error: Error | null
  stack: string
}

/**
 * The last line of defence. A render error anywhere outside a widget body -- in the canvas, the window
 * frame, the sidebar, a store selector -- unmounts the whole tree, and because the BrowserWindow is
 * transparent an unmounted tree is a see-through window. That reads as "the app vanished" and leaves no
 * React error on screen, no macOS crash report and nothing in the main log.
 *
 * This paints an opaque surface no matter what, so a failure always looks like a failure.
 */
export default class RootBoundary extends Component<{ children: ReactNode }, State> {
  state: State = { error: null, stack: '' }

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    this.setState({ stack: (info.componentStack ?? '').split('\n').slice(0, 12).join('\n') })
    console.error('[root] render failed:', error, info.componentStack)
  }

  render(): ReactNode {
    const { error, stack } = this.state
    if (!error) return this.props.children
    return (
      <div className="root-error">
        <h2>Something broke while rendering</h2>
        <p className="muted">The rest of the app was torn down to avoid showing a half-rendered state.</p>
        <pre>{error.message || String(error)}</pre>
        {stack && <pre className="root-error-stack">{stack}</pre>}
        <div className="root-error-actions">
          <button className="primary-btn" onClick={() => this.setState({ error: null, stack: '' })}>Try again</button>
          <button className="ghost-btn" onClick={() => window.location.reload()}>Reload</button>
        </div>
      </div>
    )
  }
}

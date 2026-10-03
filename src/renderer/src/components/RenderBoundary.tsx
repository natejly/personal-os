import { Component, type ErrorInfo, type ReactNode } from 'react'

/** True when the key a fallback was shown for has changed, so the real content deserves another try. */
export function shouldReset(prev: unknown, next: unknown): boolean {
  return !Object.is(prev, next)
}

/**
 * One preformatted line for the main-process log: the label, the error message and the top of the component
 * stack. It carries no props, message text or tool arguments, only where the render failed.
 */
export function logLine(label: string, error: Error, componentStack?: string | null): string {
  const stack = (componentStack ?? '').split('\n').filter((l) => l.trim()).slice(0, 6)
  return [`[chat:${label}] ${error.message}`, ...stack].join('\n')
}

interface Props {
  label: string
  /** When this changes (by identity) a boundary showing its fallback tries the children again. */
  resetKey: unknown
  fallback: (error: Error, retry: () => void) => ReactNode
  children: ReactNode
}

/**
 * Contains a render error to the subtree it wraps. The error is logged once per distinct message per
 * instance, so a block that throws on every streamed token does not flood the log.
 */
export default class RenderBoundary extends Component<Props, { error: Error | null }> {
  state: { error: Error | null } = { error: null }
  private logged = new Set<string>()

  static getDerivedStateFromError(error: Error): { error: Error } {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    if (this.logged.has(error.message)) return
    this.logged.add(error.message)
    console.error(logLine(this.props.label, error, info.componentStack))
  }

  componentDidUpdate(prev: Props): void {
    if (this.state.error && shouldReset(prev.resetKey, this.props.resetKey)) this.setState({ error: null })
  }

  private retry = (): void => this.setState({ error: null })

  render(): ReactNode {
    return this.state.error ? this.props.fallback(this.state.error, this.retry) : this.props.children
  }
}

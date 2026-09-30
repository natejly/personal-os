import { Component, type ErrorInfo, type ReactNode } from 'react'
import { AlertTriangle, RotateCw } from 'lucide-react'

interface Props {
  /** Shown in the fallback so a failing widget is identifiable without opening devtools. */
  label: string
  children: ReactNode
}
interface State {
  error: Error | null
  info: string
}

/**
 * Without a boundary a single widget throwing during render unmounts the WHOLE React tree. The
 * BrowserWindow is transparent, so an unmounted tree renders as a see-through window -- which reads as
 * "the app went transparent" or "the app crashed" rather than as a widget bug. One boundary per widget
 * keeps the blast radius to that widget and puts the error on screen where it can be reported.
 */
export default class WidgetBoundary extends Component<Props, State> {
  state: State = { error: null, info: '' }

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    this.setState({ info: (info.componentStack ?? '').split('\n').slice(0, 6).join('\n') })
    console.error(`[widget:${this.props.label}]`, error, info.componentStack)
  }

  render(): ReactNode {
    const { error, info } = this.state
    if (!error) return this.props.children
    return (
      <div className="widget widget-error" role="alert">
        <div className="widget-error-head">
          <AlertTriangle size={14} />
          <b>{this.props.label} failed</b>
          <button className="ghost-btn" onClick={() => this.setState({ error: null, info: '' })}>
            <RotateCw size={12} /> Retry
          </button>
        </div>
        <pre className="widget-error-body">{error.message || String(error)}</pre>
        {info && <pre className="widget-error-stack">{info}</pre>}
      </div>
    )
  }
}

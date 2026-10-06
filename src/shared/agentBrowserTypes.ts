/** Renderer-facing shape of the agent's interactive browser (`window.os.agentBrowser`). The bridge secret never crosses this. */
export interface AgentBrowserSessionInfo {
  session: string
  url: string
  title: string
  tabs: number
  /** Seconds since the agent last used the session. */
  idleSeconds: number
  /** Whether the session's window is shown to the user (take-over). */
  visible: boolean
}

export interface AgentBrowserFrame {
  /** JPEG data: URL of the active tab. */
  dataUrl: string
  url: string
  title: string
  at: number
}

export interface AgentBrowserApi {
  list(): Promise<AgentBrowserSessionInfo[]>
  show(session: string): Promise<void>
  hide(session: string): Promise<void>
  /** Frames arrive after each agent action and about every 1.5 s while subscribed; nothing is captured with no subscriber. */
  subscribe(session: string, cb: (frame: AgentBrowserFrame) => void): () => void
}

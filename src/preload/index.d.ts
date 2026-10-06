import type { GrainApi } from '../shared/types'
import type { AgentBrowserApi } from '../shared/agentBrowserTypes'

export type { AgentBrowserApi, AgentBrowserFrame, AgentBrowserSessionInfo } from '../shared/agentBrowserTypes'

declare global {
  interface Window {
    os: GrainApi & { agentBrowser: AgentBrowserApi }
  }
}
export {}

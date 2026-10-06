import type { GrainApi } from '../shared/types'

declare global {
  interface Window {
    os: GrainApi
  }
}
export {}

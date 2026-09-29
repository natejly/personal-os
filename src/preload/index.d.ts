import type { PersonalOSApi } from '../shared/types'

declare global {
  interface Window {
    os: PersonalOSApi
  }
}
export {}

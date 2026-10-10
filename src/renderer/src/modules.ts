import type { View } from './store'
import { navEntries } from './shell/nav'

/**
 * Views that may be hidden or moved between the sidebar and the title bar (shell/nav.tsx). Chats
 * and Files are the shell itself and stay.
 */
export const OPTIONAL_VIEWS: { view: View; label: string }[] = navEntries().map(({ view, label }) => ({ view, label }))

/** One chord, two jobs: in a chat view ⌘I toggles the side panel on Context; anywhere else it is Quick chat. */
export function cmdIAction(view: string): 'panel-context' | 'page-agent' {
  return view === 'chat' ? 'panel-context' : 'page-agent'
}

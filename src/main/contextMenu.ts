import type { ContextMenuParams, MenuItemConstructorOptions } from 'electron'

type Params = Pick<ContextMenuParams, 'misspelledWord' | 'dictionarySuggestions' | 'selectionText' | 'isEditable'>

export interface ContextMenuActions {
  replaceMisspelling: (suggestion: string) => void
  addToDictionary: (word: string) => void
  /** Selection verbs (Explain, Summarize, ...); left out where there is no assistant to ask. */
  verbs?: { label: string; click: () => void }[]
}

/**
 * The right-click menu: spelling fixes first when the word under the pointer is misspelled, then the edit
 * items, then the selection verbs. Empty when there is nothing to offer (plain text with no selection).
 */
export function contextMenuTemplate(p: Params, a: ContextMenuActions): MenuItemConstructorOptions[] {
  const selected = !!p.selectionText.trim()
  const out: MenuItemConstructorOptions[] = []
  if (p.isEditable && p.misspelledWord) {
    if (p.dictionarySuggestions.length) {
      for (const s of p.dictionarySuggestions) out.push({ label: s, click: () => a.replaceMisspelling(s) })
    } else out.push({ label: 'No suggestions', enabled: false })
    out.push({ label: 'Add to Dictionary', click: () => a.addToDictionary(p.misspelledWord) }, { type: 'separator' })
  }
  if (p.isEditable) out.push({ role: 'cut' }, { role: 'copy' }, { role: 'paste' }, { role: 'selectAll' })
  else if (selected) out.push({ role: 'copy' })
  if (selected && a.verbs?.length) out.push({ type: 'separator' }, ...a.verbs)
  return out
}

/**
 * Menu items an extension adds to the app menu (design doc sections 4 and 6.1). The renderer sends the list to the main
 * process, which places each item at the end of the top-level menu named by its group; a click comes back as a `menu` action.
 */
export const MENU_GROUPS = ['File', 'Edit', 'View', 'Canvases', 'Window', 'Help'] as const
export type MenuGroup = (typeof MENU_GROUPS)[number]

/** `id` is `<extension id>:<item id>`, which is also what the clicked action carries. */
export interface ExtensionMenuItem {
  id: string
  label: string
  group: MenuGroup
}

export const extensionMenuAction = (id: string): string => `ext-menu:${id}`

/** What the main process keeps of a list sent over IPC: well-formed items only. */
export function acceptMenuItems(raw: unknown): ExtensionMenuItem[] {
  if (!Array.isArray(raw)) return []
  return raw.filter((i): i is ExtensionMenuItem =>
    !!i && typeof i.id === 'string' && !!i.id && typeof i.label === 'string' && !!i.label && MENU_GROUPS.includes(i.group))
}

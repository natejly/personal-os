/** File types the side panel may hand to the system's default app: documents and media that cannot run. */
const OPENABLE = new Set(['pdf', 'png', 'jpg', 'jpeg', 'gif', 'webp', 'txt', 'md', 'markdown', 'csv', 'json', 'log'])

export const isOpenable = (name: string): boolean => OPENABLE.has((name.split('.').pop() || '').toLowerCase()) && name.includes('.')

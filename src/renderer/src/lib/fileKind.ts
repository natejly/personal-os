export type FileKind = 'pdf' | 'text' | 'image' | 'table' | 'doc' | 'slides' | 'html' | 'code' | 'archive' | 'folder' | 'file'

const BY_EXT: Record<string, FileKind> = {
  pdf: 'pdf',
  md: 'text', markdown: 'text', txt: 'text',
  png: 'image', jpg: 'image', jpeg: 'image', gif: 'image', webp: 'image', svg: 'image', heic: 'image',
  csv: 'table', tsv: 'table', xlsx: 'table', xls: 'table',
  docx: 'doc', doc: 'doc',
  pptx: 'slides', ppt: 'slides',
  html: 'html', htm: 'html',
  json: 'code', js: 'code', ts: 'code', tsx: 'code', jsx: 'code', py: 'code', sh: 'code', css: 'code', yaml: 'code', yml: 'code',
  zip: 'archive', tar: 'archive', gz: 'archive'
}

/** What kind of file a path names, from its extension; a trailing "/" is a folder. */
export function fileKind(path: string): FileKind {
  if (path.endsWith('/')) return 'folder'
  return BY_EXT[path.split('.').pop()?.toLowerCase() ?? ''] ?? 'file'
}

export const baseName = (path: string): string => path.replace(/\/+$/, '').split('/').pop() || path

import { useRef } from 'react'
import { FileText, Upload } from '../icons'
import { useStore } from '../store'
import { tip } from '../lib/tip'
import { useViewCommand } from '../lib/viewCommands'

/** Drop target helper: true while a drag carries files from the desktop (not a note or folder dragged inside the tree). */
export const dragHasFiles = (e: { dataTransfer: DataTransfer | null }): boolean => Array.from(e.dataTransfer?.types ?? []).includes('Files')

/**
 * The one upload control: an icon button over a hidden input (id `doc-upload-input`, the id the Upload shortcut and
 * the palette click). `projectId` files the upload under that project; null is personal. The Files view mounts the primary one; any other mount passes `primary={false}` so the id and the palette command stay single.
 */
export function FilesUpload({ projectId = null, className = 'icon-btn ghost', primary = true }: { projectId?: string | null; className?: string; primary?: boolean }): JSX.Element {
  const upload = useStore((s) => s.uploadDocuments)
  const ref = useRef<HTMLInputElement>(null)
  useViewCommand('files:upload', () => { if (primary) ref.current?.click() })
  return (
    <>
      <input id={primary ? 'doc-upload-input' : undefined} ref={ref} type="file" multiple hidden
        onChange={(e) => { if (e.target.files?.length) void upload(e.target.files, projectId); e.target.value = '' }} />
      <button type="button" className={className} title={tip('Upload file', 'upload')} aria-label="Upload file" onClick={() => ref.current?.click()}><Upload size={14} /></button>
    </>
  )
}

/** The one empty state for a list of files: an icon and a title. */
export function FilesEmpty(): JSX.Element {
  return (
    <div className="empty-state">
      <FileText size={28} />
      <h2>No files</h2>
    </div>
  )
}

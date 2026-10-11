import { File, FileText, FilePdf, FileDoc, FilePpt, FileHtml, FileCsv, FileImage, FileCode2, FileArchive, Folder, type Icon } from '../icons'
import type { FileKind } from '../lib/fileKind'

export const FILE_KIND_ICON: Record<FileKind, Icon> = {
  pdf: FilePdf, text: FileText, image: FileImage, table: FileCsv, doc: FileDoc, slides: FilePpt,
  html: FileHtml, code: FileCode2, archive: FileArchive, folder: Folder, file: File
}

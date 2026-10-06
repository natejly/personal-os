import test from 'node:test'
import assert from 'node:assert/strict'
import { contextMenuTemplate } from './contextMenu'

const base = { misspelledWord: '', dictionarySuggestions: [] as string[], selectionText: '', isEditable: true }
const acts = (log: string[] = []) => ({
  replaceMisspelling: (s: string) => log.push(`replace:${s}`),
  addToDictionary: (w: string) => log.push(`add:${w}`),
  verbs: [{ label: 'Explain', click: () => log.push('explain') }]
})
const labels = (t: ReturnType<typeof contextMenuTemplate>): string[] => t.map((i) => i.label ?? i.role ?? i.type ?? '')

test('a misspelled word gets its suggestions first, then Add to Dictionary, then the edit items', () => {
  const log: string[] = []
  const t = contextMenuTemplate({ ...base, misspelledWord: 'teh', dictionarySuggestions: ['the', 'ten'] }, acts(log))
  assert.deepEqual(labels(t), ['the', 'ten', 'Add to Dictionary', 'separator', 'cut', 'copy', 'paste', 'selectAll'])
  t[0].click?.({} as never, undefined, {} as never)
  t[2].click?.({} as never, undefined, {} as never)
  assert.deepEqual(log, ['replace:the', 'add:teh'])
})

test('a misspelled word with no suggestions shows a disabled placeholder', () => {
  const t = contextMenuTemplate({ ...base, misspelledWord: 'xqzv' }, acts())
  assert.equal(t[0].label, 'No suggestions')
  assert.equal(t[0].enabled, false)
  assert.equal(t[1].label, 'Add to Dictionary')
})

test('an editable field with no selection still offers the edit items', () => {
  assert.deepEqual(labels(contextMenuTemplate(base, acts())), ['cut', 'copy', 'paste', 'selectAll'])
})

test('selected text in an editable field adds the verbs after the edit items', () => {
  const t = contextMenuTemplate({ ...base, selectionText: 'hi' }, acts())
  assert.deepEqual(labels(t), ['cut', 'copy', 'paste', 'selectAll', 'separator', 'Explain'])
})

test('a non-editable selection gets copy and the verbs; a misspelling there is ignored', () => {
  const t = contextMenuTemplate({ ...base, isEditable: false, selectionText: 'hi', misspelledWord: 'teh' }, acts())
  assert.deepEqual(labels(t), ['copy', 'separator', 'Explain'])
})

test('non-editable text with no selection shows no menu', () => {
  assert.deepEqual(contextMenuTemplate({ ...base, isEditable: false }, acts()), [])
})

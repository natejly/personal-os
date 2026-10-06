import test from 'node:test'
import assert from 'node:assert/strict'
import { exportFilename, formatTranscript } from './exportText'

const lines = [
  { t_start: 3, who: 'You', text: 'Let us start.' },
  { t_start: 3725, who: 'Them', text: '  Sounds good.  ' },
  { t_start: 4000, who: 'You', text: '' }
]

test('txt puts a timestamp and speaker on every line and drops empty ones', () => {
  assert.equal(formatTranscript(lines, 'txt'), '[00:03] You: Let us start.\n[1:02:05] Them: Sounds good.\n')
})

test('txt and md carry the title when there is one', () => {
  assert.equal(formatTranscript(lines.slice(0, 1), 'txt', 'Standup'), 'Standup\n\n[00:03] You: Let us start.\n')
  assert.equal(formatTranscript(lines.slice(0, 1), 'md', 'Standup'), '# Standup\n\n**You** `00:03`\nLet us start.\n')
})

test('an empty transcript is an empty body, not a stray newline', () => {
  assert.equal(formatTranscript([], 'txt'), '')
  assert.equal(formatTranscript([], 'md'), '')
})

test('exportFilename slugs the title and falls back', () => {
  assert.equal(exportFilename('Q3 Planning / notes!', 'md'), 'q3-planning-notes-transcript.md')
  assert.equal(exportFilename('???', 'txt'), 'recording-transcript.txt')
})

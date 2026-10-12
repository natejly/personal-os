import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const src = readFileSync('src/renderer/src/components/MailView.tsx', 'utf8')
const button = (cls: string, label: string): string => {
  const m = src.match(new RegExp(`<button className="${cls}"[^>]*>(?:(?!</button>).)*${label}`))
  assert.ok(m, `${label} button not found`)
  return m![0]
}

test('Reply stays disabled until the body of the open message is loaded', () => {
  assert.match(button('primary-btn', 'Reply'), /disabled=\{!full \|\| full\.id !== m\.id\}/)
})

test('Forward uses the same loaded-body gate', () => {
  assert.match(button('ghost-btn', 'Forward'), /disabled=\{!full \|\| full\.id !== m\.id\}/)
})

test('compose validity goes through parseAddressList for to, cc and bcc', () => {
  assert.match(src, /import \{[^}]*\bparseAddressList\b[^}]*\} from '..\/lib\/mailCompose'/)
  const line = src.split('\n').find((l) => l.includes('const composeValid')) ?? ''
  assert.ok(line.includes('composeInvalid === null'), 'any invalid entry must block sending')
  assert.ok(line.includes('parseAddressList(compose.to).ok.length > 0'), 'needs one valid recipient')
  assert.ok(!src.includes('/\\S+@\\S+/'), 'the loose address test must be gone')
  assert.match(src, /parseAddressList\(`\$\{compose\.to\},\$\{compose\.cc\},\$\{compose\.bcc\}`\)/)
})

test('the offending entry is shown next to the disabled buttons', () => {
  assert.match(src, /invalidEntry !== null && <span className="muted"[^>]*>"\{invalidEntry\}" is not a valid email address\./)
})

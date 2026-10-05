import test from 'node:test'
import assert from 'node:assert/strict'
import { speakableText, speechChunks } from './speak'

test('code blocks are replaced, links keep their text, markup is dropped', () => {
  const md = '# Plan\n\nSee [the docs](https://x.io/a) and **bold** `inline`.\n\n```ts\nconst a = 1\n```\n\n- first\n- second item\n1. third'
  assert.equal(speakableText(md), 'Plan. See the docs and bold inline. code omitted. first. second item. third.')
})

test('an unclosed fence is still omitted, bare urls and tables go quiet', () => {
  assert.equal(speakableText('Run:\n```\nrm -rf x'), 'Run: code omitted.')
  assert.equal(speakableText('Visit https://example.com now'), 'Visit now.')
  assert.equal(speakableText('| a | b |\n|---|---|\n| 1 | 2 |'), 'a, b. 1, 2.')
})

test('chunks stay under the cap at sentence edges', () => {
  const t = 'One two three. Four five six. Seven eight nine.'
  assert.deepEqual(speechChunks(t, 20), ['One two three.', 'Four five six.', 'Seven eight nine.'])
  assert.deepEqual(speechChunks(''), [])
})

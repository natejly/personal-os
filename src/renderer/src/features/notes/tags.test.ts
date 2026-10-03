import test from 'node:test'
import assert from 'node:assert/strict'
import { extractTags } from './tags'

test('headings and inline code are not tags; nested tags keep their slash', () => {
  assert.deepEqual(extractTags('# Heading\n## #nope\n`#x` see #todo/now and #Todo/now'), ['todo/now'])
})

test('fences and math blocks are skipped, unicode letters work, mid-word hashes do not', () => {
  const src = '```\n#in-fence\n```\n$$\n#math\n$$\na#b #café #1 #-x\n#after'
  assert.deepEqual(extractTags(src), ['café', 'after'])
})

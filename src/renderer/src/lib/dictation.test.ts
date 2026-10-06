import test from 'node:test'
import assert from 'node:assert/strict'
import { dictationText } from './dictation'

test('spacing: a space after a word, none after whitespace or an opener, none before punctuation', () => {
  assert.equal(dictationText('and then we left', 'We arrived,'), ' and then we left')
  // prices stay prices: the preview would set `$12 ... $40` as a formula
  assert.equal(dictationText('it is $12 now and $40 later', 'Well, '), 'it is \\$12 now and \\$40 later')
  assert.equal(dictationText('and then', 'We arrived, '), 'and then')
  assert.equal(dictationText('quoted', 'He said "'), 'quoted')
  assert.equal(dictationText('(aside', 'See ('), '(aside')
  assert.equal(dictationText(', right', 'Ok'), ', right')
  assert.equal(dictationText('and done', ''), 'And done')
})

test('capitalisation: starts of sentences and lines, but not mid-sentence', () => {
  assert.equal(dictationText('next point', 'First point.'), ' Next point')
  assert.equal(dictationText('next point', 'First point. '), 'Next point')
  assert.equal(dictationText('next point', 'First point?"'), ' Next point')
  assert.equal(dictationText('item two', '- item one\n'), 'Item two')
  assert.equal(dictationText('Alice joined', 'we met with'), ' Alice joined')
  assert.equal(dictationText('and also', 'we met with'), ' and also')
})

test('commands: only a whole utterance is a command', () => {
  assert.equal(dictationText('new line', 'text'), '\n')
  assert.equal(dictationText('New line.', 'text'), '\n')
  assert.equal(dictationText('new paragraph', 'text'), '\n\n')
  assert.equal(dictationText('New paragraph!', 'text\n'), '\n')
  assert.equal(dictationText('new paragraph', 'text\n\n'), '')
  assert.equal(dictationText('start a new line here', 'text'), ' start a new line here')
})

test('spoken punctuation attaches to the previous word', () => {
  assert.equal(dictationText('period', 'hello'), '.')
  assert.equal(dictationText('Comma.', 'hello'), ',')
  assert.equal(dictationText('question mark', 'is it'), '?')
  assert.equal(dictationText('the period of time', 'in'), ' the period of time')
})

test('bullets and headings start at a line start', () => {
  assert.equal(dictationText('bullet', 'text'), '\n- ')
  assert.equal(dictationText('next bullet', 'text\n'), '- ')
  assert.equal(dictationText('bullet', '- '), '')
  assert.equal(dictationText('heading two', 'text'), '\n## ')
  assert.equal(dictationText('heading one', ''), '# ')
  assert.equal(dictationText('first item', '- '), 'First item')
})

test('fillers are stripped as whole words only', () => {
  assert.equal(dictationText('um we should, uh, go', 'x.'), ' We should, go')
  assert.equal(dictationText('the umbrella', 'x.'), ' The umbrella')
  assert.equal(dictationText('um, new line', 'text'), '\n')
  assert.equal(dictationText('uh', 'x'), '')
})

test('editor commands type nothing', () => {
  assert.equal(dictationText('scratch that', 'x'), '')
  assert.equal(dictationText('Stop dictation.', 'x'), '')
})

test('whitespace and empty input', () => {
  assert.equal(dictationText('   ', 'x'), '')
  assert.equal(dictationText('a   b', 'x.\n'), 'A b')
})

test('spoken punctuation: period, comma and question mark, mid-clip too', () => {
  assert.equal(dictationText('thanks period', 'Hi '), 'thanks.')
  assert.equal(dictationText('Thanks, period.', 'Hi '), 'Thanks.')
  assert.equal(dictationText('hello comma there', 'Hi '), 'hello, there')
  assert.equal(dictationText('are you coming question mark', 'Hi '), 'are you coming?')
  assert.ok(!dictationText('no marks here', 'Hi ').includes('.'))
  assert.equal(dictationText('new paragraph', 'Hi.'), '\n\n')
  assert.equal(dictationText('it costs $5', 'Hi '), 'it costs \\$5')
})

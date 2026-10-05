import test from 'node:test'
import assert from 'node:assert/strict'
import { pageSizeFor, printFilename } from './printDoc'

test('printFilename makes a safe .pdf name', () => {
  assert.equal(printFilename('Problem Set 3'), 'Problem Set 3.pdf')
  assert.equal(printFilename('a/b: c?'), 'a b c.pdf')
  assert.equal(printFilename('  '), 'Untitled.pdf')
  assert.equal(printFilename('.hidden'), 'hidden.pdf')
})

test('pageSizeFor picks Letter for North America, A4 otherwise', () => {
  assert.equal(pageSizeFor('en-US'), 'Letter')
  assert.equal(pageSizeFor('fr_CA'), 'Letter')
  assert.equal(pageSizeFor('en-GB'), 'A4')
  assert.equal(pageSizeFor('de-DE'), 'A4')
  assert.equal(pageSizeFor('en'), 'A4')
  assert.equal(pageSizeFor(''), 'A4')
})

import assert from 'node:assert/strict'
import { test } from 'node:test'
import { attachScript, isAttachId } from './codingAttach'

test('only 8 lowercase hex characters are accepted', () => {
  assert.ok(isAttachId('0a1b2c3d'))
  for (const bad of ['0A1B2C3D', '0a1b2c3', '0a1b2c3d4', '0a1b2c3g', '0a1b2c3d; rm -rf ~', '0a1b2c3d\n', '', null, 5]) assert.ok(!isAttachId(bad), String(bad))
})

test('the script is a login-shell attach and nothing else', () => {
  assert.equal(attachScript('0a1b2c3d'), '#!/bin/zsh -l\nclaude attach 0a1b2c3d\n')
})

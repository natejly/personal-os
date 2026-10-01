import { test } from 'node:test'
import assert from 'node:assert/strict'
import { fromConfigJson, joinArgv, parseEnvText, tokenize } from './McpSettings'

test('tokenize splits a command line and keeps quoted paths whole', () => {
  assert.deepEqual(tokenize('npx -y @modelcontextprotocol/server-filesystem ~/Documents'),
    ['npx', '-y', '@modelcontextprotocol/server-filesystem', '~/Documents'])
  assert.deepEqual(tokenize('python "/My Drive/stub.py" --mode friendly'),
    ['python', '/My Drive/stub.py', '--mode', 'friendly'])
  assert.deepEqual(tokenize("sh -c 'echo hi'"), ['sh', '-c', 'echo hi'])
  assert.deepEqual(tokenize('   '), [])
})

test('joinArgv round-trips a path with a space back into one quoted token', () => {
  const argv = ['/My Drive/stub.py', '--mode', 'friendly']
  const line = joinArgv('python', argv)
  assert.equal(line, 'python "/My Drive/stub.py" --mode friendly')
  assert.deepEqual(tokenize(line), ['python', ...argv])
})

test('parseEnvText keeps values containing = and drops blanks and comments', () => {
  assert.deepEqual(parseEnvText('A=1\n\n# note\nB=x=y\n  C = 3  '), { A: '1', B: 'x=y', C: '3' })
  assert.deepEqual(parseEnvText('BARE'), { BARE: '' })
  assert.deepEqual(parseEnvText(''), {})
})

test('fromConfigJson reads a published mcpServers block', () => {
  const got = fromConfigJson(JSON.stringify({
    mcpServers: { filesystem: { command: 'npx', args: ['-y', 'server-filesystem', '/tmp'], env: { TOKEN: 'abc' } } }
  }))
  assert.deepEqual(got, { name: 'filesystem', argv: 'npx -y server-filesystem /tmp', envText: 'TOKEN=abc' })
})

test('fromConfigJson also accepts the bare inner entry', () => {
  assert.deepEqual(fromConfigJson('{"command":"uvx","args":["mcp-server-git"]}'),
    { name: undefined, argv: 'uvx mcp-server-git', envText: '' })
})

test('fromConfigJson returns null for anything that is not a launch config', () => {
  for (const bad of ['not json', '[]', '{}', '{"mcpServers":{}}', '{"mcpServers":{"a":{"url":"http://x"}}}', 'null'])
    assert.equal(fromConfigJson(bad), null, bad)
})

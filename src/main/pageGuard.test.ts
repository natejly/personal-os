import assert from 'node:assert/strict'
import { test } from 'node:test'
import { chromeIpv4, hostBlocked, isLoopbackHost, isPrivateHost } from './pageGuard'

test('chrome spellings of loopback are private before any DNS lookup', () => {
  for (const host of ['127.0.0.1', '2130706433', '0x7f000001', '0x7f.0.0.1', '0177.0.0.1', '127.1', '127.0.1', '0']) {
    assert.equal(isPrivateHost(host), true, host)
  }
  assert.equal(chromeIpv4('0177.0.0.1'), '127.0.0.1')
  assert.equal(chromeIpv4('8.8.8.8'), '8.8.8.8')
  assert.equal(isPrivateHost('8.8.8.8'), false)
  assert.equal(isPrivateHost('example.com'), false)
})

test('link-local, private and mapped addresses are refused', () => {
  for (const host of ['169.254.169.254', '10.1.2.3', '192.168.0.1', '172.16.0.1', '[::1]', 'fe80::1', '::ffff:127.0.0.1', 'localhost']) {
    assert.equal(isPrivateHost(host), true, host)
  }
})

test('site-local and embedded loopback, link-local and private addresses are refused', () => {
  for (const host of [
    'fec0::1', '[fec0::1]', 'fed0::1', 'feff::1',
    '::ffff:7f00:1', '[::ffff:7f00:1]', '::ffff:a9fe:a9fe', '::ffff:a00:1',
    '::7f00:1', '2002:7f00:1::', '2002:a00:1::', '2002:a9fe:a9fe::',
    '64:ff9b::7f00:1', '64:ff9b::a9fe:a9fe', '64:ff9b:1::1', '2001:0::1',
    'fe80::1%lo0', '10.1'
  ]) {
    assert.equal(isPrivateHost(host), true, host)
  }
  // Public embeds stay reachable. 6to4 to 8.8.8.8 is a public address.
  for (const host of ['8.8.8.8', '2002:808:808::', '::ffff:8.8.8.8', '::ffff:808:808', '64:ff9b::808:808', '2001:4860:4860::8888', 'example.com']) {
    assert.equal(isPrivateHost(host), false, host)
  }
})

test('loopback spellings are loopback, and LAN addresses are not', () => {
  for (const host of ['localhost', '127.0.0.1', '::1', '[::1]', '127.1', '0x7f.0.0.1', '0177.0.0.1', '2130706433', '0', '::ffff:127.0.0.1', '::7f00:1']) {
    assert.equal(isLoopbackHost(host), true, host)
  }
  for (const host of ['10.1.2.3', '192.168.0.1', '8.8.8.8', 'example.com', '2002:808:808::']) {
    assert.equal(isLoopbackHost(host), false, host)
  }
})

test('a literal private address is refused even when DNS would call it public', async () => {
  assert.equal(await hostBlocked('fec0::1', async () => ['93.184.216.34']), true)
  assert.equal(await hostBlocked('[2002:7f00:1::]', async () => ['93.184.216.34']), true)
})

test('a name that resolves privately is refused, and a lookup failure is refused', async () => {
  assert.equal(await hostBlocked('rebind.test', async () => ['127.0.0.1']), true)
  assert.equal(await hostBlocked('rebind.test', async () => ['93.184.216.34']), false)
  assert.equal(await hostBlocked('rebind.test', async () => { throw new Error('nxdomain') }), true)
  assert.equal(await hostBlocked('0177.0.0.1', async () => ['177.0.0.1']), true)
})

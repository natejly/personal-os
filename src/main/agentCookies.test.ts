import assert from 'node:assert/strict'
import { test } from 'node:test'
import { clearSignIn, listSignIns, type CookieSession } from './agentCookies'

type C = { domain: string; path: string; name: string; secure: boolean }

function fake(jar: C[]): CookieSession & { removed: string[]; cleared: string[] } {
  const removed: string[] = []
  const cleared: string[] = []
  return {
    removed,
    cleared,
    cookies: {
      get: async () => jar.slice(),
      remove: async (url, name) => {
        removed.push(`${url} ${name}`)
        const u = new URL(url)
        const i = jar.findIndex((c) => c.name === name && c.domain.replace(/^\./, '') === u.hostname && c.path === u.pathname)
        if (i >= 0) jar.splice(i, 1)
      }
    },
    clearStorageData: async (o) => { cleared.push(o?.origin ?? '*') }
  }
}

test('sign-ins group by site with the leading dot dropped', async () => {
  const ses = fake([
    { domain: '.example.com', path: '/', name: 'sid', secure: true },
    { domain: 'example.com', path: '/app', name: 'pref', secure: false },
    { domain: 'mail.other.org', path: '/', name: 'a', secure: true }
  ])
  assert.deepEqual(await listSignIns(ses), [{ domain: 'example.com', count: 2 }, { domain: 'mail.other.org', count: 1 }])
})

test('removing a site removes its cookies by url and keeps every other site', async () => {
  const ses = fake([
    { domain: '.example.com', path: '/', name: 'sid', secure: true },
    { domain: 'example.com', path: '/app', name: 'pref', secure: false },
    { domain: 'www.example.com', path: '/', name: 'w', secure: true },
    { domain: 'other.org', path: '/', name: 'a', secure: true }
  ])
  assert.equal(await clearSignIn(ses, 'example.com'), 2)
  assert.deepEqual(ses.removed, ['https://example.com/ sid', 'http://example.com/app pref'])
  assert.deepEqual(ses.cleared, ['https://example.com', 'http://example.com'])
  assert.deepEqual(await listSignIns(ses), [{ domain: 'other.org', count: 1 }, { domain: 'www.example.com', count: 1 }])
})

test('an empty domain clears nothing', async () => {
  const ses = fake([{ domain: 'a.com', path: '/', name: 'x', secure: true }])
  assert.equal(await clearSignIn(ses, ''), 0)
  assert.equal(ses.removed.length + ses.cleared.length, 0)
})

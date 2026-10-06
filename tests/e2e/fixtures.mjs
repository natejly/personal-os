// `import { test, expect } from './fixtures.mjs'` gives every test a fresh, isolated app as `grain`.
import { test as base, expect } from '@playwright/test'
import { launchApp } from './harness.mjs'

export const test = base.extend({
  grain: async ({}, use, testInfo) => {
    const g = await launchApp({ name: testInfo.title.replace(/\W+/g, '-').slice(0, 40) })
    try {
      await use(g)
    } finally {
      if (testInfo.status !== testInfo.expectedStatus) {
        try { await testInfo.attach('screenshot', { body: await g.page.screenshot(), contentType: 'image/png' }) } catch {}
        testInfo.attach('backend.log', { body: g.backend.log().slice(-20000), contentType: 'text/plain' })
        testInfo.attach('console.errors', { body: g.consoleErrors.join('\n'), contentType: 'text/plain' })
      }
      await g.close()
    }
  }
})
export { expect }

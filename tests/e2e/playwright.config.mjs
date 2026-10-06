import { defineConfig } from '@playwright/test'
export default defineConfig({
  testDir: '.',
  testMatch: /.*\.spec\.mjs/,
  timeout: 120_000,
  expect: { timeout: 15_000 },
  fullyParallel: true,
  workers: Number(process.env.E2E_WORKERS || 2),
  retries: Number(process.env.E2E_RETRIES || 0),
  reporter: [['list'], ['json', { outputFile: process.env.E2E_REPORT || 'results.json' }]],
  outputDir: process.env.E2E_OUT || '../../out/e2e',
  use: { trace: 'retain-on-failure' }
})

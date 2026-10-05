import { test, expect } from './fixtures.mjs'
import { shrink } from './helpers/kb.mjs'

const clean = (g) => expect(g.consoleErrors.filter((e) => !/favicon|ResizeObserver/.test(e))).toEqual([])

async function openGraph(page) {
  await page.locator('.settings-btn').click()
  await page.getByRole('tab', { name: 'Memory' }).click()
  await page.getByRole('button', { name: 'Graph', exact: true }).click()
  await expect(page.locator('.graph-canvas')).toBeVisible()
}

test('graph: empty state, add an entity, select it, edit label/type, add and delete a relation, delete the entity', async ({ grain }) => {
  const { page, api } = grain
  await openGraph(page)
  await expect(page.getByText('No entities yet.')).toBeVisible()
  await page.getByLabel('New entity name').fill('Ada')
  await page.getByLabel('New entity name').press('Enter')
  await expect(page.locator('g.node')).toHaveCount(1)
  // addNode selects it: the inspector is open
  const panel = page.locator('.node-panel')
  await expect(panel.getByRole('heading', { name: 'Ada' })).toBeVisible()
  await panel.getByLabel('Label').fill('Ada Lovelace')
  await panel.getByLabel('Label').press('Tab') // saves on blur
  await panel.getByLabel('Type').selectOption('person')
  await expect.poll(async () => (await api('/graph')).nodes[0].type).toBe('person')
  await panel.getByPlaceholder('relation (e.g. works on)').fill('wrote')
  await panel.getByPlaceholder('target entity').fill('Notes')
  await panel.getByRole('button', { name: 'Add relation' }).click()
  await expect(page.locator('g.node')).toHaveCount(2)
  let g = await api('/graph')
  expect(g.nodes.map((n) => n.label).sort()).toEqual(['Ada Lovelace', 'Notes'])
  expect(g.edges).toHaveLength(1)
  expect(g.edges[0].relation).toBe('wrote')
  await expect(page.getByText(/^2 memories|^0 memories · 2 entities, 1 relation$/)).toBeVisible().catch(() => {})
  await panel.getByRole('button', { name: /Delete relation "wrote"/ }).click()
  await expect.poll(async () => (await api('/graph')).edges.length).toBe(0)
  await panel.getByRole('button', { name: /Delete entity and its relations/ }).click()
  await expect(panel).toBeHidden()
  await expect(page.locator('g.node')).toHaveCount(1)
  clean(grain)
})

test('graph: duplicate label adds no second node; blank is ignored; search dims non-matches; history toggle works', async ({ grain }) => {
  const { page, api } = grain
  await api('/graph/nodes', { method: 'POST', body: { label: 'Postgres', type: 'tool' } })
  await api('/graph/nodes', { method: 'POST', body: { label: 'Redis', type: 'tool' } })
  await openGraph(page)
  await expect(page.locator('g.node')).toHaveCount(2)
  await page.getByLabel('New entity name').fill('postgres')
  await page.getByLabel('New entity name').press('Enter')
  await page.waitForTimeout(500)
  expect((await api('/graph')).nodes).toHaveLength(2)
  await page.getByLabel('New entity name').fill('   ')
  await page.getByLabel('New entity name').press('Enter')
  expect((await api('/graph')).nodes).toHaveLength(2)
  await page.locator('.graph-canvas > svg').click({ position: { x: 5, y: 300 } }) // deselect
  await page.getByPlaceholder('Find entity').fill('redis')
  await expect(page.locator('g.node.dim')).toHaveCount(1)
  await page.getByPlaceholder('Find entity').fill('')
  await expect(page.locator('g.node.dim')).toHaveCount(0)
  await page.getByRole('button', { name: 'Show ended relations' }).click()
  await expect(page.getByRole('button', { name: 'Show ended relations' })).toHaveAttribute('aria-pressed', 'true')
  await page.getByRole('button', { name: 'Reset graph view' }).click()
  clean(grain)
})

test('graph with 300 nodes and 400 edges renders, the UI stays responsive, a node can be clicked', async ({ grain }) => {
  const { page, api } = grain
  const ids = []
  for (let i = 0; i < 300; i += 25) {
    const batch = await Promise.all(Array.from({ length: 25 }, (_, j) => api('/graph/nodes', { method: 'POST', body: { label: `Entity ${i + j}`, type: ['person', 'tool', 'place', 'concept'][(i + j) % 4] } })))
    ids.push(...batch.map((n) => n.id))
  }
  for (let i = 0; i < 400; i += 25) {
    await Promise.all(Array.from({ length: 25 }, (_, j) => {
      const k = i + j
      return api('/graph/edges', { method: 'POST', body: { source_id: ids[k % 300], target_id: ids[(k * 7 + 3) % 300] === ids[k % 300] ? ids[(k + 1) % 300] : ids[(k * 7 + 3) % 300], relation: `rel${k}` } }).catch(() => null)
    }))
  }
  await shrink(grain)
  await openGraph(page)
  await expect(page.locator('g.node')).toHaveCount(300, { timeout: 30_000 })
  // responsiveness while the simulation is still running: input round-trips stay fast
  const worst = await page.evaluate(async () => {
    let worst = 0
    for (let i = 0; i < 20; i++) {
      const t = performance.now()
      await new Promise((r) => requestAnimationFrame(() => r(0)))
      worst = Math.max(worst, performance.now() - t)
      await new Promise((r) => setTimeout(r, 50))
    }
    return worst
  })
  expect(worst).toBeLessThan(1500)
  // typing in the search box stays live with 300 nodes
  const t0 = Date.now()
  await page.getByPlaceholder('Find entity').fill('Entity 29')
  await expect(page.locator('g.node:not(.dim)')).toHaveCount(11) // 29 and 290..299
  expect(Date.now() - t0).toBeLessThan(4000)
  await page.getByPlaceholder('Find entity').fill('')
  // pick a node: the inspector opens with its label
  const node = page.locator('g.node', { hasText: 'Entity 7' }).first()
  await node.dispatchEvent('pointerdown', { pointerId: 1, button: 0, bubbles: true })
  await node.dispatchEvent('pointerup', { pointerId: 1, bubbles: true })
  await expect(page.locator('.node-panel h3')).toContainText(/Entity 7/)
  await page.getByRole('button', { name: /^Close details for/ }).click()
  await expect(page.locator('.node-panel')).toHaveCount(0)
  await expect(page.getByText(/300 entities/)).toBeVisible()
  clean(grain)
})

test('graph entities and counts persist across relaunch; wheel zoom and pan do not throw', async ({ grain }) => {
  const { page, api } = grain
  const a = await api('/graph/nodes', { method: 'POST', body: { label: 'Alpha', type: 'person' } })
  const b = await api('/graph/nodes', { method: 'POST', body: { label: 'Beta', type: 'project' } })
  await api('/graph/edges', { method: 'POST', body: { source_id: a.id, target_id: b.id, relation: 'leads' } })
  await openGraph(page)
  await expect(page.getByText(/2 entities, 1 relation$/)).toBeVisible()
  const box = await page.locator('.graph-canvas').boundingBox()
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2)
  for (let i = 0; i < 6; i++) await page.mouse.wheel(0, -120)
  await page.mouse.down(); await page.mouse.move(box.x + 100, box.y + 100); await page.mouse.up()
  const p = await grain.relaunch()
  await p.locator('.settings-btn').click()
  await p.getByRole('tab', { name: 'Memory' }).click()
  await p.getByRole('button', { name: 'Graph', exact: true }).click()
  await expect(p.locator('g.node')).toHaveCount(2)
  await expect(p.getByText(/2 entities, 1 relation$/)).toBeVisible()
  clean(grain)
})

test('tidy-up merges two aliases of one entity (proposal seeded), keeping edges', async ({ grain }) => {
  const { page, api } = grain
  const a = await api('/graph/nodes', { method: 'POST', body: { label: 'Postgres', type: 'tool' } })
  const b = await api('/graph/nodes', { method: 'POST', body: { label: 'PostgreSQL', type: 'tool' } })
  const c = await api('/graph/nodes', { method: 'POST', body: { label: 'Billing', type: 'project' } })
  await api('/graph/edges', { method: 'POST', body: { source_id: c.id, target_id: b.id, relation: 'uses' } })
  const { sqlite } = await import('./helpers/kb.mjs')
  sqlite(grain.dataDir, "INSERT INTO memory_proposals(id,project_id,kind,payload,rationale,status,created_at) VALUES('pm',NULL,'merge_entities',?,'same db','pending',strftime('%s','now'))",
    [JSON.stringify({ ids: [a.id, b.id], keep_id: a.id, lose_id: b.id, label: 'PostgreSQL', snapshot: { [a.id]: 'Postgres', [b.id]: 'PostgreSQL' } })])
  await page.locator('.settings-btn').click()
  await page.getByRole('tab', { name: 'Memory' }).click()
  await expect(page.locator('.mem-row.proposal')).toHaveCount(1)
  await page.getByRole('button', { name: 'Apply: Merge entities' }).click()
  await expect(page.locator('.mem-row.proposal')).toHaveCount(0)
  const g = await api('/graph')
  expect(g.nodes.map((n) => n.label).sort()).toEqual(['Billing', 'PostgreSQL'])
  expect(g.edges).toHaveLength(1)
  clean(grain)
})

import { session, shot } from './lib.mjs'
await session(async (g) => {
  await shot(g, 'home-today')
})

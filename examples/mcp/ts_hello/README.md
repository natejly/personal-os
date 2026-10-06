# ts_hello

A small TypeScript MCP server over stdio (`@modelcontextprotocol/sdk` 1.x, `zod`) with two tools,
`greet(name)` and `add(a, b)`.

```bash
npm install
npm run typecheck
npm start          # waits for an MCP client on stdin
```

Add it in Grain with Add custom, transport `stdio`, after `npm install`. Set the working directory to this
folder (use the absolute path) and the command line to:

```
npx tsx index.ts
```

Then press Check. Both tools start at `ask`.

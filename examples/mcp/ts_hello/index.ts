import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";

const server = new McpServer({ name: "hello", version: "0.1.0" });

server.registerTool(
  "greet",
  {
    description: "Greet someone by name.",
    inputSchema: { name: z.string().describe("Who to greet") },
    annotations: { readOnlyHint: true }, // self-reported; Grain still asks before running it
  },
  async ({ name }) => ({ content: [{ type: "text", text: `Hello, ${name}!` }] }),
);

server.registerTool(
  "add",
  {
    description: "Add two numbers.",
    inputSchema: { a: z.number(), b: z.number() },
    annotations: { readOnlyHint: true },
  },
  async ({ a, b }) => ({ content: [{ type: "text", text: String(a + b) }] }),
);

// stdout carries the protocol: log with console.error, never console.log.
await server.connect(new StdioServerTransport());

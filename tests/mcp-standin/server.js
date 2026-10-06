// Stand-in stdio MCP server (newline-delimited JSON-RPC). It never prints the
// key: the "whoami" tool returns whether MCP_TEST_KEY is set and a short
// SHA-256 prefix of it, so tests can prove the server received the right value.
const crypto = require("crypto");
const rl = require("readline").createInterface({ input: process.stdin });
const reply = (id, result) => process.stdout.write(JSON.stringify({ jsonrpc: "2.0", id, result }) + "\n");
rl.on("line", (line) => {
  let m;
  try { m = JSON.parse(line); } catch { return; }
  if (m.id === undefined) return; // notification
  if (m.method === "initialize") {
    reply(m.id, { protocolVersion: "2025-03-26", capabilities: { tools: {} }, serverInfo: { name: "standin", version: "0" } });
  } else if (m.method === "tools/list") {
    reply(m.id, { tools: [{ name: "whoami", description: "reports whether the key arrived", inputSchema: { type: "object", properties: {} } }] });
  } else if (m.method === "tools/call") {
    const k = process.env.MCP_TEST_KEY;
    const out = k ? { present: true, sha256_prefix: crypto.createHash("sha256").update(k).digest("hex").slice(0, 12) } : { present: false };
    reply(m.id, { content: [{ type: "text", text: JSON.stringify(out) }] });
  } else {
    reply(m.id, {});
  }
});

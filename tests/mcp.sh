#!/usr/bin/env bash
# MCP / Codex-TOML path with a stand-in stdio server and an env-var-sourced key.
# Needs the test pods up. Proves: the TOML reaches Codex, the server receives the
# key, other pods do not have it, and the key appears in no log or status output.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
fails=0
pass() { echo "ok   $*"; }
bad() { echo "FAIL $*"; fails=$((fails+1)); }
KEY=$(sed -n 's/^MCP_TEST_KEY=//p' tests/test.env)
WANT=$(printf %s "$KEY" | { sha256sum 2>/dev/null || shasum -a 256; } | cut -c1-12)

echo "== Codex parses the rendered config"
LIST=$(docker exec glaring-builder codex mcp list 2>&1)
echo "$LIST" | grep -q standin && echo "$LIST" | grep -q remote && pass "codex mcp list shows both servers" || bad "codex mcp list: $LIST"
echo "$LIST" | grep -qF "$KEY" && bad "key value printed by codex mcp list" || pass "codex mcp list does not print the key"
docker exec glaring-builder grep -qF "$KEY" /home/pod/.codex/config.toml && bad "key value written into the pod's config.toml" || pass "no key value in the pod's rendered config.toml"
docker exec glaring-builder grep -q 'env_vars = \["MCP_TEST_KEY"\]' /home/pod/.codex/config.toml && pass "config references the key by NAME" || bad "env_vars reference missing"

echo "== the stand-in server receives the key (launched the way Codex launches stdio servers: only declared env vars)"
OUT=$(printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"whoami"}}' |
  docker exec -i glaring-builder sh -c 'env -i PATH="$PATH" MCP_TEST_KEY="$MCP_TEST_KEY" node /opt/mcp/server.js')
echo "$OUT" | grep -q "$WANT" && pass "server saw the key (sha256 prefix $WANT matches)" || bad "server did not receive the right key: $OUT"
echo "$OUT" | grep -qF "$KEY" && bad "server echoed the raw key" || pass "server output holds no raw key"
NOKEY=$(printf '%s\n' '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"whoami"}}' |
  docker exec -i glaring-builder sh -c 'env -i PATH="$PATH" node /opt/mcp/server.js')
echo "$NOKEY" | grep -q '\\"present\\":false' && pass "control: without the env var the server sees no key" || bad "control failed: $NOKEY"

echo "== other pods cannot see it"
for p in orchestrator qa intruder; do
  docker exec "glaring-$p" sh -c 'test -z "$MCP_TEST_KEY"' && pass "$p: MCP_TEST_KEY not in its environment" || bad "$p has MCP_TEST_KEY"
  docker exec "glaring-$p" test -e /opt/mcp/server.js && bad "$p has the MCP server mount" || pass "$p: no MCP server mount"
  docker inspect "glaring-$p" | grep -qF "$KEY" && bad "$p: key in docker inspect" || pass "$p: key not in docker inspect"
  docker exec "glaring-$p" sh -c "cat /proc/*/environ 2>/dev/null | tr '\\0' '\\n' | grep -q '^MCP_TEST_KEY='" && bad "$p: key in /proc environ" || pass "$p: key not in any process environment"
done

echo "== the key is in no log or status output"
LEAK=0
for c in glaring-orchestrator glaring-builder glaring-qa glaring-intruder glaring-egress-builder glaring-egress-qa; do
  docker logs "$c" 2>&1 | grep -qF "$KEY" && { bad "key in logs of $c"; LEAK=1; }
done
[ $LEAK -eq 0 ] && pass "no container or proxy log contains the key"
./glaring --pods tests/pods status 2>&1 | grep -qF "$KEY" && bad "key in glaring status" || pass "glaring status does not print it"
./glaring --pods tests/pods doctor --offline --env-file tests/test.env --json 2>&1 | grep -qF "$KEY" && bad "key in glaring doctor output" || pass "glaring doctor (json) does not print it"
docker exec glaring-builder rig ps 2>&1 | grep -qF "$KEY" && bad "key in rig ps" || pass "rig ps does not print it"

echo "== validation refuses a hostile config"
D=$(mktemp -d "$HOME/.glaring-mcp.XXXXXX"); trap 'rm -rf "$D"' EXIT
mkdir -p "$D/pods"
printf '[mcp_servers.x]\ncommand = "node"\nargs = ["/opt/mcp/server.js"]\nenv_vars = ["GITHUB_TOKEN"]\n' > "$D/c.toml"
printf 'name: evil\nmodel:\n  provider: none\nsecrets: [MCP_TEST_KEY]\ncodex_config: %s\n' "$D/c.toml" > "$D/pods/evil.yaml"
OUT=$(./glaring --pods "$D/pods" status 2>&1); echo "$OUT" | grep -q "did not declare" && pass "reference to a variable the pod did not declare is refused" || bad "undeclared reference accepted"

echo; echo "failed=$fails"; exit $fails

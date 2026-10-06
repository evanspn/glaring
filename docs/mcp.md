# MCP servers and Codex config in a pod

A pod can use MCP servers, including ones that need API keys, without any key
touching the repo or the image.

1. Write a Codex TOML with only `[mcp_servers.*]` tables (see
   `configs/codex.example.toml`) and point the pod at it:

   ```yaml
   codex_config: configs/mine.toml     # relative to the repo, or absolute; gitignored
   secrets: [EXAMPLE_MCP_TOKEN]        # every env var the TOML references MUST be declared here
   egress: [mcp.example.com]           # every MCP URL host must be allowlisted
   ```
2. Put the value in `.env` (`EXAMPLE_MCP_TOKEN=...`). It is passed only to pods that declare it.
3. `glaring up`. glaring validates the file, re-renders the servers itself, and
   merges them after its own provider config. **Precedence:** glaring owns
   everything except `[mcp_servers.*]`; a file with `model`, `model_provider`,
   `sandbox_mode`, `approval_policy`, `hooks`, `profiles`, etc. is refused rather than merged.

## What is validated (and refused)

| Rule | Why |
| --- | --- |
| Only `mcp_servers` at top level | cannot override the provider, sandbox or hooks |
| Env references (`env_vars`, `bearer_token_env_var`, `env_http_headers`) must name variables the pod declared under `secrets:` | a pod cannot reference another pod's keys |
| `env` / `http_headers` literals that look like secrets (secret-ish names, long tokens, `Bearer ...`) | keys belong in `.env` |
| HTTP servers: `https://`, default port, no credentials, no IP literals; host must be in `egress:` (warning if not: the proxy fails closed) | no silent egress |
| `auth = "oauth"/"chatgpt"` | interactive token stores in the pod volume are not supported |
| stdio `command` must be inside the pod (`/workspace`, `/home/pod`, `/opt`, `/usr/...`) | no host paths |
| `npx`, `uvx`, `pip`, `sh -c`, `curl`, `docker`... as the command | can download or run arbitrary code at run time. Opt in per pod with `allow_runtime_install: true` (and allowlist a registry host) |
| `cwd` must be inside the pod, no `..` | |

## Local stdio servers: bake them in

Prefer a derived image with the server pre-installed, then use it in the pod spec:

```dockerfile
FROM glaring-pod:latest
USER root
RUN npm install -g @example/mcp-server@1.2.3
USER 10001:10001
```
```sh
docker build -t my-pod:1 -f Dockerfile.mine .
```
and `image: my-pod:1` in the pod spec; `command = "example-mcp-server"` in the TOML.

## Limits

- An MCP server is code running with the pod's secrets. Treat the TOML as trusted input.
- The key is an environment variable of the pod (visible to every process in it, and to anyone who can run `docker inspect` on the host).
- Real MCP servers were not exercised; the stand-in in `tests/mcp-standin/` proves the config and key path only (assumed, unverified for real servers).

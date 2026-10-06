# Security model

Short version: glaring makes a swarm **much harder to run loose on your
machine**. It is not an unbreakable sandbox, and it does not make an agent
trustworthy.

## What the boundary does

| Control | Where | Tested by |
| --- | --- | --- |
| Non-root (uid 10001), all capabilities dropped, `no-new-privileges`, default seccomp/AppArmor (never `unconfined`), read-only root fs, tmpfs scratch | `glaring up_pod` | `tests/escape.sh` |
| CPU, memory (no swap) and pids limits per pod | pod spec | `tests/escape.sh` |
| No Docker socket, no privileged, no host network/pid, no host home mount; the only bind mounts are the rendered config files and read-only mounts you list; your home or `/` is refused | `glaring`, `load_pod` | `tests/escape.sh`, unit tests |
| Default-deny egress: pods sit on `internal` Docker networks with no route out; the only way out is that pod's own Squid proxy with a hostname allowlist, HTTPS (CONNECT to 443) only | `proxy/`, `squid_conf` | `tests/escape.sh` |
| Per-pod secrets: each pod receives only the env names it declares, via a temporary 0600 `--env-file` that is deleted right after the container is created | `up_pod` | `tests/escape.sh` |
| No long-lived AWS keys or profile in a pod: the host exports short-lived keys for one profile; the pod gets a one-profile credentials file, read-only | `aws_credentials_text` | unit test, `tests/escape.sh` |
| Pods cannot use each other's proxies or secrets, and cannot drive another pod's daemon except through a declared peer; peer daemons need a per-pod bearer token (OpenRig refuses a non-loopback bind without one) | `up_pod` | `tests/escape.sh` |
| Proxy and container logs live in Docker on the host, not in the pod | `glaring logs` | manual |

## What it does NOT protect

- **A container shares the host kernel** on Linux. A kernel or runtime bug can escape it. On macOS the pods run inside the Colima/Docker VM, which adds a layer. Keep Docker and the kernel patched.
- **Anything you give a pod, it can misuse.** A pod with `GITHUB_TOKEN` can do what that token can do. Use fine-grained tokens limited to the repos and scopes the pod needs, and expect to revoke them.
- **Allowed hosts are a data path.** A compromised pod can send data to any host on its allowlist, including the model endpoint and `github.com`. The proxy filters by hostname, not content, and does not do TLS inspection. Keep allowlists small.
- **Bedrock/AWS keys in a pod are usable by the pod** until they expire (typically an hour). Scope the IAM role to Bedrock invoke only.
- **Bearer tokens and secrets are visible to anyone with access to your Docker daemon** (`docker inspect`). Treat Docker access as equivalent to access to these secrets. `~/.local/state/glaring/` holds the generated peer tokens (mode 0600).
- **Peering is control.** A pod that holds a peer's bearer token can drive that peer's OpenRig daemon (create queue rows, send keystrokes to its seats, and so on), so it effectively controls the peer pod. Declare `peers:` only between pods you would trust with each other's seats. Pods on the mesh can reach each other's daemon port (7433); a pod only knows the tokens of its declared peers.
- **DNS.** Pods sit on internal Docker networks. External names do not resolve inside a pod (tested), so DNS is not an exfiltration channel here. This depends on Docker's behavior for internal networks; the test will tell you if a different runtime resolves them.
- **The home volume persists.** Each pod's `/home/pod` and `/workspace` named volumes survive `glaring down`; anything an agent or a login (`glaring login`) stored there stays until `glaring down --purge`. The pod's Codex config is re-copied at each start.
- **IP-literal CONNECTs** are matched without reverse DNS (`dstdomain -n`), so reaching an allowed host's IP does not bypass the hostname allowlist (tested).
- **Wildcard allowlist entries** need at least two labels and are refused for shared-tenant suffixes (for example `github.io`, `amazonaws.com`, `vercel.app`); name exact hosts for those.
- **Mounts** are always read-only; sockets, your home directory and its ancestors, credential/state directories (`.ssh`, `.aws`, `.config`, ...) and system paths are refused, after resolving symlinks. A `rig:` file must live under `rigs/`.
- **MCP servers are code running with the pod's secrets.** `codex_config` is validated (only `[mcp_servers.*]`; env references must be variables the pod declared; secret-looking literals, `auth = oauth`, host paths and run-time installers like `npx`/`sh -c` are refused unless you opt in with `allow_runtime_install`), and glaring re-renders the servers itself rather than copying your text. The key is an environment variable of the pod, so every process in that pod can read it, and so can anyone with Docker access on the host. Real MCP servers have not been exercised; a stand-in was.
- **`glaring doctor`** is read-only and never prints values; for AWS it only reports whether a profile yields short-lived credentials and the last four digits of the account id.
- **AWS:** only profiles that export a session token (SSO or assume-role) are accepted, so a long-lived key is never copied into a pod.
- **Codex runs with its own sandbox off and approvals off** inside the pod (`sandbox_mode = "danger-full-access"`, `approval_policy = "never"`), because the container is the boundary. That means an agent can freely change anything inside its pod, including its workspace volume.
- **Supply chain.** The image installs `@openrig/cli` and `@openai/codex` from npm at pinned versions, and builds on `node:22-bookworm-slim`. Pin by digest in your own fork if that matters to you.
- **Not verified:** rootless Docker/Podman, Docker Desktop, Windows, and Codex-on-Bedrock end to end (see the README status table).

## Secret handling rules

1. `.env` is gitignored and dockerignored. Only `.env.example` (names, no values) is committed.
2. Secrets never enter an image layer, a build arg or a command line; `glaring` never prints them.
3. The pod config rendered by `glaring` (`codex.toml`) contains no secrets.
4. Public repo hygiene: run `scripts/harden-public-repo.sh OWNER/REPO` after creating a public fork, and scan the tree and history for personal data before publishing.

## Reporting

Open a GitHub issue for non-sensitive problems. For something exploitable, use GitHub's private vulnerability reporting on this repository.

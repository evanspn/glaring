# glaring

Run an [OpenRig](https://www.openrig.dev) swarm where **every pod is a
hardened container**, so agents cannot run loose on your machine.

- One container per pod, each with its **own OpenRig daemon**, paired to its
  peers with OpenRig's multi-host feature. Pods talk **only** through queue
  rows. No shared filesystem.
- Locked down: non-root, all capabilities dropped, `no-new-privileges`,
  default seccomp, read-only root filesystem, CPU/memory/pids limits, no Docker
  socket, no privileged mode, no host network, no host home mount.
- **Default-deny egress.** Each pod reaches the internet only through its own
  allowlist proxy (model backend + the hosts its spec names). Proxy logs stay
  outside the pod.
- **No secrets in the repo or the image.** Scoped tokens come from an env file
  at run time; each pod gets only the names it declares. AWS access is short-lived
  keys exported from your profile at run time.
- The model backend is a per-pod setting: **AWS Bedrock** (work), your own
  OpenAI key or login, or any OpenAI-compatible endpoint (home). No image rebuild.

Read [SECURITY.md](SECURITY.md) for what this does and does not protect.

## Status (read this first)

| Piece | State |
| --- | --- |
| Pods as containers, each with its own daemon, paired by queue rows | Verified (Colima on macOS, and in CI on Linux) |
| Cross-pod task: orchestrator -> builder -> qa -> orchestrator | Verified by `tests/e2e.sh` with terminal stand-in seats |
| Hardening and escape/exfil tests (150+ checks) | Verified, in CI |
| Egress allowlist (allowed host works, denied host blocked, no way around the proxy) | Verified |
| Codex starts in a pod and loads the rendered Bedrock provider config | Verified (it reports provider `amazon-bedrock`, the chosen model, no sandbox) |
| **Codex actually answering through Bedrock** | **Assumed, unverified.** Needs your AWS profile and Bedrock access; written from OpenAI's Codex-on-Bedrock docs. |
| Model-backed (Codex) seats running a real task | Not verified (no model backend was reachable when this was built) |
| Podman / rootless Docker | Not verified (the CLI is invoked as `docker`; set `GLARING_DOCKER=podman` at your own risk) |

## Requirements

- Linux or macOS with Docker, [Colima](https://github.com/abiosoft/colima) or Podman's docker-compatible CLI. On macOS use Colima or Docker Desktop (a Linux VM sits between agents and your Mac).
- Python 3.8+ (standard library only).
- AWS CLI v2 on the host if you use Bedrock with an AWS profile.
- Images are built locally from `image/` and `proxy/`; nothing is pulled but Debian/Node base images and the npm packages `@openrig/cli` and `@openai/codex` (pinned versions in `image/Dockerfile`).

## 10-minute quickstart (work laptop, Codex on Bedrock)

```sh
git clone https://github.com/evanspn/glaring && cd glaring

# 1. container runtime (macOS example; skip if Docker already works)
brew install colima docker && colima start --cpu 4 --memory 6

# 2. scoped tokens, loaded at run time only (.env is gitignored AND dockerignored)
cp .env.example .env && $EDITOR .env      # GITHUB_TOKEN, JIRA_API_TOKEN, ...

# 3. images (a few minutes the first time)
./glaring build

# 4. sign in to AWS if your profile uses SSO, then start the pods
aws sso login --profile my-work-profile
./glaring up --aws-profile my-work-profile

# 5. see them
./glaring status
./glaring exec orchestrator rig ps
./glaring logs builder            # container logs; --proxy for the egress log
```

Short-lived AWS keys expire. Refresh them without restarting:
`./glaring refresh-creds --aws-profile my-work-profile`.

Edit Jira host and other allowed hosts in `pods/*.yaml` under `egress:` (a
blocked host shows up as a `TCP_DENIED` line in `./glaring logs --proxy POD`).
Stop everything: `./glaring down` (add `--purge` to also delete workspaces).

### Home setup (no AWS)

In `pods/<name>.yaml` change only the `model:` block, then `./glaring up`:

```yaml
model:
  provider: openai          # your key: put OPENAI_API_KEY in .env and list it under secrets:
```
or, to use a ChatGPT/Codex login stored in the pod's own volume:
`provider: openai`, then `./glaring login builder` (device login; nothing leaves the pod volume).
Any OpenAI-compatible endpoint:

```yaml
model:
  provider: custom
  base_url: https://llm.example.net/v1
  env_key: MY_LLM_KEY       # list MY_LLM_KEY under secrets:
```

## Verify it yourself

```sh
./glaring --pods tests/pods up --env-file tests/test.env   # 4 throwaway pods, fake tokens
./tests/e2e.sh        # a task crosses 3 pod boundaries as queue rows
./tests/escape.sh     # 150+ escape/exfiltration checks; exit code = failures
python3 -m unittest tests.test_glaring
./glaring --pods tests/pods down --purge
```

## Pod spec

A pod is one small YAML file in `pods/` (a strict YAML subset: maps, lists of scalars, `[a, b]`).

| Key | Meaning |
| --- | --- |
| `name` | pod name (`[a-z][a-z0-9-]*`) |
| `rig` | an OpenRig rig spec started inside the pod (this pod's seats) |
| `cpus`, `memory`, `pids` | hard limits (defaults 2, 4g, 512) |
| `model` | `provider: bedrock \| openai \| custom \| none`, plus `region`, `model`, `base_url`, `env_key` |
| `egress` | extra allowed hostnames (HTTPS only; a leading dot allows subdomains) |
| `secrets` | names from the env file this pod may receive |
| `peers` | pods this pod is paired with (it gets only their bearer tokens) |
| `mounts` | explicit read-only host mounts `host_path:/in/pod` (your home or `/` is refused) |

`policy/egress.yaml` adds hosts for every pod (empty by default).
`rigs/example/` has one Codex seat per pod; `rigs/standin/` has terminal seats used by the tests.

## How it fits together

```
 host ── glaring (python) ── docker
   mesh network (internal: no route out)      per-pod egress network (internal)
   orchestrator ◄── queue rows ──► builder      builder ── allowlist proxy ──► internet
   qa           ◄── queue rows ──►              (one proxy per pod, own allowlist)
```

Each pod's daemon listens on the internal mesh only, requires a per-pod bearer
token, and is registered on peers with `rig host add --transport http`.

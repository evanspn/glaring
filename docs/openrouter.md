# OpenRouter as a pod backend (overflow)

A pod can use any OpenAI-compatible endpoint with `provider: custom`.
`examples/pods/overflow-openrouter.yaml` shows OpenRouter:

```yaml
model:
  provider: custom
  base_url: https://openrouter.ai/api/v1
  model: moonshotai/kimi-k2.7-code
  env_key: OPENROUTER_API_KEY
secrets: [OPENROUTER_API_KEY]
```

- The key goes in `.env` only, and only a pod that lists it under `secrets:` gets it.
- Create the key in the OpenRouter dashboard **with a credit limit**. glaring has
  no budget setting of its own; the key's limit is the only spend guard.
- Only `openrouter.ai` is allowlisted for that pod (derived from `base_url`).

**Data leaves the machine.** Everything the pod's agent reads (workspace files, tool output, the task text) is sent to OpenRouter and on to the upstream model provider, who may log it. Do not point such a pod at private or sensitive data, and turn off prompt logging in OpenRouter's privacy settings.

**Status: assumed, unverified.** The config renders and validates in tests, but no
request to OpenRouter was made. Codex talks the OpenAI *Responses* wire protocol
(`wire_api = "responses"`), and whether a given OpenRouter model works with Codex
through it was not tested.

**Pi instead of Codex.** The Pi coding agent (`@earendil-works/pi-coding-agent`)
speaks to OpenRouter natively. The stock image does not include it; add it in a
derived image, as in `docs/mcp.md`:

```dockerfile
FROM glaring-pod:latest
USER root
RUN npm install -g @earendil-works/pi-coding-agent@1.0.4
USER 10001:10001
```
then set `image:` in the pod spec and give Pi the key through the pod's env
(`OPENROUTER_API_KEY` from `secrets:`). This path was not run inside a pod here.

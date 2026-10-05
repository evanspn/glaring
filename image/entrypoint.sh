#!/bin/sh
# glaring pod entrypoint: tmux server, this pod's OpenRig daemon, peer hosts,
# optional rig spec. Runs as the unprivileged pod user on a read-only rootfs.
set -eu

# Codex model backend config is rendered on the host by `glaring` and mounted
# read-only; copy it into the writable home volume so Codex can also write
# its own state next to it.
if [ -f /etc/glaring/codex.toml ]; then
  mkdir -p "$HOME/.codex"
  cp /etc/glaring/codex.toml "$HOME/.codex/config.toml"
fi

tmux start-server
tmux has-session -t base 2>/dev/null || tmux new-session -d -s base

# Non-loopback bind requires the bearer token (OPENRIG_AUTH_BEARER_TOKEN),
# which `glaring up` generates per pod.
rig daemon start --host 0.0.0.0 --port 7433 --no-kernel

# Peers: GLARING_PEERS="name=http://host:7433=ENVVAR_WITH_TOKEN;name2=..."
# Only the peers the pod spec declares are registered, and only their tokens
# are present in this container's environment.
if [ -n "${GLARING_PEERS:-}" ]; then
  OLD_IFS=$IFS; IFS=';'
  for entry in $GLARING_PEERS; do
    IFS=$OLD_IFS
    name=${entry%%=*}; rest=${entry#*=}; url=${rest%%=*}; tokvar=${rest#*=}
    rig host add --id "$name" --transport http --url "$url" --bearer-env "$tokvar" >/dev/null 2>&1 || true
    IFS=';'
  done
  IFS=$OLD_IFS
fi

if [ -f /rig/rig.yaml ]; then
  rig up /rig/rig.yaml || echo "glaring: rig up failed (pod stays up for debugging)" >&2
fi

echo "glaring: pod ${GLARING_POD:-?} ready"
exec sleep infinity

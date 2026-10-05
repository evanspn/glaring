# glaring pod base image: Node + tmux + git + the OpenRig CLI/daemon + Codex.
# No secrets, no credentials, no personal data are ever baked in.
FROM node:22-bookworm-slim AS build
ARG OPENRIG_VERSION=0.6.4
ARG CODEX_VERSION=0.160.0
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential python3 \
 && rm -rf /var/lib/apt/lists/*
RUN npm install -g --omit=dev --no-fund --no-audit \
      @openrig/cli@${OPENRIG_VERSION} @openai/codex@${CODEX_VERSION}

FROM node:22-bookworm-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends tmux git ca-certificates curl jq procps iproute2 \
 && rm -rf /var/lib/apt/lists/*
COPY --from=build /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/@openrig/cli/dist/bin-wrapper.js /usr/local/bin/rig \
 && chmod +x /usr/local/lib/node_modules/@openrig/cli/dist/bin-wrapper.js \
 && ln -s /usr/local/lib/node_modules/@openai/codex/bin/codex.js /usr/local/bin/codex
COPY entrypoint.sh /opt/glaring/entrypoint.sh
RUN chmod 0755 /opt/glaring/entrypoint.sh
# Non-root user; HOME and workspace are volumes/tmpfs at run time.
RUN useradd --uid 10001 --create-home --home-dir /home/pod --shell /bin/bash pod \
 && mkdir -p /workspace && chown pod:pod /workspace
USER 10001:10001
ENV HOME=/home/pod TERM=xterm-256color
WORKDIR /workspace
ENTRYPOINT ["/opt/glaring/entrypoint.sh"]

# Default-deny egress proxy: one instance per pod, allowlist rendered by `glaring`.
FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends squid \
 && rm -rf /var/lib/apt/lists/*
USER proxy
ENTRYPOINT ["squid", "-N", "-f", "/etc/squid/squid.conf"]

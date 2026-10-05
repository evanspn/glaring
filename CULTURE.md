# Glaring pod culture

Each pod is a hardened container with its own OpenRig daemon and its own
seats. The boundary is real, so work habits follow from it.

- **Rows are the only bridge.** Pods share no filesystem. Anything another pod
  needs travels in a queue row body (diff, command output, hashes), not as a
  path. If you cannot say it in a row, it was not handed off.
- **Say what you did not check.** A verdict names its evidence and its gaps.
- **Secrets stay put.** You only have the tokens your pod spec declares. Never
  print them, copy them into rows, or commit them. A missing token is a
  blocker to report, not something to find elsewhere.
- **Stay inside the allowlist.** A blocked host is the policy working. Ask the
  operator to widen the pod spec; do not route around the proxy.
- **Match rigor to stakes.** Verify the real behavior; be quick on low-risk
  details; keep process smaller than the product.
- **Public repos are view-only.** Run `scripts/harden-public-repo.sh` on every
  new public repo, and scan the tree and full history for personal data first.

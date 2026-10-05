#!/usr/bin/env bash
# Escape / exfiltration tests against the running TEST pods.
#   ./glaring --pods tests/pods up --env-file tests/test.env && ./glaring test
# Each check tries to do something a compromised pod would try, and passes only
# when it FAILS to. Exit code is the number of failed checks (0 = all held).
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
PODS="orchestrator builder qa intruder"
fails=0; passes=0
pass() { echo "ok   $*"; passes=$((passes+1)); }
bad() { echo "FAIL $*"; fails=$((fails+1)); }
expect() { # description, command...   (command must succeed)
  local d=$1; shift; if "$@" >/dev/null 2>&1; then pass "$d"; else bad "$d"; fi; }
refuse() { # description, command...   (command must FAIL)
  local d=$1; shift; if "$@" >/dev/null 2>&1; then bad "$d"; else pass "$d"; fi; }
inpod() { local p=$1; shift; docker exec "glaring-$p" "$@"; }
insp() { docker inspect -f "$2" "glaring-$1"; }

# a host canary that must never be visible in any pod
CANARY_FILE="$HOME/.glaring-canary-$$"; CANARY="CANARY-$RANDOM-$RANDOM"
echo "$CANARY" > "$CANARY_FILE"; trap 'rm -f "$CANARY_FILE"' EXIT

for p in $PODS; do
  echo "== pod: $p"
  # --- container configuration (what docker was told) ---
  [ "$(insp $p '{{.HostConfig.Privileged}}')" = false ] && pass "$p not privileged" || bad "$p privileged"
  [ "$(insp $p '{{.HostConfig.ReadonlyRootfs}}')" = true ] && pass "$p read-only rootfs" || bad "$p rootfs writable"
  [ "$(insp $p '{{.HostConfig.CapDrop}}')" = "[ALL]" ] && pass "$p all capabilities dropped" || bad "$p capabilities"
  [ "$(insp $p '{{.HostConfig.CapAdd}}')" = "[]" ] || [ "$(insp $p '{{.HostConfig.CapAdd}}')" = "<no value>" ] && pass "$p no capabilities added" || bad "$p CapAdd set"
  insp $p '{{.HostConfig.SecurityOpt}}' | grep -q no-new-privileges && pass "$p no-new-privileges" || bad "$p no-new-privileges missing"
  insp $p '{{.HostConfig.SecurityOpt}}' | grep -q unconfined && bad "$p seccomp/apparmor unconfined" || pass "$p default seccomp (not unconfined)"
  [ "$(insp $p '{{.Config.User}}')" = "10001:10001" ] && pass "$p runs as uid 10001" || bad "$p user"
  [ "$(insp $p '{{.HostConfig.NetworkMode}}')" != host ] && pass "$p not on the host network" || bad "$p host network"
  [ "$(insp $p '{{.HostConfig.PidMode}}')" = "" ] && pass "$p own pid namespace" || bad "$p shares pid ns"
  [ "$(insp $p '{{.HostConfig.PidsLimit}}')" -gt 0 ] && pass "$p pids limit set" || bad "$p no pids limit"
  [ "$(insp $p '{{.HostConfig.Memory}}')" -gt 0 ] && pass "$p memory limit set" || bad "$p no memory limit"
  [ "$(insp $p '{{.HostConfig.NanoCpus}}')" -gt 0 ] && pass "$p cpu limit set" || bad "$p no cpu limit"
  insp $p '{{range .Mounts}}{{.Source}}|{{.Destination}}|{{.RW}}{{"\n"}}{{end}}' > /tmp/glaring-mounts.$$
  grep -qE 'docker\.sock' /tmp/glaring-mounts.$$ && bad "$p mounts the docker socket" || pass "$p no docker socket mount"
  grep -qF "$HOME|" /tmp/glaring-mounts.$$ && bad "$p mounts the host home" || pass "$p no host home mount"
  # every bind mount must be read-only; only the two named volumes may be writable
  awk -F'|' 'NF>=3 && $3=="true" {print $2}' /tmp/glaring-mounts.$$ | grep -vE '^(/home/pod|/workspace)$' | grep -q . \
    && bad "$p has an unexpected writable mount" || pass "$p writable mounts are only home+workspace volumes"
  rm -f /tmp/glaring-mounts.$$

  # --- from the inside ---
  [ "$(inpod $p id -u)" != 0 ] && pass "$p: not root" || bad "$p: root"
  refuse "$p: cannot write to the root filesystem" inpod $p sh -c 'touch /etc/x'
  refuse "$p: cannot become root (sudo/su)" inpod $p sh -c 'sudo -n true || su -c true'
  [ "$(inpod $p sh -c "grep CapEff /proc/self/status | awk '{print \$2}'")" = 0000000000000000 ] && pass "$p: effective capabilities are empty" || bad "$p: has capabilities"
  inpod $p sh -c "grep NoNewPrivs /proc/self/status" | grep -q 1 && pass "$p: NoNewPrivs=1" || bad "$p: NoNewPrivs"
  refuse "$p: no docker socket" inpod $p test -e /var/run/docker.sock
  refuse "$p: cannot mount (no CAP_SYS_ADMIN)" inpod $p mount -t tmpfs none /mnt
  refuse "$p: cannot see the host canary file" inpod $p sh -c "grep -rqs '$CANARY' /home /workspace /tmp /run /mnt /media /srv /opt /etc 2>/dev/null"
  refuse "$p: no /Users or /host directory" inpod $p sh -c 'test -e /Users -o -e /host -o -e /Volumes'
  refuse "$p: no .env file anywhere in the pod" inpod $p sh -c "find /home /workspace /tmp /run /mnt /srv /opt /etc \\( -name '.env' -o -name '*.env' \\) 2>/dev/null | grep -q ."
  refuse "$p: no AWS config or SSO cache" inpod $p sh -c 'test -e ~/.aws -o -e /root/.aws'
  refuse "$p: no ssh keys" inpod $p sh -c 'ls ~/.ssh/id_* 2>/dev/null | grep -q .'

  # --- network: default deny ---
  refuse "$p: no direct internet (bypassing the proxy)" inpod $p curl -s --noproxy '*' -m 5 -o /dev/null https://1.1.1.1
  refuse "$p: no direct DNS-resolved internet" inpod $p curl -s --noproxy '*' -m 5 -o /dev/null https://example.com
  refuse "$p: denied host is blocked by the proxy" inpod $p curl -sf -m 8 -o /dev/null https://example.com
  refuse "$p: plain-HTTP to the internet is blocked" inpod $p curl -sf -m 8 -o /dev/null http://example.com
  refuse "$p: cannot reach the host (Colima/Docker host gateway)" inpod $p sh -c 'curl -sf --noproxy "*" -m 3 -o /dev/null http://host.docker.internal:22 || curl -sf --noproxy "*" -m 3 -o /dev/null http://192.168.5.2:22'
  refuse "$p: cannot reach the cloud metadata address" inpod $p curl -s --noproxy '*' -m 3 -o /dev/null http://169.254.169.254/
done

echo "== DNS and IP-literal bypass attempts"
for p in $PODS; do
  refuse "$p: external DNS names do not resolve (no DNS exfiltration channel)" inpod $p sh -c 'getent hosts example.com || getent hosts abc123.exfil.example.org'
done
GH_IP=$(python3 -c 'import socket;print(socket.gethostbyname("api.github.com"))' 2>/dev/null || true)
if [ -n "$GH_IP" ]; then
  refuse "builder: CONNECT to an IP literal of an allowed host is denied (no reverse-DNS match)" inpod builder curl -sfk -m 8 -o /dev/null "https://$GH_IP/"
else
  echo "skip IP-literal test (could not resolve api.github.com on the host)"
fi

echo "== allowlist works where it should"
expect "builder: allowed host (api.github.com) is reachable through the proxy" inpod builder curl -s -m 15 -o /dev/null https://api.github.com
refuse "orchestrator: api.github.com is NOT on its allowlist" inpod orchestrator curl -sf -m 8 -o /dev/null https://api.github.com
refuse "qa: api.github.com is NOT on its allowlist" inpod qa curl -sf -m 8 -o /dev/null https://api.github.com

echo "== secrets stay with the pod that declared them"
expect "builder has its declared GITHUB_TOKEN" inpod builder sh -c 'test -n "$GITHUB_TOKEN"'
for p in orchestrator qa intruder; do
  refuse "$p: no GITHUB_TOKEN (not declared)" inpod $p sh -c 'test -n "$GITHUB_TOKEN"'
  refuse "$p: no JIRA_API_TOKEN" inpod $p sh -c 'test -n "$JIRA_API_TOKEN"'
done
for p in $PODS; do
  refuse "$p: no AWS_* credentials in env (non-Bedrock pods)" inpod $p sh -c 'env | grep -q "^AWS_"'
done
refuse "intruder: cannot read another pod's token from its environment" inpod intruder sh -c 'env | grep -q PEER_'
refuse "orchestrator: cannot read builder's secrets via /proc" inpod orchestrator sh -c 'cat /proc/*/environ 2>/dev/null | tr "\0" "\n" | grep -q "^GITHUB_TOKEN="'
refuse "docker env of qa does not contain builder's GITHUB_TOKEN" sh -c "docker inspect glaring-qa | grep -q fake-github-token"

echo "== pods cannot reach each other except the authenticated OpenRig API"
refuse "intruder: cannot call builder's daemon without the bearer token" inpod intruder curl -sf -m 5 http://glaring-builder:7433/api/ps
refuse "intruder: cannot use the builder's egress proxy" inpod intruder curl -sf -m 5 -x http://glaring-egress-builder:3128 -o /dev/null https://api.github.com
refuse "intruder: cannot reach another pod's egress proxy at all" inpod intruder curl -s --noproxy '*' -m 3 -o /dev/null http://glaring-egress-builder:3128
refuse "intruder: cannot read builder's workspace volume" inpod intruder test -e /workspace/result.txt
refuse "intruder: no SSH/other service ports on peers" inpod intruder sh -c 'curl -s --noproxy "*" -m 3 glaring-builder:22 -o /dev/null'

echo
echo "passed=$passes failed=$fails"
exit $fails

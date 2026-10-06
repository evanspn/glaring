"""glaring doctor: dependency and environment check.

Reads, never writes. Never prints credential values: AWS checks only report
whether a profile yields short-lived keys, and env-file checks report variable
NAMES only.
"""
import json
import os
import platform
import re
import shutil
import socket
import ssl
import subprocess
import sys
import time
from email.utils import parsedate_to_datetime
from pathlib import Path

PASS, WARN, FAIL, INFO = "pass", "warn", "fail", "info"
MIN = {"python": (3, 8), "docker": (24, 0), "cpus": 4, "mem_gb": 5.5, "disk_gb": 15.0}


def os_kind():
    s = platform.system()
    return "macos" if s == "Darwin" else "linux" if s == "Linux" else s.lower()


def parse_version(text):
    m = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", text or "")
    return tuple(int(x or 0) for x in m.groups()) if m else None


def run(cmd, timeout=15):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except FileNotFoundError:
        return 127, "", "not found"
    except subprocess.TimeoutExpired:
        return 124, "", "timed out"


class Report:
    def __init__(self):
        self.items = []  # (group, name, status, detail, fix)

    def add(self, group, name, status, detail="", fix=None):
        self.items.append({"group": group, "name": name, "status": status, "detail": detail, "fix": fix})

    def counts(self):
        c = {PASS: 0, WARN: 0, FAIL: 0, INFO: 0}
        for i in self.items:
            c[i["status"]] += 1
        return c


def fix_for(os_name, mac, linux):
    return mac if os_name == "macos" else linux


def check_system(rep, os_name):
    g = "System"
    rep.add(g, "Operating system", PASS if os_name in ("macos", "linux") else FAIL,
            f"{platform.system()} {platform.machine()}",
            None if os_name in ("macos", "linux") else "glaring supports macOS and Linux (Windows: use WSL2)")
    pv = sys.version_info[:3]
    ok = pv >= (*MIN["python"], 0)
    rep.add(g, "Python", PASS if ok else FAIL, ".".join(map(str, pv)) + f" (need >= {MIN['python'][0]}.{MIN['python'][1]})",
            None if ok else fix_for(os_name, "brew install python", "install python3 (>= 3.8) with your package manager"))
    free = shutil.disk_usage(Path.home()).free / 1e9
    rep.add(g, "Free disk (home)", PASS if free >= MIN["disk_gb"] else WARN, f"{free:.0f} GB free (images + volumes need ~{MIN['disk_gb']:.0f} GB)",
            None if free >= MIN["disk_gb"] else "free some space; images are ~1.5 GB and pod volumes grow with use")


def docker_bin():
    return os.environ.get("GLARING_DOCKER", "docker")


def check_runtime(rep, os_name):
    g = "Container runtime"
    dbin = docker_bin()
    path = shutil.which(dbin)
    if not path:
        rep.add(g, "docker CLI", FAIL, f"'{dbin}' not found",
                fix_for(os_name, "brew install colima docker && colima start --cpu 4 --memory 6",
                        "install Docker Engine (https://docs.docker.com/engine/install/) or Podman with its docker-compatible CLI"))
        return None
    rc, out, _ = run([dbin, "--version"])
    ver = parse_version(out)
    rep.add(g, "docker CLI", PASS, out or path)
    rc, out, err = run([dbin, "info", "--format", "{{json .}}"], timeout=30)
    if rc != 0:
        rep.add(g, "Daemon reachable", FAIL, (err.splitlines() or ["cannot connect"])[0][:140],
                fix_for(os_name, "colima start --cpu 4 --memory 6   (or open Docker Desktop)", "sudo systemctl start docker   (and add yourself to the docker group, or use rootless Docker)"))
        return None
    info = json.loads(out)
    server = parse_version(info.get("ServerVersion", ""))
    ok = server is not None and server >= MIN["docker"]
    rep.add(g, "Engine version", PASS if ok else WARN, f"{info.get('ServerVersion')} (need >= {MIN['docker'][0]})",
            None if ok else "upgrade Docker/Colima")
    name = (info.get("Name") or "").lower()
    osn = (info.get("OperatingSystem") or "")
    ctx = run([dbin, "context", "show"])[1]
    kind = ("Colima VM" if "colima" in ctx or "colima" in name else "Docker Desktop VM" if "Docker Desktop" in osn
            else "Podman" if "podman" in (info.get("ServerVersion", "") + osn).lower() else "native Linux engine")
    vm = kind.endswith("VM")
    opts = " ".join(info.get("SecurityOptions") or [])
    rootless = "rootless" in opts
    rep.add(g, "Isolation layer", PASS if (vm or rootless) else WARN,
            f"{kind}{', rootless' if rootless else ''}",
            None if (vm or rootless) else "pods share the host kernel with a root daemon; consider rootless Docker/Podman (see SECURITY.md)")
    rep.add(g, "Default seccomp profile", PASS if "seccomp" in opts else WARN, "active" if "seccomp" in opts else "not reported by the engine",
            None if "seccomp" in opts else "enable the default seccomp profile")
    ncpu, mem = info.get("NCPU", 0), (info.get("MemTotal", 0) or 0) / 2**30
    rep.add(g, "CPUs available to containers", PASS if ncpu >= MIN["cpus"] else WARN, f"{ncpu} (recommended >= {MIN['cpus']})",
            None if ncpu >= MIN["cpus"] else fix_for(os_name, "colima stop && colima start --cpu 4 --memory 6", "give the engine more CPUs"))
    rep.add(g, "Memory available to containers", PASS if mem >= MIN["mem_gb"] else WARN, f"{mem:.1f} GB (recommended >= {MIN["mem_gb"]:.1f})",
            None if mem >= MIN["mem_gb"] else fix_for(os_name, "colima stop && colima start --memory 6", "give the engine more memory"))
    rc, out, _ = run([dbin, "compose", "version"])
    rc2 = run(["docker-compose", "version"])[0] if rc != 0 else 0
    rep.add(g, "Compose (optional)", PASS if (rc == 0 or rc2 == 0) else INFO,
            out.split("\n")[0] if rc == 0 else "not installed (glaring itself does not need it)",
            None)
    rc, out, _ = run([dbin, "buildx", "version"])
    rep.add(g, "BuildKit/buildx (optional)", PASS if rc == 0 else INFO, out.split("\n")[0] if rc == 0 else "legacy builder in use (works, slower, deprecated)",
            None if rc == 0 else fix_for(os_name, "brew install docker-buildx", None))
    return dbin


def check_images(rep, dbin, root):
    g = "Images"
    for tag, ctx in (("glaring-pod:latest", "image"), ("glaring-proxy:latest", "proxy")):
        if dbin and run([dbin, "image", "inspect", tag])[0] == 0:
            rep.add(g, tag, PASS, "present")
        else:
            have = (root / ctx / "Dockerfile").exists()
            rep.add(g, tag, WARN if have else FAIL, "not built yet" if have else f"{ctx}/Dockerfile missing from this install",
                    "glaring build" if have else "reinstall glaring")
    if dbin:
        left = [n for n in run([dbin, "ps", "-a", "--format", "{{.Names}}"])[1].splitlines() if n.startswith("glaring-")]
        rep.add(g, "Leftover glaring containers", PASS if not left else INFO,
                "none" if not left else f"{len(left)} existing (a later 'glaring up' recreates them)", None)


def check_aws(rep, os_name, profile, needs_bedrock):
    g = "AWS (Bedrock)"
    if not needs_bedrock:
        rep.add(g, "Bedrock needed", INFO, "no pod spec uses provider: bedrock")
        return
    exe = shutil.which("aws")
    if not exe:
        rep.add(g, "AWS CLI v2", FAIL, "not found",
                fix_for(os_name, "brew install awscli", "https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html"))
        return
    rc, out, err = run(["aws", "--version"])
    v = parse_version((out or err).split("/")[-1])
    rep.add(g, "AWS CLI v2", PASS if v and v[0] >= 2 else FAIL, out or err,
            None if v and v[0] >= 2 else "install AWS CLI v2 (export-credentials needs it)")
    if not profile:
        rep.add(g, "Profile", WARN, "none given (pass --aws-profile NAME or set AWS_PROFILE)", "glaring doctor --aws-profile NAME")
        return
    rc, out, err = run(["aws", "sts", "get-caller-identity", "--profile", profile, "--output", "json"], timeout=30)
    if rc != 0:
        rep.add(g, f"Profile '{profile}' valid", FAIL, (err.splitlines() or ["not valid"])[-1][:140],
                f"aws sso login --profile {profile}")
        return
    acct = json.loads(out).get("Account", "")
    rep.add(g, f"Profile '{profile}' valid", PASS, f"account ending {acct[-4:]}")
    rc, out, err = run(["aws", "configure", "export-credentials", "--profile", profile, "--format", "env"], timeout=30)
    has_tok = rc == 0 and "AWS_SESSION_TOKEN" in out
    rep.add(g, "Short-lived credentials", PASS if has_tok else FAIL,
            "session token present (values not shown)" if has_tok else "profile yields long-lived keys or none",
            None if has_tok else "use an SSO or assume-role profile; glaring refuses to copy long-lived keys into pods")


def check_config(rep, root, env_file, pods_dir, load_pods):
    g = "Configuration"
    needs_bedrock, required = False, set()
    try:
        pods = load_pods(pods_dir, None)
        rep.add(g, "Pod specs", PASS, f"{len(pods)} valid in {pods_dir}")
        uses_toml = False
        for p in pods.values():
            required.update(p["secrets"])
            needs_bedrock |= p["model"].get("provider") == "bedrock"
            uses_toml |= bool(p.get("codex_config"))
            for w in p["_warnings"]:
                rep.add(g, f"Pod {p['name']}: MCP", WARN, w[-150:], "add the host to the pod's egress: list")
        if uses_toml:
            ok = sys.version_info >= (3, 11)
            rep.add(g, "Python 3.11+ (codex_config validation)", PASS if ok else FAIL, ".".join(map(str, sys.version_info[:3])),
                    None if ok else "install Python 3.11+ (brew install python)")
    except Exception as e:  # noqa: BLE001 - report any spec problem
        rep.add(g, "Pod specs", FAIL, str(e)[:160], "fix the pod spec or pass --pods DIR")
        return False, set()
    env = {}
    if env_file and Path(env_file).is_file():
        mode = Path(env_file).stat().st_mode & 0o077
        rep.add(g, "Env file", PASS, str(env_file))
        if mode:
            rep.add(g, "Env file permissions", WARN, "readable by other users", f"chmod 600 {env_file}")
        for line in Path(env_file).read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    else:
        rep.add(g, "Env file", WARN if required else INFO, f"{env_file or '.env'} not found",
                "cp .env.example .env  # then fill in the names listed below" if required else None)
    for name in sorted(required):
        rep.add(g, f"Secret {name}", PASS if env.get(name) else WARN, "set (value not shown)" if env.get(name) else "declared by a pod but empty/missing",
                None if env.get(name) else f"set {name} in {env_file or '.env'}")
    return needs_bedrock, required


def tls_head(host, timeout=6):
    """Return (ok, http Date header or None). No credentials involved."""
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((host, 443), timeout=timeout) as s, ctx.wrap_socket(s, server_hostname=host) as t:
            t.sendall(f"HEAD / HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\nUser-Agent: glaring-doctor\r\n\r\n".encode())
            data = t.recv(4096).decode("latin-1", "replace")
        m = re.search(r"(?im)^date:\s*(.+)$", data)
        return True, m.group(1).strip() if m else None
    except Exception as e:  # noqa: BLE001
        return False, str(e)[:100]


def check_network(rep, os_name, hosts):
    g = "Network"
    skew = None
    for h in hosts:
        ok, extra = tls_head(h)
        if ok:
            rep.add(g, h, PASS, "HTTPS reachable")
            if extra and skew is None and h == "api.github.com":
                try:
                    skew = abs(time.time() - parsedate_to_datetime(extra).timestamp())
                except Exception:  # noqa: BLE001
                    pass
        else:
            rep.add(g, h, WARN, f"unreachable ({extra})", "check VPN/corporate proxy settings (skip network checks with --offline)")
    if skew is not None:
        rep.add(g, "Clock skew", PASS if skew < 120 else WARN, f"{skew:.0f}s vs server time",
                None if skew < 120 else fix_for(os_name, "sudo sntp -sS time.apple.com", "sudo timedatectl set-ntp true") + "  (AWS signatures fail with a skewed clock)")
    rep.add(g, "Host ports", PASS, "glaring publishes no host ports (pods use internal networks)")


def run_doctor(root, env_file, pods_dir, load_pods, profile=None, offline=False):
    rep = Report()
    os_name = os_kind()
    check_system(rep, os_name)
    dbin = check_runtime(rep, os_name)
    check_images(rep, dbin, root)
    needs_bedrock, _ = check_config(rep, root, env_file, pods_dir, load_pods) or (False, set())
    check_aws(rep, os_name, profile, bool(needs_bedrock))
    if not offline:
        hosts = ["registry-1.docker.io", "api.github.com", "registry.npmjs.org"]
        if needs_bedrock:
            hosts.append("bedrock-runtime.us-west-2.amazonaws.com")
        check_network(rep, os_name, hosts)
    return rep, os_name


# ------------------------------------------------------------------ output
def render(rep, color, unicode_ok, show_fix):
    def c(code, s):
        return f"\033[{code}m{s}\033[0m" if color else s
    sym = {PASS: ("✔" if unicode_ok else "ok", "32"), WARN: ("▲" if unicode_ok else "!!", "33"),
           FAIL: ("✘" if unicode_ok else "XX", "31;1"), INFO: ("•" if unicode_ok else "-", "36")}
    out = [c("1", "glaring doctor"), ""]
    group = None
    for i in rep.items:
        if i["group"] != group:
            group = i["group"]
            out += ["", c("1;34", group)]
        s, code = sym[i["status"]]
        line = f"  {c(code, s)} {i['name']}"
        if i["detail"]:
            line += c("2", f"  {i['detail']}")
        out.append(line)
        if i["fix"] and i["status"] in (WARN, FAIL):
            out.append(f"      {c('33', '→')} {i['fix']}")
    n = rep.counts()
    out += ["", f"{c('32;1', str(n[PASS]))} passed   {c('33;1', str(n[WARN]))} warnings   {c('31;1', str(n[FAIL]))} failed"]
    if n[FAIL]:
        out.append(c("31", "Not ready: fix the failed checks above."))
    elif n[WARN]:
        out.append(c("33", "Usable, with warnings."))
    else:
        out.append(c("32", "Ready."))
    if show_fix:
        steps = []
        for i in rep.items:
            if i["fix"] and i["status"] in (WARN, FAIL) and i["fix"] not in steps:
                steps.append(i["fix"])
        out += ["", c("1", "Fix list")] + ([f"  {k}. {s}" for k, s in enumerate(steps, 1)] or ["  nothing to fix"])
    return "\n".join(out)


def to_json(rep, os_name):
    return json.dumps({"os": os_name, "ok": rep.counts()[FAIL] == 0, "summary": rep.counts(), "checks": rep.items}, indent=2)

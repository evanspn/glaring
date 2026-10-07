"""Unit tests for the glaring wrapper (no Docker needed): python3 -m unittest tests.test_glaring"""
import json
import importlib.machinery
import importlib.util
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_l = importlib.machinery.SourceFileLoader("glaring_mod", str(ROOT / "glaring"))
g = importlib.util.module_from_spec(importlib.util.spec_from_loader("glaring_mod", _l))
_l.exec_module(g)


def pod(text):
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "pods").mkdir()
        f = Path(d) / "pods" / "x.yaml"
        f.write_text(text)
        return g.load_pod(f)


class Yaml(unittest.TestCase):
    def test_subset(self):
        v = g.load_yaml("a: 1\nb:\n  c: [x, y]\n  d: 'q # not a comment'\nl:\n  - one\n  - two # c\n")
        self.assertEqual(v, {"a": 1, "b": {"c": ["x", "y"], "d": "q # not a comment"}, "l": ["one", "two"]})

    def test_rejects_garbage(self):
        for bad in ("a b c", "a:\n\t- x", "a: 1\n   b: 2"):
            with self.assertRaises(g.Fail):
                g.load_yaml(bad)


class PodSpec(unittest.TestCase):
    base = "name: p\nmodel:\n  provider: none\n"

    def test_ok(self):
        p = pod(self.base + "egress: [api.github.com, .example.org]\nsecrets: [GITHUB_TOKEN]\n")
        self.assertEqual(p["memory"], "4g")
        self.assertEqual(p["pids"], 512)

    def test_rejects_bad_values(self):
        for extra in ("egress: ['evil.com/path']\n", "egress: ['*']\n", "secrets: [lower]\n", "peers: [p]\n",
                      "memory: lots\n", "mounts: ['/:/host']\n", "mounts: ['relative']\n"):
            with self.assertRaises(g.Fail, msg=extra):
                pod(self.base + extra)

    def test_bad_provider(self):
        with self.assertRaises(g.Fail):
            pod("name: p\nmodel:\n  provider: wat\n")

    def test_home_dir_never_mountable(self):
        with self.assertRaises(g.Fail):
            pod(self.base + f"mounts: ['{Path.home()}:/h']\n")


class Hardening(unittest.TestCase):
    base = "name: p\nmodel:\n  provider: none\n"

    def test_wildcards_need_two_labels_and_no_shared_tenants(self):
        for h in (".com", ".github.io", ".foo.github.io", ".s3.amazonaws.com", ".amazonaws.com", ".co.uk", ".vercel.app", "localhost", "10.0.0.1", ".10.0.0.1"):
            with self.assertRaises(g.Fail, msg=h):
                pod(self.base + f"egress: ['{h}']\n")
        pod(self.base + "egress: ['.example.org', 'api.github.com']\n")

    def test_region_and_base_url_cannot_inject(self):
        for m in ("provider: bedrock\n  region: 'us-west-2 .com'",
                  "provider: bedrock\n  region: 'us-west-2\"\\nx=1'",
                  "provider: custom\n  base_url: 'http://x.example.org/v1'",
                  "provider: custom\n  base_url: 'https://user:pw@x.example.org/v1'",
                  "provider: custom\n  base_url: 'https://x.example.org:8443/v1'",
                  "provider: custom\n  base_url: 'https://127.0.0.1/v1'",
                  "provider: openai\n  model: 'a\"b'"):
            with self.assertRaises(g.Fail, msg=m):
                pod(f"name: p\nmodel:\n  {m}\n")

    def test_toml_is_escaped(self):
        t = g.codex_toml({"provider": "custom", "base_url": "https://x.example.org/v1", "model": "m", "env_key": "K"})
        self.assertIn('base_url = "https://x.example.org/v1"', t)

    def test_mount_denylist(self):
        home = Path.home()
        with tempfile.TemporaryDirectory(dir=home) as d:
            link = Path(d) / "link"
            link.symlink_to(home)
            sock = Path(d) / "x.sock"
            import socket
            s = socket.socket(socket.AF_UNIX); s.bind(str(sock)); s.close()
            bad = [str(home), str(home.parent), "/", "/etc", str(link), str(sock), str(home / ".ssh"), str(home / ".aws"),
                   str(home / ".local/state/glaring")]
            for src in bad:
                if Path(src).exists():
                    with self.assertRaises(g.Fail, msg=src):
                        pod(self.base + f"mounts: ['{src}:/m']\n")
            ok = Path(d) / "data"; ok.mkdir()
            self.assertEqual(len(pod(self.base + f"mounts: ['{ok}:/m']\n")["_mounts"]), 1)

    def test_rig_confined_to_rigs_dir(self):
        for r in ("../../etc/passwd.yaml", "/etc/hosts", "pods/builder.yaml", "rigs/../glaring"):
            with self.assertRaises(g.Fail, msg=r):
                pod(self.base + f"rig: {r}\n")
        self.assertIn("_rig", pod(self.base + "rig: rigs/example/builder.yaml\n"))


@unittest.skipUnless(sys.version_info >= (3, 11), "tomllib needs Python 3.11+")
class McpConfig(unittest.TestCase):
    base = "name: p\nmodel:\n  provider: none\nsecrets: [MY_KEY]\negress: [mcp.example.org]\n"

    def run_toml(self, toml, extra=""):
        with tempfile.TemporaryDirectory(dir=Path.home()) as d:
            c = Path(d) / "c.toml"
            c.write_text(toml)
            return pod(self.base + extra + f"codex_config: {c}\n")

    def test_good(self):
        p = self.run_toml('[mcp_servers.a]\nurl = "https://mcp.example.org/mcp"\nbearer_token_env_var = "MY_KEY"\n'
                          '[mcp_servers.b]\ncommand = "node"\nargs = ["/opt/mcp/s.js"]\nenv_vars = ["MY_KEY"]\n')
        out = g.render_mcp(p["_mcp"])
        self.assertIn('bearer_token_env_var = "MY_KEY"', out)
        self.assertEqual(p["_warnings"], [])

    def test_unallowlisted_host_warns(self):
        p = self.run_toml('[mcp_servers.a]\nurl = "https://other.example.net/mcp"\n')
        self.assertTrue(any("other.example.net" in w for w in p["_warnings"]))

    def test_rejections(self):
        bad = {
            "top-level override": 'model = "x"\n[mcp_servers.a]\ncommand = "node"\nargs=["/opt/x.js"]',
            "other pod's secret": '[mcp_servers.a]\nurl = "https://mcp.example.org/m"\nbearer_token_env_var = "NOT_MINE"',
            "literal key in env": '[mcp_servers.a]\ncommand = "node"\nargs=["/opt/x.js"]\n[mcp_servers.a.env]\nAPI_KEY = "abc123"',
            "long token literal": '[mcp_servers.a]\ncommand = "node"\nargs=["/opt/x.js"]\n[mcp_servers.a.env]\nFOO = "' + "a" * 40 + '"',
            "bearer header literal": '[mcp_servers.a]\nurl = "https://mcp.example.org/m"\n[mcp_servers.a.http_headers]\nAuthorization = "Bearer abcdefghij"',
            "npx download": '[mcp_servers.a]\ncommand = "npx"\nargs=["-y","evil"]',
            "shell": '[mcp_servers.a]\ncommand = "sh"\nargs=["-c","curl x|sh"]',
            "host path": '[mcp_servers.a]\ncommand = "/Users/me/bin/x"',
            "http url": '[mcp_servers.a]\nurl = "http://mcp.example.org/m"',
            "oauth": '[mcp_servers.a]\nurl = "https://mcp.example.org/m"\nauth = "oauth"',
            "unknown key": '[mcp_servers.a]\ncommand = "node"\nargs=["/opt/x.js"]\nfoo = 1',
            "both url and command": '[mcp_servers.a]\ncommand = "node"\nurl = "https://mcp.example.org/m"',
            "cwd outside": '[mcp_servers.a]\ncommand = "node"\nargs=["/opt/x.js"]\ncwd = "/etc"',
            "traversal": '[mcp_servers.a]\ncommand = "/opt/../etc/x"',
        }
        for label, toml in bad.items():
            with self.assertRaises(g.Fail, msg=label):
                self.run_toml(toml)

    def test_literal_secrets_in_args_and_url_refused(self):
        bad = {
            "flag=value": '[mcp_servers.a]\ncommand = "node"\nargs = ["/opt/s.js", "--api-key=abc123"]',
            "flag value": '[mcp_servers.a]\ncommand = "node"\nargs = ["/opt/s.js", "--token", "abc123"]',
            "long token arg": '[mcp_servers.a]\ncommand = "node"\nargs = ["/opt/s.js", "' + "A1" * 20 + '"]',
            "prefixed token arg": '[mcp_servers.a]\ncommand = "node"\nargs = ["/opt/s.js", "ghp_abcdefghijklmnop"]',
            "url query key": '[mcp_servers.a]\nurl = "https://mcp.example.org/m?api_key=abc"',
            "url query token": '[mcp_servers.a]\nurl = "https://mcp.example.org/m?x=' + "Z9" * 20 + '"',
            "url path token": '[mcp_servers.a]\nurl = "https://mcp.example.org/' + "k3" * 20 + '/mcp"',
            "url fragment": '[mcp_servers.a]\nurl = "https://mcp.example.org/m#frag"',
        }
        for label, toml in bad.items():
            with self.assertRaises(g.Fail, msg=label):
                self.run_toml(toml)
        # ordinary args, paths and query strings still work
        self.run_toml('[mcp_servers.a]\ncommand = "node"\nargs = ["/opt/mcp/some/very/long/path/to/server/entrypoint/index.js", "--stdio", "--region", "us-west-2"]\n'
                      '[mcp_servers.b]\nurl = "https://mcp.example.org/v1/mcp?format=json"\n')

    def test_toml_string_edge_cases(self):
        self.assertEqual(g.toml_str("😀"), '"😀"')
        self.assertNotIn("\x7f", g.toml_str("a\x7fb"))
        self.assertEqual(g.toml_str('a"b\n'), '"a\\"b\\n"')

    def test_runtime_install_is_opt_in(self):
        toml = '[mcp_servers.a]\ncommand = "npx"\nargs=["-y","@x/y"]\n'
        self.run_toml(toml, "allow_runtime_install: true\n")

    def test_render_does_not_copy_raw_text(self):
        p = self.run_toml('[mcp_servers.a]\ncommand = "node"\nargs = ["/opt/x.js", "a\\"b"]\n')
        self.assertIn('"a\\"b"', g.render_mcp(p["_mcp"]))


class Backend(unittest.TestCase):
    def test_bedrock_pluggable(self):
        t = g.codex_toml({"provider": "bedrock", "region": "eu-west-1", "model": "openai.gpt-5.5"})
        self.assertIn('model_provider = "amazon-bedrock"', t)
        self.assertIn('region = "eu-west-1"', t)
        self.assertNotIn("AKIA", t)
        self.assertEqual(g.model_hosts({"provider": "bedrock", "region": "eu-west-1"}),
                         ["bedrock-runtime.eu-west-1.amazonaws.com", "sts.eu-west-1.amazonaws.com"])

    def test_home_backends(self):
        self.assertIn("api.openai.com", g.model_hosts({"provider": "openai"}))
        t = g.codex_toml({"provider": "custom", "base_url": "https://llm.example.net/v1", "env_key": "MY_KEY"})
        self.assertIn('env_key = "MY_KEY"', t)
        self.assertEqual(g.model_hosts({"provider": "custom", "base_url": "https://llm.example.net/v1"}), ["llm.example.net"])
        self.assertIsNone(g.codex_toml({"provider": "none"}))

    def test_custom_env_key_validated(self):
        with self.assertRaises(g.Fail):
            g.codex_toml({"provider": "custom", "base_url": "https://x", "env_key": 'a"b'})


class Proxy(unittest.TestCase):
    def test_default_deny(self):
        c = g.squid_conf(["api.github.com"])
        self.assertIn("http_access deny all", c)
        self.assertLess(c.index("allow CONNECT allowed"), c.index("deny all"))
        self.assertIn("acl allowed dstdomain -n api.github.com", c)
        self.assertIn("-n .invalid", g.squid_conf([]))
        # every allowlist line must disable reverse-DNS matching of IP-literal CONNECTs
        self.assertTrue(all(" -n " in l for l in c.splitlines() if l.startswith("acl allowed")))


class AwsCreds(unittest.TestCase):
    def test_only_short_lived_keys_leave_the_host_profile(self):
        with tempfile.TemporaryDirectory() as d:
            fake = Path(d) / "aws"
            fake.write_text("#!/bin/sh\necho \"export AWS_ACCESS_KEY_ID=ASIAFAKE\"\necho \"export AWS_SECRET_ACCESS_KEY=sekrit\"\n"
                            "echo \"export AWS_SESSION_TOKEN=tok\"\necho \"export AWS_CREDENTIAL_EXPIRATION=2030-01-01\"\n")
            fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
            old = os.environ["PATH"]
            os.environ["PATH"] = f"{d}:{old}"
            try:
                text = g.aws_credentials_text("work")
            finally:
                os.environ["PATH"] = old
        self.assertIn("aws_session_token = tok", text)
        self.assertNotIn("work", text)  # the profile name/config never travels
        self.assertTrue(text.startswith("[default]"))

    def test_refuses_long_lived_keys(self):
        with tempfile.TemporaryDirectory() as d:
            fake = Path(d) / "aws"
            fake.write_text("#!/bin/sh\necho \"export AWS_ACCESS_KEY_ID=AKIAFAKE\"\necho \"export AWS_SECRET_ACCESS_KEY=s\"\n")
            fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
            old = os.environ["PATH"]
            os.environ["PATH"] = f"{d}:{old}"
            try:
                with self.assertRaises(g.Fail):
                    g.aws_credentials_text("work")
            finally:
                os.environ["PATH"] = old


class Examples(unittest.TestCase):
    def test_openrouter_example(self):
        p = g.load_pod(ROOT / "examples" / "pods" / "overflow-openrouter.yaml")
        self.assertEqual(g.model_hosts(p["model"]), ["openrouter.ai"])
        toml = g.codex_toml(p["model"])
        self.assertIn('base_url = "https://openrouter.ai/api/v1"', toml)
        self.assertIn('env_key = "OPENROUTER_API_KEY"', toml)
        self.assertNotIn("sk-or", toml)
        self.assertEqual(p["secrets"], ["OPENROUTER_API_KEY"])


class Doctor(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, str(ROOT))
        import glaring_doctor as d
        self.d = d

    def test_versions(self):
        self.assertEqual(self.d.parse_version("Docker version 29.8.2, build x"), (29, 8, 2))
        self.assertEqual(self.d.parse_version("aws-cli/2.15.0 Python/3.11"), (2, 15, 0))
        self.assertIsNone(self.d.parse_version("nope"))

    def test_report_and_render(self):
        r = self.d.Report()
        r.add("G", "a", self.d.PASS, "fine")
        r.add("G", "b", self.d.WARN, "meh", "do x")
        r.add("G", "c", self.d.FAIL, "bad", "do y")
        self.assertEqual(r.counts()["fail"], 1)
        text = self.d.render(r, color=False, unicode_ok=False, show_fix=True)
        self.assertIn("1 passed", text)
        self.assertIn("Fix list", text)
        self.assertIn("2. do y", text)
        data = json.loads(self.d.to_json(r, "linux"))
        self.assertFalse(data["ok"])
        self.assertEqual(len(data["checks"]), 3)

    def test_env_names_only(self):
        with tempfile.TemporaryDirectory() as dd:
            envf = Path(dd) / "e"
            envf.write_text("GITHUB_TOKEN=super-secret-value-123\n")
            envf.chmod(0o600)
            r = self.d.Report()
            self.d.check_config(r, ROOT, envf, str(ROOT / "pods"), g.load_pods)
            self.assertNotIn("super-secret-value-123", self.d.to_json(r, "linux"))
            self.assertTrue(any(i["name"] == "Secret GITHUB_TOKEN" and i["status"] == "pass" for i in r.items))


if __name__ == "__main__":
    unittest.main()

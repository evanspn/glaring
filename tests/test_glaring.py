"""Unit tests for the glaring wrapper (no Docker needed): python3 -m unittest tests.test_glaring"""
import importlib.machinery
import importlib.util
import os
import stat
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


if __name__ == "__main__":
    unittest.main()

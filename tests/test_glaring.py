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
        self.assertIn("acl allowed dstdomain api.github.com", c)
        self.assertIn(".invalid", g.squid_conf([]))


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


if __name__ == "__main__":
    unittest.main()

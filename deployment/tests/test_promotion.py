"""Exercise promotion with real Git repositories and no external services."""
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from test_autodeploy import module


class PromotionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.remote = self.root / "remote.git"
        self.checkout = self.root / "checkout"
        subprocess.run(["git", "init", "--bare", str(self.remote)], check=True, capture_output=True)
        subprocess.run(["git", "init", "-b", "main", str(self.checkout)], check=True, capture_output=True)
        self.git("config", "user.name", "CI test")
        self.git("config", "user.email", "ci@example.test")
        self.git("remote", "add", "origin", str(self.remote))
        self.base = self.commit("initial.txt", "base")
        self.git("branch", "deploy")
        self.git("push", "origin", "main", "deploy")
        self.current = self.commit("feature.txt", "working feature")
        self.git("push", "origin", "main")
        self.script = module("promote")

    def git(self, *arguments):
        return subprocess.run(["git", *arguments], cwd=self.checkout, check=True,
                              capture_output=True, text=True).stdout.strip()

    def commit(self, filename, content):
        (self.checkout / filename).write_text(content)
        self.git("add", filename)
        self.git("commit", "-m", content)
        return self.git("rev-parse", "HEAD")

    def remote_deploy(self):
        return subprocess.run(["git", "--git-dir", str(self.remote), "rev-parse", "deploy"],
                              check=True, capture_output=True, text=True).stdout.strip()

    def promote(self, ref, sha):
        original_run = subprocess.run

        def in_checkout(command, **kwargs):
            return original_run(command, cwd=self.checkout, **kwargs)

        with patch.object(self.script.subprocess, "run", side_effect=in_checkout):
            return self.script.promote(ref, sha)

    def test_main_fast_forwards_deploy_to_same_tested_commit(self):
        self.assertTrue(self.promote("refs/heads/main", self.current))
        self.assertEqual(self.remote_deploy(), self.current)

    def test_stale_main_does_not_change_deploy(self):
        self.assertFalse(self.promote("refs/heads/main", self.base))
        self.assertEqual(self.remote_deploy(), self.base)

    def test_divergent_deploy_is_not_overwritten(self):
        self.git("switch", "deploy")
        divergent = self.commit("hotfix.txt", "deployment hotfix")
        self.git("push", "origin", "deploy")
        self.git("switch", "main")
        with self.assertRaises(subprocess.CalledProcessError):
            self.promote("refs/heads/main", self.current)
        self.assertEqual(self.remote_deploy(), divergent)

    def test_dev_cannot_promote(self):
        with self.assertRaises(ValueError):
            self.promote("refs/heads/dev", self.current)
        self.assertEqual(self.remote_deploy(), self.base)

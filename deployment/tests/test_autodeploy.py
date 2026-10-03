"""Reject arbitrary SSH commands and commits outside the deployment head."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


def module(name):
    specification = importlib.util.spec_from_file_location(
        name, Path(__file__).resolve().parents[1] / f"{name}.py")
    loaded = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(loaded)
    return loaded


class DeploymentTests(unittest.TestCase):
    def test_ssh_accepts_only_full_sha_deployment(self):
        wrapper = module("deploy_ssh")
        sha = "a" * 40
        self.assertEqual(wrapper.requested_commit(f"deploy {sha}"), sha)
        for command in ("", "bash", "deploy main", "deploy " + "a" * 39,
                        f"deploy {sha}; id", f"deploy {sha}\n", "deploy ../deploy"):
            with self.subTest(command=command), self.assertRaises(ValueError):
                wrapper.requested_commit(command)

    def test_rejects_invalid_commit_before_commands(self):
        script = module("deploy")
        with patch.object(script, "run") as run:
            with self.assertRaises(ValueError):
                script.deploy("main; id")
            run.assert_not_called()

    def test_stale_workflow_does_not_redeploy_old_code(self):
        script = module("deploy")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "deployment"
            config.mkdir()
            (config / ".env").touch()
            with patch.object(script, "ROOT", root), patch.object(script, "CONFIG", config), \
                 patch.object(script, "run", side_effect=[None, None, "b" * 40 + "\n"]) as run:
                script.deploy("a" * 40)
                self.assertEqual(run.call_count, 3)
                self.assertFalse((root / "releases").exists())


if __name__ == "__main__":
    unittest.main()

"""Promote a tested main commit to deploy without rewriting branch history."""
import os
from pathlib import Path
import re
import subprocess


def git(*arguments, capture=False):
    result = subprocess.run(["git", *arguments], check=True, text=True,
                            stdout=subprocess.PIPE if capture else None)
    return result.stdout.strip() if capture else None


def promote(ref, sha):
    if ref not in ("refs/heads/main", "refs/heads/deploy"):
        raise ValueError("Only main and deploy may deploy")
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("Expected an exact commit SHA")
    git("fetch", "origin", "main", "deploy")
    branch = ref.rsplit("/", 1)[-1]
    if git("rev-parse", f"origin/{branch}", capture=True) != sha:
        print(f"A newer {branch} commit exists; skipping this deployment")
        return False
    if branch == "main":
        # A divergent deploy branch must be merged into main first, never overwritten.
        git("merge-base", "--is-ancestor", "origin/deploy", sha)
        git("push", "origin", f"{sha}:refs/heads/deploy")
    return True


if __name__ == "__main__":
    ready = promote(os.environ["GITHUB_REF"], os.environ["GITHUB_SHA"])
    with Path(os.environ["GITHUB_OUTPUT"]).open("a") as output:
        output.write(f"ready={'true' if ready else 'false'}\n")

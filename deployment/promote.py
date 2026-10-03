"""Check a tested main commit; the server records deploy only after success."""
import os
from pathlib import Path
import re
import subprocess


def git(*arguments, capture=False):
    result = subprocess.run(["git", *arguments], check=True, text=True,
                            stdout=subprocess.PIPE if capture else None)
    return result.stdout.strip() if capture else None


def promote(ref, sha):
    if ref != "refs/heads/main":
        raise ValueError("Only main may deploy")
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("Expected an exact commit SHA")
    git("fetch", "origin", "main", "deploy")
    if git("rev-parse", "origin/main", capture=True) != sha:
        print("A newer main commit exists; skipping this deployment")
        return False
    # A divergent release marker must be merged into main first, never overwritten.
    git("merge-base", "--is-ancestor", "origin/deploy", sha)
    return True


if __name__ == "__main__":
    ready = promote(os.environ["GITHUB_REF"], os.environ["GITHUB_SHA"])
    with Path(os.environ["GITHUB_OUTPUT"]).open("a") as output:
        output.write(f"ready={'true' if ready else 'false'}\n")

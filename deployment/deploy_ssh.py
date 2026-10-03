#!/usr/bin/python3
"""Forced SSH command: only a deployment of a full commit SHA is allowed."""
import os
import re
import sys


def requested_commit(command):
    match = re.fullmatch(r"deploy ([0-9a-f]{40})", command)
    if not match:
        raise ValueError("Only 'deploy <commit SHA>' is allowed")
    return match.group(1)


if __name__ == "__main__":
    try:
        sha = requested_commit(os.environ.get("SSH_ORIGINAL_COMMAND", ""))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(64)
    os.execv("/usr/local/sbin/prozapas-deploy", ["prozapas-deploy", sha])

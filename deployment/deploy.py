#!/usr/bin/python3
"""Deploy approved main, then record the healthy running commit in deploy."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile

ROOT = Path("/opt/prozapas")
CONFIG = ROOT / "deployment"
REPOSITORY = "https://github.com/dkarasevgthub/prozapas.git"
PUBLISH_REPOSITORY = "git@github.com:dkarasevgthub/prozapas.git"
PUBLISH_KEY = Path("/root/.ssh/prozapas_release_marker")
PUBLISH_HOSTS = Path("/root/.ssh/prozapas_github_known_hosts")


def log(message):
    print(message, flush=True)


def run(command, *, env=None, capture=False):
    return subprocess.run(command, check=True, env=env, text=True,
                          stdout=subprocess.PIPE if capture else None,
                          timeout=900).stdout


def compose(directory, version=None):
    command = ["docker", "compose", "--project-name", "prozapas",
               "--env-file", str(CONFIG / ".env"),
               "-f", str(directory / "compose.yaml")]
    environment = dict(os.environ)
    if version:
        environment["APP_VERSION"] = version
    return command, environment


def load_previous():
    try:
        state = json.loads((CONFIG / "deployed.json").read_text())
        sha = state["sha"]
        if not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise ValueError("Invalid deployment state")
        directory = ROOT / "releases" / sha / "deployment"
        if not (directory / "compose.yaml").is_file():
            raise ValueError("Previous release is missing")
        return directory, sha
    except FileNotFoundError:
        return CONFIG, None


def publish_release(repository, sha):
    """Publish only a release recorded locally and confirmed by the public API."""
    if json.loads((CONFIG / "deployed.json").read_text())["sha"] != sha:
        raise RuntimeError("Only the running release may be recorded in deploy")
    version = json.loads(run(["curl", "--fail", "--silent", "--show-error",
                             "https://185.196.117.2/api/v1/version"], capture=True))
    if version["version"] != sha:
        raise RuntimeError("Public API version differs from the release marker")
    git = ["git", "--git-dir", str(repository)]
    run(git + ["fetch", "--no-tags", REPOSITORY,
               "+refs/heads/deploy:refs/heads/deploy"])
    run(git + ["merge-base", "--is-ancestor", "refs/heads/deploy", sha])
    ssh_command = (f"ssh -i {PUBLISH_KEY} -o BatchMode=yes -o IdentitiesOnly=yes "
                   f"-o StrictHostKeyChecking=yes -o UserKnownHostsFile={PUBLISH_HOSTS}")
    run(["git", "-c", "core.sshCommand=" + ssh_command, "--git-dir", str(repository),
         "push", PUBLISH_REPOSITORY, f"{sha}:refs/heads/deploy"])
    log(f"Recorded running release {sha} in deploy")


def deploy(sha):
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("Expected a full hexadecimal commit SHA")
    if not (CONFIG / ".env").is_file():
        raise RuntimeError("Existing server configuration is required")
    repository = ROOT / "repository.git"
    if not repository.exists():
        run(["git", "init", "--bare", str(repository)])
    git = ["git", "--git-dir", str(repository)]
    log("Fetching approved main and the running release marker")
    run(git + ["fetch", "--no-tags", REPOSITORY,
               "+refs/heads/main:refs/heads/main", "+refs/heads/deploy:refs/heads/deploy"])
    latest = run(git + ["rev-parse", "refs/heads/main"], capture=True).strip()
    if latest != sha:
        log(f"Skipping outdated commit {sha}; main is now {latest}")
        return
    if not PUBLISH_KEY.is_file():
        raise RuntimeError("The server release marker key must be configured first")
    run(git + ["merge-base", "--is-ancestor", "refs/heads/deploy", sha])
    previous_directory, previous_sha = load_previous()
    if previous_sha == sha:
        command, environment = compose(previous_directory, previous_sha)
        run(command + ["up", "-d", "--no-build", "--wait", "--wait-timeout", "120", "api", "proxy"], env=environment)
        publish_release(repository, sha)
        log(f"Commit {sha} is already deployed and healthy")
        return
    release = ROOT / "releases" / sha
    release.mkdir(parents=True, exist_ok=True)
    archive = release / "source.tar"
    run(git + ["archive", "--format=tar", "-o", str(archive), sha])
    with tarfile.open(archive) as source:
        source.extractall(release, filter="data")
    archive.unlink()
    directory = release / "deployment"
    release_env = directory / ".env"
    if release_env.is_symlink():
        release_env.unlink()
    lines = [line for line in (CONFIG / ".env").read_text().splitlines()
             if not line.strip().startswith("APP_VERSION=")]
    release_env.write_text("\n".join(lines + [f"APP_VERSION={sha}"]) + "\n")
    release_env.chmod(0o600)
    for name in ("demo-users.json", "access.txt"):
        link = directory / name
        if (CONFIG / name).exists() and not link.exists():
            link.symlink_to(CONFIG / name)
    command, environment = compose(directory, sha)
    log(f"Building immutable images for {sha}")
    run(command + ["build", "api", "migrate"], env=environment)
    run(command + ["config", "--quiet"], env=environment)

    backup_directory = CONFIG / "backups"
    backup_directory.mkdir(mode=0o700, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup = backup_directory / f"before-{timestamp}-{sha[:12]}.dump"
    log(f"Backing up database to {backup}")
    previous_command, previous_environment = compose(previous_directory, previous_sha)
    with backup.open("xb") as stream:
        subprocess.run(previous_command + ["exec", "-T", "db", "sh", "-c",
                        'exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc'],
                       check=True, env=previous_environment, stdout=stream, timeout=120)
    if not backup.stat().st_size:
        raise RuntimeError("Database backup is empty")
    try:
        log("Applying database migrations (seed is never run on updates)")
        run(command + ["run", "--rm", "--no-deps", "migrate", "upgrade", "head"], env=environment)
        log("Starting API and proxy")
        run(command + ["up", "-d", "--no-build", "--wait", "--wait-timeout", "120", "api", "proxy"], env=environment)
        run(["curl", "--fail", "--silent", "--show-error", "--retry", "5",
             "--retry-delay", "3", "https://185.196.117.2/api/v1/health"])
    except Exception:
        log("Deployment failed; restoring previous API/proxy. Database migrations "
            f"are not reversed automatically; backup: {backup}")
        run(previous_command + ["up", "-d", "--no-build", "--wait", "--wait-timeout", "120", "api", "proxy"],
            env=previous_environment)
        raise
    current_link = ROOT / "current.next"
    current_link.unlink(missing_ok=True)
    current_link.symlink_to(release, target_is_directory=True)
    current_link.replace(ROOT / "current")
    state = CONFIG / "deployed.next.json"
    state.write_text(json.dumps({"sha": sha, "deployed_at": timestamp,
                                 "backup": str(backup)}, indent=2) + "\n")
    state.replace(CONFIG / "deployed.json")
    # If publishing fails, the healthy release stays running. A retry repairs
    # the marker without repeating migrations, builds or database backups.
    publish_release(repository, sha)
    log(f"Successfully deployed {sha}")


if __name__ == "__main__":
    import fcntl
    os.umask(0o077)
    if len(sys.argv) != 2:
        sys.exit("Usage: prozapas-deploy <commit SHA>")
    with (CONFIG / "deploy.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        deploy(sys.argv[1])

"""Run API tests in a separate prozapas_test database on this deployment."""
from pathlib import Path
import os
import subprocess

directory = Path(__file__).resolve().parent
values = dict(line.split("=", 1) for line in (directory / ".env").read_text().splitlines()
              if line and not line.startswith("#"))
env = dict(os.environ)
env["TEST_DATABASE_URL"] = (
    f"postgresql+psycopg://{values['POSTGRES_USER']}:{values['POSTGRES_PASSWORD']}"
    "@db:5432/prozapas_test"
)
command = [
    "docker", "compose", "run", "--rm", "--no-deps",
    "-e", "TEST_DATABASE_URL",
    "-v", f"{directory.parent / 'backend'}:/workspace",
    "-v", f"{directory.parent / 'docs'}:/docs:ro",
    "-w", "/workspace", "api",
    "uv", "run", "--python", "/usr/local/bin/python", "--locked", "pytest", "-q",
]
raise SystemExit(subprocess.call(command, cwd=directory, env=env))

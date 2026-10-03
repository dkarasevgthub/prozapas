"""Run UI, concurrency and serial tests against an isolated real HTTP API."""
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from urllib.parse import urlsplit

import psycopg

ROOT = Path(__file__).resolve().parents[1]


def main():
    database_url = os.environ["SYSTEM_DATABASE_URL"]
    parsed = urlsplit(database_url)
    if parsed.hostname not in ("127.0.0.1", "localhost") or parsed.path != "/prozapas_system_test":
        raise ValueError("System tests require a local dedicated prozapas_system_test database")
    plain = database_url.replace("+psycopg", "")
    with psycopg.connect(plain.rsplit("/", 1)[0] + "/postgres", autocommit=True) as db:
        db.execute("DROP DATABASE IF EXISTS prozapas_system_test WITH (FORCE)")
        db.execute("CREATE DATABASE prozapas_system_test")
    env = {**os.environ, "DATABASE_URL": database_url, "PYTHONUTF8": "1",
           "JWT_SECRET": "system-test-secret-" + "0" * 40,
           "SEED_PASSWORD": "system-test-password", "ADMIN_LOGIN": "admin",
           "ADMIN_EMAIL": "admin@prozapas.test", "APP_VERSION": "system-test"}
    subprocess.run([sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "head"],
                   cwd=ROOT / "backend", env=env, check=True)
    subprocess.run([sys.executable, str(ROOT / "backend/database/seed.py")],
                   cwd=ROOT / "backend", env=env, check=True)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    desktop_python = ROOT / "desktop/.venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    artifacts = Path(os.environ.get("SYSTEM_TEST_ARTIFACTS", ROOT / "deployment/.private/system-results"))
    artifacts.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="prozapas-system-") as temporary:
        desktop_env = {**env, "PROZAPAS_API": f"http://127.0.0.1:{port}/api/v1",
                       "PROZAPAS_ENV_FILE": str(Path(temporary) / "unused.env"),
                       "APPDATA": temporary, "XDG_CONFIG_HOME": temporary,
                       "PROZAPAS_SIMULATOR": "false", "SYSTEM_TEST_ARTIFACTS": str(artifacts),
                       "PYTHONPATH": os.pathsep.join(str(ROOT / p) for p in
                                                     ("desktop", "desktop/tests", "desktop/integration"))}
        with (artifacts / "api.log").open("w", encoding="utf-8") as log:
            server = subprocess.Popen([sys.executable, "-m", "uvicorn", "api.main:app",
                                       "--host", "127.0.0.1", "--port", str(port)],
                                      cwd=ROOT / "backend", env=env, stdout=log, stderr=log)
            try:
                deadline = time.monotonic() + 20
                while True:
                    if server.poll() is not None:
                        raise RuntimeError("Test API exited; see api.log")
                    try:
                        with urllib.request.urlopen(desktop_env["PROZAPAS_API"] + "/health", timeout=1):
                            break
                    except OSError:
                        if time.monotonic() > deadline:
                            raise
                        time.sleep(0.1)
                subprocess.run([str(desktop_python), "-m", "unittest", "discover", "-s", "integration", "-v"],
                               cwd=ROOT / "desktop", env=desktop_env, check=True, timeout=300)
            finally:
                if os.name == "nt":
                    # The venv launcher can spawn a Python child. Stop only this API's tree.
                    subprocess.run(["taskkill", "/PID", str(server.pid), "/T", "/F"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                else:
                    server.terminate()
                server.wait(timeout=10)
    with psycopg.connect(plain, autocommit=True) as db:
        with (artifacts / "schema.txt").open("w") as report:
            report.write(str(db.execute("SELECT version_num FROM alembic_version").fetchone()))


if __name__ == "__main__":
    main()

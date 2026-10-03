"""Restore a real pg_dump into a separate DB and compare every public table."""
import json
import os
from pathlib import Path
import subprocess
from urllib.parse import urlsplit

import psycopg
from psycopg import sql


def snapshot(connection):
    result = {}
    tables = connection.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename")
    for (name,) in tables.fetchall():
        query = sql.SQL("SELECT count(*), md5(string_agg(payload::text, E'\\n' ORDER BY payload::text)) "
                        "FROM (SELECT to_jsonb(t) AS payload FROM {} t) records").format(sql.Identifier(name))
        result[name] = connection.execute(query).fetchone()
    return result


def main():
    source_url = os.environ["SYSTEM_DATABASE_URL"].replace("+psycopg", "")
    parsed = urlsplit(source_url)
    if parsed.hostname not in ("127.0.0.1", "localhost") or parsed.path != "/prozapas_system_test":
        raise ValueError("Only the local system test database may be backed up")
    container = os.environ["PG_TEST_CONTAINER"]
    base_url = source_url.rsplit("/", 1)[0]
    backup = subprocess.run(["docker", "exec", container, "pg_dump", "-U", parsed.username,
                             "-d", "prozapas_system_test", "-Fc"], capture_output=True, check=True).stdout
    if not backup:
        raise AssertionError("Empty PostgreSQL backup")
    with psycopg.connect(base_url + "/postgres", autocommit=True) as admin:
        admin.execute("DROP DATABASE IF EXISTS prozapas_restore_test WITH (FORCE)")
        admin.execute("CREATE DATABASE prozapas_restore_test")
    try:
        subprocess.run(["docker", "exec", "-i", container, "pg_restore", "-U", parsed.username,
                        "-d", "prozapas_restore_test", "--exit-on-error"], input=backup, check=True)
        with psycopg.connect(source_url) as source, psycopg.connect(base_url + "/prozapas_restore_test") as restored:
            original, copy = snapshot(source), snapshot(restored)
            if original != copy:
                raise AssertionError("Restored data/schema differ from the original test database")
            report = {"ok": True, "backup_bytes": len(backup), "tables": original}
            artifacts = Path(os.environ["SYSTEM_TEST_ARTIFACTS"])
            (artifacts / "backup-restore.json").write_text(json.dumps(report, indent=2))
            print(f"Backup and restore verified: {len(original)} tables, all row counts and data hashes match")
    finally:
        with psycopg.connect(base_url + "/postgres", autocommit=True) as admin:
            admin.execute("DROP DATABASE prozapas_restore_test WITH (FORCE)")


if __name__ == "__main__":
    main()

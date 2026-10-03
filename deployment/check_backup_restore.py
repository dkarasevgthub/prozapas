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
    sequences = {}
    for (name,) in connection.execute("SELECT sequencename FROM pg_sequences WHERE schemaname = 'public' ORDER BY sequencename").fetchall():
        sequences[name] = connection.execute(sql.SQL("SELECT last_value, is_called FROM {}").format(sql.Identifier(name))).fetchone()
    columns = connection.execute("""
        SELECT t.relname, a.attname, format_type(a.atttypid, a.atttypmod), a.attnotnull,
               pg_get_expr(d.adbin, d.adrelid)
        FROM pg_class t JOIN pg_namespace n ON n.oid = t.relnamespace
        JOIN pg_attribute a ON a.attrelid = t.oid
        LEFT JOIN pg_attrdef d ON d.adrelid = t.oid AND d.adnum = a.attnum
        WHERE n.nspname = 'public' AND t.relkind = 'r' AND a.attnum > 0 AND NOT a.attisdropped
        ORDER BY t.relname, a.attnum
    """).fetchall()
    constraints = connection.execute("""
        SELECT t.relname, c.conname, pg_get_constraintdef(c.oid)
        FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace
        WHERE n.nspname = 'public' ORDER BY t.relname, c.conname
    """).fetchall()
    indexes = connection.execute("SELECT tablename, indexname, indexdef FROM pg_indexes WHERE schemaname = 'public' ORDER BY tablename, indexname").fetchall()
    return {"tables": result, "sequences": sequences, "columns": columns,
            "constraints": constraints, "indexes": indexes}


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
            report = {"ok": True, "backup_bytes": len(backup), **original}
            artifacts = Path(os.environ["SYSTEM_TEST_ARTIFACTS"])
            (artifacts / "backup-restore.json").write_text(json.dumps(report, indent=2))
            print(f"Backup and restore verified: {len(original['tables'])} tables; data, sequences, columns, constraints and indexes match")
    finally:
        with psycopg.connect(base_url + "/postgres", autocommit=True) as admin:
            admin.execute("DROP DATABASE prozapas_restore_test WITH (FORCE)")


if __name__ == "__main__":
    main()

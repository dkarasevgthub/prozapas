"""Create server-only secrets once. Existing configuration is preserved."""
from pathlib import Path
import os
import secrets

target = Path(__file__).resolve().parent / ".env"
if not target.exists():
    values = {
        "POSTGRES_USER": "prozapas_user",
        "POSTGRES_DB": "prozapas_db",
        "POSTGRES_PASSWORD": secrets.token_urlsafe(32),
        "JWT_SECRET": secrets.token_urlsafe(48),
        "SEED_PASSWORD": secrets.token_urlsafe(24),
        "API_HOST": "185.196.117.2",
        "APP_VERSION": "1.0.0",
    }
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write("".join(f"{key}={value}\n" for key, value in values.items()))
    print("Server configuration created; secrets were not printed.")
else:
    print("Existing server configuration preserved.")

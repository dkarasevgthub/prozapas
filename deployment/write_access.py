"""Save demo login details to a private file without logging passwords."""
from pathlib import Path
import json
import os

directory = Path(__file__).resolve().parent
values = dict(line.split("=", 1) for line in (directory / ".env").read_text().splitlines()
              if line and not line.startswith("#"))
target = directory / "access.txt"
accounts = json.loads((directory / "demo-users.json").read_text(encoding="utf-8"))
administrator = next(a for a in accounts if a["role"] == "admin")
descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
    stream.write(f"API=https://{values['API_HOST']}/api/v1\n")
    stream.write(f"LOGIN={administrator['login']}\n")
    stream.write(f"PASSWORD={administrator['password']}\n")
    for account in accounts:
        stream.write(f"\n{account['full_name']}\n")
        stream.write(f"login={account['login']} password={account['password']} ")
        stream.write(f"role={account['role']} warehouse={account['warehouse']}\n")
print("Demo access details saved to access.txt.")

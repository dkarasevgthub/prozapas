"""Deployment-specific demo accounts; historical users use soft deletion.

Run through the deployment's seed service. The original seed remains unchanged
for the isolated API test database. Account passwords stay in an ignored file.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, InvalidHashError
from sqlalchemy import create_engine, select, update
from sqlalchemy.orm import Session

import seed
from models import AuditLog, RefreshToken, UserAccount, UserStatus, Warehouse


def main():
    accounts = json.loads(Path("/app/demo-users.json").read_text(encoding="utf-8"))
    logins = [a["login"] for a in accounts]
    if len(accounts) != 3 or len(set(logins)) != 3:
        raise ValueError("Expected exactly three distinct demo accounts")
    if not any(a["role"] == "admin" for a in accounts):
        raise ValueError("At least one administrator is required")
    seeded_logins = {seed.ADMIN_LOGIN, *(row[6] for row in seed.USERS)}
    now = datetime.now(timezone.utc)
    hasher = PasswordHasher()
    engine = create_engine(os.environ["DATABASE_URL"])
    with Session(engine) as session, session.begin():
        roles = seed.seed_roles(session)
        warehouses = seed.seed_warehouses(session)
        seed.seed_stock(session, seed.seed_catalog(session), warehouses)
        current = list(session.scalars(select(UserAccount).with_for_update(of=UserAccount)))
        active = {u.login: u for u in current if u.deleted_at is None}
        selected = {}
        changed = []
        revoked = []
        for account in accounts:
            login = account["login"]
            user = active.get(login)
            if user is None:
                user = UserAccount(
                    login=login, email=f"{login}@prozapas.ru",
                    full_name=account["full_name"],
                    password_hash=hasher.hash(account["password"]),
                    role_id=roles[account["role"]].id,
                    warehouse_id=warehouses[account["warehouse"]].id,
                    status=UserStatus.ACTIVE,
                )
                session.add(user)
                changed.append((user, "created"))
            else:
                try:
                    password_matches = hasher.verify(user.password_hash, account["password"])
                except (VerificationError, InvalidHashError):
                    password_matches = False
                if not password_matches:
                    user.password_hash = hasher.hash(account["password"])
                    revoked.append(user.id)
                    changed.append((user, "password-changed"))
                user.full_name = account["full_name"]
                user.role_id = roles[account["role"]].id
                user.warehouse_id = warehouses[account["warehouse"]].id
                user.status = UserStatus.ACTIVE
            selected[login] = user
        session.flush()
        actor = next(selected[a["login"]] for a in accounts if a["role"] == "admin")
        deleted = []
        for user in current:
            if user.login in seeded_logins and user.login not in selected and user.deleted_at is None:
                user.deleted_at = now
                revoked.append(user.id)
                deleted.append(user.id)
                changed.append((user, "deleted"))
        if revoked:
            session.execute(update(RefreshToken).where(
                RefreshToken.user_id.in_(revoked), RefreshToken.revoked_at.is_(None)
            ).values(revoked_at=now))
        for warehouse in warehouses.values():
            if warehouse.responsible_user_id in deleted:
                warehouse.responsible_user_id = None
        for account in accounts:
            warehouses[account["warehouse"]].responsible_user_id = selected[account["login"]].id
        for user, action in changed:
            session.add(AuditLog(entity="user_account", entity_id=user.id,
                                 action=action, user_id=actor.id,
                                 after={"login": user.login}))
        print(f"Custom demo users: {len(selected)}; previous seed users deleted: {len(deleted)}")
    engine.dispose()


if __name__ == "__main__":
    main()

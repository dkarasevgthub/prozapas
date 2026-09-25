"""Наполнение базы для тестовых прогонов.

Не миграция: миграции меняют структуру, этот скрипт — данные. Идемпотентен —
повторный запуск ничего не дублирует, поэтому его безопасно звать из `make up`.

    docker compose run --rm seed

**Это стенд, не рабочий контур.** Все двадцать одна учётная запись получают один
и тот же пароль из `SEED_PASSWORD`, склады и номенклатура выдуманы. Когда дойдёт
до настоящей установки, отсюда останутся только роли и права — они не данные, а
часть схемы: `role_id` у пользователя NOT NULL, а без прав не открывается ни
один раздел.

Заказов не создаёт намеренно. Заказ тянет за собой резерв, движения и статусы —
это три транзакции из docs/api.md §7. Повторить их здесь значит завести вторую
копию бизнес-логики, которая разойдётся с первой. Демо-заказы создаются через
API: так они проходят настоящий код.
"""
import os
import sys
from datetime import date, datetime

from argon2 import PasswordHasher
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from models import (
    CatalogItem,
    Role,
    RolePermission,
    Section,
    StockBalance,
    UserAccount,
    UserStatus,
    Warehouse,
)

DATABASE_URL = os.environ.get("DATABASE_URL")
ADMIN_LOGIN = os.environ.get("ADMIN_LOGIN", "admin")
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@prozapas.ru")
SEED_PASSWORD = os.environ.get("SEED_PASSWORD")

EMAIL_DOMAIN = "stalker.ru"

ROLES = [
    ("manager", "Менеджер"),
    ("stockman", "Кладовщик"),
    ("admin", "Администратор"),
]

#: Права по умолчанию. (раздел, роль) -> (просмотр, изменение)
PERMISSIONS = {
    Section.ORDERS:    {"manager": (True, True),   "stockman": (True, False),  "admin": (True, True)},
    Section.SHIPPING:  {"manager": (True, False),  "stockman": (True, True),   "admin": (True, True)},
    Section.RECEIVING: {"manager": (True, False),  "stockman": (True, True),   "admin": (True, True)},
    Section.CATALOG:   {"manager": (True, True),   "stockman": (True, False),  "admin": (True, True)},
    Section.STOCK:     {"manager": (True, False),  "stockman": (True, True),   "admin": (True, True)},
    Section.USERS:     {"manager": (False, False), "stockman": (False, False), "admin": (True, True)},
}

# (код, название, чей)
WAREHOUSES = [
    ("128", "Склад №1", "ООО «Сталкер Групп»"),
    ("129", "Склад №3", "ООО «Сталкер Групп»"),
    ("130", "Склад №4", "ООО «Сталкер Групп»"),
    ("131", "Центральный склад", "ООО «Сталкер Групп»"),
]

# (артикул, код 1С, наименование, единица, вес единицы)
CATALOG = [
    ("100421", "ТМЦ-00421", "Болт М8×40 ГОСТ 7798", "шт.", 0.02),
    ("100433", "ТМЦ-00433", "Гайка М8 ГОСТ 5915", "шт.", 0.01),
    ("201187", "ТМЦ-01187", "Подшипник 6205-2RS", "шт.", 0.08),
    ("201204", "ТМЦ-01204", "Ремень приводной А-1250", "шт.", 0.45),
    ("300087", "ТМЦ-00087", "Редуктор РЦД-350 в сборе", "шт.", 42.0),
    ("300091", "ТМЦ-00091", "Насос центробежный НЦ-40", "шт.", 18.5),
    ("100458", "ТМЦ-00458", "Лист стальной 2×1250×2500", "лист", 72.5),
    ("100477", "ТМЦ-00477", "Уголок стальной 50×50×5", "шт.", 4.5),
    ("100512", "ТМЦ-00512", "Труба стальная 32×2", "м", 1.6),
    ("300098", "ТМЦ-00098", "Электродвигатель АИР90", "шт.", 24.0),
    ("201340", "ТМЦ-01340", "Ремень клиновой Б-1400", "шт.", 0.35),
    ("100655", "ТМЦ-00655", "Шайба М8 ГОСТ 6402", "шт.", 0.005),
    ("100701", "ТМЦ-00701", "Винт М6×20 ГОСТ 17475", "шт.", 0.008),
    ("201412", "ТМЦ-01412", "Подшипник 6208-2RS", "шт.", 0.15),
    ("300133", "ТМЦ-00133", "Насос шестерённый НШ-10", "шт.", 6.2),
    ("100812", "ТМЦ-00812", "Швеллер 10П ГОСТ 8240", "м", 8.5),
    ("300159", "ТМЦ-00159", "Редуктор червячный РЧУ-125", "шт.", 18.7),
    ("100933", "ТМЦ-00933", "Гайка М10 ГОСТ 5915", "шт.", 0.015),
    ("201055", "ТМЦ-01055", "Муфта соединительная МУВП-40", "шт.", 2.1),
    ("100950", "ТМЦ-00950", "Шпонка 8×7×40", "шт.", 0.03),
    ("201600", "ТМЦ-01600", "Цепь приводная ПР-19.05", "м", 1.9),
    ("300210", "ТМЦ-00210", "Компрессор поршневой К-25", "шт.", 55.0),
]

#: артикул -> {код склада: количество}. По коду, а не по названию: название
#: переименуют, и сверка развалится молча.
#: Нулевых строк не заводим: отсутствие строки в stock_balance и означает, что
#: товара на складе нет.
STOCK = {
    "100421": {"128": 800, "130": 150},
    "100433": {"129": 600},
    "201187": {"128": 12, "130": 30},
    "201204": {"129": 25, "130": 8},
    "300087": {"131": 3},
    "300091": {"131": 11},
    "100458": {"129": 6, "131": 10},
    "100477": {"128": 40, "131": 60},
    "100512": {"129": 120.5, "131": 200},
    "300098": {"128": 3, "130": 2, "131": 6},
    "201340": {"128": 54, "130": 12},
    "100655": {"129": 3200, "131": 1800},
    "100701": {"128": 900, "130": 400},
    "201412": {"129": 18, "130": 10},
    "300133": {"128": 4, "130": 3, "131": 5},
    "100812": {"129": 40.75, "131": 60},
    "300159": {"128": 2, "130": 1, "131": 3},
    "100933": {"129": 1500, "131": 900},
    "201055": {"128": 6, "130": 4, "131": 8},
    "100950": {"129": 220, "131": 150},
    "201600": {"129": 30.25, "130": 20},
    "300210": {"128": 1, "131": 2},
}

# (ФИО, роль, код склада, должность, статус, телефон, логин, приём, последний вход)
USERS = [
    ("Кузнецов Игорь Александрович", "admin", "131", "Начальник склада",
     "active", "+7 916 204-11-87", "i.kuznetsov", date(2019, 3, 12), datetime(2026, 7, 24, 8, 42)),
    ("Соколов Пётр Николаевич", "stockman", "128", "Кладовщик",
     "active", "+7 903 551-22-14", "p.sokolov", date(2020, 6, 5), datetime(2026, 7, 24, 7, 58)),
    ("Морозова Елена Викторовна", "manager", "129", "Менеджер по логистике",
     "active", "+7 925 118-77-03", "e.morozova", date(2021, 9, 18), datetime(2026, 7, 23, 17, 20)),
    ("Титов Роман Андреевич", "stockman", "130", "Кладовщик",
     "active", "+7 909 330-45-61", "r.titov", date(2022, 2, 2), datetime(2026, 7, 24, 9, 5)),
    ("Волкова Мария Сергеевна", "manager", "131", "Менеджер по закупкам",
     "active", "+7 917 442-90-08", "m.volkova", date(2020, 11, 27), datetime(2026, 7, 24, 8, 15)),
    ("Новиков Дмитрий Олегович", "stockman", "128", "Комплектовщик",
     "active", "+7 985 220-14-30", "d.novikov", date(2023, 4, 14), datetime(2026, 7, 23, 16, 40)),
    ("Козлова Анна Павловна", "manager", "129", "Менеджер заказов",
     "active", "+7 926 771-08-52", "a.kozlova", date(2021, 7, 9), datetime(2026, 7, 24, 8, 33)),
    ("Лебедев Сергей Иванович", "stockman", "130", "Кладовщик",
     "blocked", "+7 903 118-64-27", "s.lebedev", date(2019, 10, 21), datetime(2026, 5, 11, 14, 2)),
    ("Егорова Ольга Дмитриевна", "manager", "128", "Менеджер по логистике",
     "active", "+7 916 905-33-71", "o.egorova", date(2022, 1, 30), datetime(2026, 7, 24, 9, 12)),
    ("Павлов Артём Викторович", "admin", "129", "Администратор системы",
     "active", "+7 925 604-77-19", "a.pavlov", date(2018, 8, 15), datetime(2026, 7, 24, 8, 1)),
    ("Семёнов Никита Андреевич", "stockman", "131", "Приёмщик",
     "active", "+7 909 442-11-06", "n.semenov", date(2024, 3, 3), datetime(2026, 7, 23, 18, 47)),
    ("Фёдорова Татьяна Юрьевна", "manager", "130", "Менеджер по закупкам",
     "active", "+7 917 330-88-52", "t.fedorova", date(2021, 5, 22), datetime(2026, 7, 24, 7, 44)),
    ("Михайлов Владимир Петрович", "stockman", "128", "Кладовщик",
     "active", "+7 985 771-20-63", "v.mihaylov", date(2020, 12, 19), datetime(2026, 7, 24, 8, 50)),
    ("Виноградова Ирина Олеговна", "manager", "129", "Менеджер заказов",
     "active", "+7 926 118-45-90", "i.vinogradova", date(2023, 6, 7), datetime(2026, 7, 23, 15, 33)),
    ("Богданов Алексей Сергеевич", "stockman", "130", "Комплектовщик",
     "blocked", "+7 903 905-14-08", "a.bogdanov", date(2019, 9, 11), datetime(2026, 3, 2, 10, 21)),
    ("Орлова Наталья Игоревна", "manager", "131", "Менеджер по логистике",
     "active", "+7 916 442-77-31", "n.orlova", date(2022, 2, 26), datetime(2026, 7, 24, 9, 0)),
    ("Киселёв Максим Дмитриевич", "stockman", "128", "Приёмщик",
     "active", "+7 925 220-63-14", "m.kiselev", date(2023, 11, 13), datetime(2026, 7, 24, 8, 22)),
    ("Макарова Светлана Андреевна", "admin", "131", "Администратор",
     "active", "+7 917 604-11-90", "s.makarova", date(2019, 4, 4), datetime(2026, 7, 24, 8, 37)),
    ("Никитин Павел Романович", "stockman", "129", "Кладовщик",
     "active", "+7 909 771-33-27", "p.nikitin", date(2022, 7, 28), datetime(2026, 7, 23, 17, 5)),
    ("Захарова Юлия Владимировна", "manager", "130", "Менеджер по закупкам",
     "active", "+7 985 330-45-08", "y.zaharova", date(2024, 1, 16), datetime(2026, 7, 24, 8, 19)),
]

#: Ответственный за склад — по логину, а не по ФИО: логин уникален.
RESPONSIBLE = {"128": "p.sokolov", "129": "e.morozova",
               "130": "r.titov", "131": "m.volkova"}


def min_qty(qty: float) -> float:
    """Точка дозаказа: за редким оборудованием следят пристальнее, чем за метизами."""
    if qty < 100:
        return 5
    if qty < 1000:
        return 50
    return 500


def seed_roles(session: Session) -> dict[str, Role]:
    roles = {r.code: r for r in session.scalars(select(Role))}
    for code, label in ROLES:
        if code not in roles:
            roles[code] = Role(code=code, label=label)
            session.add(roles[code])
    session.flush()

    existing = {(rp.role_id, rp.section)
                for rp in session.scalars(select(RolePermission))}
    added = 0
    for section, by_role in PERMISSIONS.items():
        for code, (can_view, can_edit) in by_role.items():
            role = roles[code]
            if (role.id, section.value) in existing:
                continue
            session.add(RolePermission(role_id=role.id, section=section.value,
                                       can_view=can_view, can_edit=can_edit))
            added += 1
    print(f"роли: {len(roles)}, права: добавлено {added}")
    return roles


def seed_warehouses(session: Session) -> dict[str, Warehouse]:
    known = {w.code: w for w in session.scalars(select(Warehouse))}
    added = 0
    for code, name, owner in WAREHOUSES:
        if code not in known:
            known[code] = Warehouse(code=code, name=name, owner=owner)
            session.add(known[code])
            added += 1
    session.flush()
    print(f"склады: добавлено {added}, всего {len(known)}")
    return known


def seed_users(session: Session, roles: dict, warehouses: dict,
               hasher: PasswordHasher) -> None:
    unknown = {code for _f, _r, code, *_ in USERS} - warehouses.keys()
    if unknown:
        print(f"нет складов с кодами {sorted(unknown)} — сверьте с WAREHOUSES",
              file=sys.stderr)
        raise SystemExit(2)

    people = {u.login: u for u in session.scalars(select(UserAccount))}
    added = 0
    # Администратор идёт первым: он единственный, чей логин задаётся окружением.
    if ADMIN_LOGIN not in people:
        first = min(warehouses.values(), key=lambda w: w.code)
        admin = UserAccount(
            full_name="Администратор системы", login=ADMIN_LOGIN,
            email=ADMIN_EMAIL,
            # Настоящий argon2id, а не заглушка: иначе войти нельзя.
            password_hash=hasher.hash(SEED_PASSWORD),
            role_id=roles["admin"].id, warehouse_id=first.id,
            status=UserStatus.ACTIVE)
        session.add(admin)
        people[ADMIN_LOGIN] = admin
        added += 1

    for (full_name, role, wh_code, position, status,
         phone, login, hired, last_login) in USERS:
        if login in people:
            continue
        user = UserAccount(
            full_name=full_name, login=login, email=f"{login}@{EMAIL_DOMAIN}",
            password_hash=hasher.hash(SEED_PASSWORD),
            role_id=roles[role].id, warehouse_id=warehouses[wh_code].id,
            position=position, phone=phone,
            status=UserStatus.ACTIVE if status == "active" else UserStatus.BLOCKED,
            hire_date=hired, last_login_at=last_login)
        session.add(user)
        people[login] = user
        added += 1
    session.flush()
    print(f"сотрудники: добавлено {added}, всего {len(people)}")

    wired = 0
    for wh_code, login in RESPONSIBLE.items():
        warehouse = warehouses[wh_code]
        person = people.get(login)
        if person is None or warehouse.responsible_user_id:
            continue
        warehouse.responsible_user_id = person.id
        wired += 1
    print(f"ответственные за склады: проставлено {wired}")


def seed_catalog(session: Session) -> dict[str, CatalogItem]:
    items = {c.article: c for c in session.scalars(select(CatalogItem))}
    added = 0
    for article, code1c, name, unit, weight in CATALOG:
        if article in items:
            continue
        items[article] = CatalogItem(article=article, code1c=code1c, name=name,
                                     unit=unit, unit_weight=weight)
        session.add(items[article])
        added += 1
    session.flush()
    print(f"номенклатура: добавлено {added}, всего {len(items)}")
    return items


def seed_stock(session: Session, items: dict, warehouses: dict) -> None:
    unknown = {code for by_wh in STOCK.values() for code in by_wh} - warehouses.keys()
    if unknown:
        print(f"нет складов с кодами {sorted(unknown)} — сверьте с WAREHOUSES",
              file=sys.stderr)
        raise SystemExit(2)

    existing = {(b.item_id, b.warehouse_id)
                for b in session.scalars(select(StockBalance))}
    added = 0
    for article, by_warehouse in STOCK.items():
        item = items[article]
        for wh_code, qty in by_warehouse.items():
            warehouse = warehouses[wh_code]
            if (item.id, warehouse.id) in existing:
                continue
            # Резерв нулевой: он следствие принятого заказа, а заказов здесь нет.
            session.add(StockBalance(item_id=item.id, warehouse_id=warehouse.id,
                                     qty=qty, reserved=0, min_qty=min_qty(qty)))
            added += 1
    print(f"остатки: добавлено строк {added}")


def seed(session: Session) -> None:
    hasher = PasswordHasher()
    roles = seed_roles(session)
    warehouses = seed_warehouses(session)
    seed_users(session, roles, warehouses, hasher)
    items = seed_catalog(session)
    seed_stock(session, items, warehouses)


def main() -> int:
    if not DATABASE_URL:
        print("нет DATABASE_URL", file=sys.stderr)
        return 2
    if not SEED_PASSWORD:
        print("нет SEED_PASSWORD — учётные записи не завести", file=sys.stderr)
        return 2

    engine = create_engine(DATABASE_URL)
    with Session(engine) as session, session.begin():
        seed(session)
    print(f"готово · пароль у всех один, из SEED_PASSWORD · вход: {ADMIN_LOGIN}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

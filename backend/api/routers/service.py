"""Служебные: живость, версия, справочники, сводка."""
from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import func, select, text

from database.models import (
    Order,
    OrderStatus,
    OrderStatusEvent,
    Role,
    RolePermission,
    Section,
    UserAccount,
    Warehouse,
)

from ..deps import SessionDep, UserDep
from ..errors import Unavailable
from ..schemas.common import Permission, Person, User, UserBrief, WarehouseBrief
from ..schemas.service import Bootstrap, Dashboard, Health
from ..schemas.service import Role as RoleOut
from ..schemas.service import StatusEvent, Version
from ..services import visibility
from ..settings import settings

router = APIRouter(tags=["Служебные"])

#: Сколько последних событий показывает главная. Панель рисует шесть.
EVENTS_LIMIT = 8


@router.get("/health", response_model=Health, summary="Живость сервиса")
def health(session: SessionDep) -> Health:
    """Делает SELECT 1. Для оркестратора и индикатора связи в приложении."""
    try:
        session.execute(text("SELECT 1"))
    except Exception as exc:                      # noqa: BLE001
        raise Unavailable("База не отвечает") from exc
    return Health(status="ok")


@router.get("/version", response_model=Version, summary="Версия сборки и схемы")
def version(session: SessionDep) -> Version:
    try:
        revision = session.scalar(text("SELECT version_num FROM alembic_version"))
    except Exception:                             # noqa: BLE001
        revision = None
    return Version(version=settings().version, schema_revision=revision)


@router.get("/bootstrap", response_model=Bootstrap,
            summary="Справочники одним запросом")
def bootstrap(session: SessionDep, user: UserDep) -> Bootstrap:
    warehouses = list(session.scalars(
        select(Warehouse)
        .where(Warehouse.deleted_at.is_(None), Warehouse.is_active.is_(True))
        .order_by(Warehouse.code)))
    roles = list(session.scalars(select(Role).order_by(Role.id)))
    role_of = {r.id: r.code for r in roles}
    perms = list(session.scalars(select(RolePermission)))
    people = list(session.scalars(
        select(UserAccount)
        .where(UserAccount.deleted_at.is_(None))
        .order_by(UserAccount.full_name)))

    return Bootstrap(
        warehouses=[WarehouseBrief.of(w) for w in warehouses],
        roles=[RoleOut(code=r.code, label=r.label) for r in roles],
        sections=[s.value for s in Section],
        permissions=[Permission(role=role_of.get(p.role_id, ""), section=p.section,
                                can_view=p.can_view, can_edit=p.can_edit)
                     for p in perms],
        user=User.of(user),
        warehouse=WarehouseBrief.of(user.warehouse),
        # Имя целиком, не сокращённое: это выпадающий список, там читают глазами.
        users=[Person(id=p.id, name=p.full_name, warehouse_id=p.warehouse_id)
               for p in people],
    )


@router.get("/dashboard", response_model=Dashboard, summary="Сводка для главной")
def dashboard(session: SessionDep, user: UserDep) -> Dashboard:
    """Счётчики по своему складу.

    «К отгрузке» — входящие в работе, то есть у нас заказали и мы собираем.
    «К приёмке» — исходящие в пути: мы заказали, отправитель уже отгрузил.
    """
    wh = user.warehouse_id
    in_work = (OrderStatus.CREATED, OrderStatus.PROCESSING)

    def count(*conditions) -> int:
        return session.scalar(
            select(func.count()).select_from(Order).where(*conditions)) or 0

    outgoing_in_work = count(visibility.outgoing(wh), Order.status.in_(in_work))
    incoming_in_work = count(visibility.incoming(wh), Order.status.in_(in_work))
    to_receive = count(visibility.outgoing(wh),
                       Order.status == OrderStatus.SHIPPED)

    events = list(session.execute(
        select(OrderStatusEvent, Order.number, UserAccount)
        .join(Order, Order.id == OrderStatusEvent.order_id)
        .outerjoin(UserAccount, UserAccount.id == OrderStatusEvent.user_id)
        .where(visibility.mine(wh))
        .order_by(OrderStatusEvent.occurred_at.desc())
        .limit(EVENTS_LIMIT)))

    return Dashboard(
        in_work=outgoing_in_work + incoming_in_work,
        outgoing_in_work=outgoing_in_work,
        incoming_in_work=incoming_in_work,
        # Входящие в работе и есть очередь сборки — отдельного счёта не нужно.
        to_ship=incoming_in_work,
        to_receive=to_receive,
        events=[StatusEvent(order_id=e.order_id, number=number, status=e.status,
                            reason=e.reason, user=UserBrief.of(person),
                            occurred_at=e.occurred_at)
                for e, number, person in events],
    )

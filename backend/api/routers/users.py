"""Пользователи и права — 11 операций, раздел 6.7 api.md."""
from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Query

from database.models import Section, UserAccount

from ..deps import PageParams, SessionDep, require
from ..schemas.common import Page, Permission, User
from ..schemas.users import (AuditEntry, PasswordBody, PermissionUpdate,
                             UserCreate, UserUpdate)
from ..services import users

router = APIRouter(tags=["Пользователи"])


@router.get("/users", response_model=Page[User],
            summary="Список пользователей")
def list_users(session: SessionDep,
               actor: Annotated[UserAccount, require(Section.USERS)],
               page: PageParams,
               q: Annotated[str | None, Query(max_length=100)] = None,
               role: Annotated[str | None, Query(max_length=50)] = None,
               status: Annotated[Literal["active", "blocked"] | None,
                                 Query()] = None,
               warehouse_id: Annotated[int | None, Query()] = None,
               ) -> Page[User]:
    limit, offset = page
    rows, total = users.list_users(session, q=q, role=role, status=status,
                                   warehouse_id=warehouse_id,
                                   limit=limit, offset=offset)
    return Page(items=[User.of(u) for u in rows], total=total,
                limit=limit, offset=offset)


@router.post("/users", response_model=User, status_code=201,
             summary="Завести пользователя")
def create_user(session: SessionDep,
                actor: Annotated[UserAccount,
                                 require(Section.USERS, edit=True)],
                body: UserCreate) -> User:
    user = users.create(session, actor, full_name=body.full_name,
                        login=body.login, email=body.email,
                        password=body.password, role_code=body.role,
                        warehouse_id=body.warehouse_id,
                        position=body.position, phone=body.phone,
                        hire_date=body.hire_date)
    return User.of(user)


@router.get("/users/{id}", response_model=User,
            summary="Карточка пользователя")
def get_user(session: SessionDep,
             actor: Annotated[UserAccount, require(Section.USERS)],
             id: int) -> User:
    return User.of(users.get_user(session, id))


@router.patch("/users/{id}", response_model=User,
              summary="Изменить пользователя")
def update_user(session: SessionDep,
                actor: Annotated[UserAccount,
                                 require(Section.USERS, edit=True)],
                id: int, body: UserUpdate) -> User:
    changes = body.model_dump(exclude_unset=True)
    return User.of(users.update(session, actor, id, changes))


@router.delete("/users/{id}", status_code=204, response_model=None,
               summary="Удалить пользователя")
def delete_user(session: SessionDep,
                actor: Annotated[UserAccount,
                                 require(Section.USERS, edit=True)],
                id: int) -> None:
    users.delete(session, actor, id)


@router.post("/users/{id}/block", status_code=204, response_model=None,
             summary="Заблокировать")
def block_user(session: SessionDep,
               actor: Annotated[UserAccount,
                                require(Section.USERS, edit=True)],
               id: int) -> None:
    users.block(session, actor, id)


@router.post("/users/{id}/unblock", status_code=204, response_model=None,
             summary="Разблокировать")
def unblock_user(session: SessionDep,
                 actor: Annotated[UserAccount,
                                  require(Section.USERS, edit=True)],
                 id: int) -> None:
    users.unblock(session, actor, id)


@router.post("/users/{id}/password", status_code=204, response_model=None,
             summary="Сменить пароль")
def set_password(session: SessionDep,
                 actor: Annotated[UserAccount,
                                  require(Section.USERS, edit=True)],
                 id: int, body: PasswordBody) -> None:
    users.set_password(session, actor, id, body.password)


@router.get("/users/{id}/activity", response_model=Page[AuditEntry],
            summary="История действий")
def activity(session: SessionDep,
             actor: Annotated[UserAccount, require(Section.USERS)],
             id: int, page: PageParams) -> Page[AuditEntry]:
    limit, offset = page
    rows, total = users.activity(session, id, limit, offset)
    return Page(items=[AuditEntry.of(e) for e in rows], total=total,
                limit=limit, offset=offset)


@router.get("/permissions", response_model=list[Permission],
            summary="Матрица прав")
def get_permissions(session: SessionDep,
                    actor: Annotated[UserAccount, require(Section.USERS)]
                    ) -> list[Permission]:
    return [Permission(role=code, section=p.section, can_view=p.can_view,
                       can_edit=p.can_edit)
            for p, code in users.matrix(session)]


@router.put("/permissions", response_model=list[Permission],
            summary="Изменить матрицу прав")
def put_permissions(session: SessionDep,
                    actor: Annotated[UserAccount,
                                     require(Section.USERS, edit=True)],
                    body: list[PermissionUpdate]) -> list[Permission]:
    users.matrix_put(session, actor, body)
    return [Permission(role=code, section=p.section, can_view=p.can_view,
                       can_edit=p.can_edit)
            for p, code in users.matrix(session)]

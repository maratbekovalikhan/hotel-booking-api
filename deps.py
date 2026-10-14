from datetime import date
from pathlib import Path

from fastapi import Depends, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

import models
from constants import (
    BOOKING_STATUS_LABELS, HOTEL_STATUS_LABELS, HOUSEKEEPING_LABELS,
    PAYMENT_STATUS_LABELS, ROLE_CUSTOMER, ROLE_LABELS, ROLE_MANAGER,
    ROLE_RECEPTIONIST, ROLE_SUPERADMIN,
)
from database import get_db

BASE_DIR = Path(__file__).resolve().parent

# Контакты для футера (демо-данные — замените на свои)
CONTACTS = {
    "phone": "+7 (700) 000-00-00",
    "email": "info@hotelbooking.example",
    "address": "г. Астана, пр. Достык, 12 (главный офис)",
    "app_store": "#",
    "google_play": "#",
}


def money(value) -> str:
    return f"{int(value):,}".replace(",", " ")


templates = Jinja2Templates(directory=BASE_DIR / "templates")
templates.env.filters["money"] = money
templates.env.globals.update(
    ROLE_LABELS=ROLE_LABELS,
    BOOKING_STATUS_LABELS=BOOKING_STATUS_LABELS,
    HOUSEKEEPING_LABELS=HOUSEKEEPING_LABELS,
    HOTEL_STATUS_LABELS=HOTEL_STATUS_LABELS,
    PAYMENT_STATUS_LABELS=PAYMENT_STATUS_LABELS,
)


class NotAuthenticated(Exception):
    pass


class Forbidden(Exception):
    pass


def flash(request: Request, message: str, category: str = "success") -> None:
    request.session["flash"] = {"category": category, "message": message}


def safe_redirect(path: str | None, default: str = "/") -> str:
    """Разрешаем только локальные пути (защита от open redirect)."""
    if path and path.startswith("/") and not path.startswith("//") and "\\" not in path:
        return path
    return default


def current_path(request: Request) -> str:
    q = request.url.query
    return request.url.path + (f"?{q}" if q else "")


def parse_int(value: str | None) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except ValueError:
        return None


def parse_date(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


# ---------- пользователь и права ----------

def get_current_user(request: Request, db: Session = Depends(get_db)) -> models.User | None:
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    user = db.get(models.User, user_id)
    if user is None or user.is_blocked:
        request.session.clear()  # заблокированного пользователя сразу «выкидываем»
        return None
    return user


def require_user(user: models.User | None = Depends(get_current_user)) -> models.User:
    if user is None:
        raise NotAuthenticated()
    return user


def require_roles(*roles: str):
    def checker(user: models.User = Depends(require_user)) -> models.User:
        if user.role not in roles:
            raise Forbidden()
        return user

    return checker


customer_only = require_roles(ROLE_CUSTOMER)
reception_only = require_roles(ROLE_RECEPTIONIST, ROLE_SUPERADMIN)
manager_only = require_roles(ROLE_MANAGER, ROLE_SUPERADMIN)
admin_only = require_roles(ROLE_SUPERADMIN)


def has_perm(user: models.User, perm: str) -> bool:
    """Суперадмин может всё; менеджер — только если ему выдано право."""
    if user.role == ROLE_SUPERADMIN:
        return True
    return user.role == ROLE_MANAGER and bool(getattr(user, perm, False))


# ---------- рендер ----------

def render(
    request: Request,
    name: str,
    db: Session,
    user: models.User | None,
    context: dict | None = None,
    active: str = "",
    status_code: int = 200,
):
    ctx = {
        "user": user,
        "active": active,
        "flash": request.session.pop("flash", None),
        "contacts": CONTACTS,
        "footer_hotels": db.scalars(
            select(models.Hotel).where(models.Hotel.status == "approved").order_by(models.Hotel.id)
        ).all(),
        "today": date.today().isoformat(),
        "today_date": date.today(),
        "back": current_path(request),
    }
    ctx.update(context or {})
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)

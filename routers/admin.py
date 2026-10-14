"""Суперадминистратор: сотрудники, права менеджеров, финансы и комиссия."""
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import models
from constants import ROLE_MANAGER, ROLE_RECEPTIONIST, STAFF_ROLES
from database import get_db
from deps import admin_only, flash, render
from services import create_user, get_setting, set_setting, validate_new_user

router = APIRouter(prefix="/admin")


def back(path: str) -> RedirectResponse:
    return RedirectResponse(path, status_code=303)


@router.get("", response_class=HTMLResponse)
def dashboard(
    request: Request, db: Session = Depends(get_db), user: models.User = Depends(admin_only),
):
    def total(*conds):
        return db.scalar(select(func.coalesce(func.sum(models.Payment.amount), 0)).where(*conds)) or 0

    gross = total()
    refunded = total(models.Payment.status == "refunded")
    net = gross - refunded
    percent = float(get_setting(db, "commission_percent", "10"))
    users_by_role = dict(db.execute(select(models.User.role, func.count(models.User.id)).group_by(models.User.role)).all())
    bookings_by_status = dict(db.execute(select(models.Booking.status, func.count(models.Booking.id)).group_by(models.Booking.status)).all())
    return render(
        request, "admin_dashboard.html", db, user, active="admin",
        context={"gross": gross, "refunded": refunded, "net": net, "percent": percent,
                 "commission": round(net * percent / 100), "users_by_role": users_by_role,
                 "bookings_by_status": bookings_by_status, "sub": "dashboard"},
    )


@router.post("/settings/commission")
def set_commission(
    request: Request, percent: float = Form(...),
    db: Session = Depends(get_db), user: models.User = Depends(admin_only),
):
    if not 0 <= percent <= 100:
        flash(request, "Комиссия должна быть от 0 до 100%.", "error")
    else:
        set_setting(db, "commission_percent", str(percent))
        flash(request, f"Комиссия платформы: {percent}%.")
    return back("/admin")


@router.get("/staff", response_class=HTMLResponse)
def staff(
    request: Request, db: Session = Depends(get_db), user: models.User = Depends(admin_only),
):
    staff_list = db.scalars(
        select(models.User).where(models.User.role.in_(STAFF_ROLES))
        .order_by(models.User.role, models.User.id)
    ).all()
    return render(request, "staff.html", db, user, active="admin",
                  context={"staff_list": staff_list, "is_admin": True, "sub": "staff"})


@router.post("/staff")
def create_staff(
    request: Request,
    full_name: str = Form(""), email: str = Form(""), password: str = Form(""), role: str = Form(...),
    db: Session = Depends(get_db), user: models.User = Depends(admin_only),
):
    full_name, email = full_name.strip(), email.strip().lower()
    error = None if role in STAFF_ROLES else "Недопустимая роль."
    error = error or validate_new_user(db, full_name, email, password)
    if error:
        flash(request, error, "error")
    else:
        extra = {"can_create_receptionists": False, "can_add_hotels": True} if role == ROLE_MANAGER else {}
        create_user(db, full_name, email, password, role=role, **extra)
        flash(request, f"Сотрудник {full_name} создан.")
    return back("/admin/staff")


@router.post("/staff/{user_id}/permissions")
def set_permissions(
    request: Request, user_id: int,
    can_create_receptionists: str | None = Form(None), can_add_hotels: str | None = Form(None),
    db: Session = Depends(get_db), user: models.User = Depends(admin_only),
):
    target = db.get(models.User, user_id)
    if target is None or target.role != ROLE_MANAGER:
        flash(request, "Права можно настраивать только у менеджеров платформы.", "error")
    else:
        target.can_create_receptionists = can_create_receptionists is not None
        target.can_add_hotels = can_add_hotels is not None
        db.commit()
        flash(request, f"Права менеджера {target.full_name} обновлены.")
    return back("/admin/staff")


@router.post("/staff/{user_id}/delete")
def delete_staff(
    request: Request, user_id: int,
    db: Session = Depends(get_db), user: models.User = Depends(admin_only),
):
    target = db.get(models.User, user_id)
    if target is None or target.role not in STAFF_ROLES:
        flash(request, "Удалять можно только менеджеров и сотрудников ресепшена.", "error")
    else:
        name = target.full_name
        db.delete(target)
        db.commit()
        flash(request, f"Сотрудник {name} удалён.")
    return back("/admin/staff")

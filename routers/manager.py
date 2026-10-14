"""Менеджер платформы: модерация отелей, споры, возвраты, блокировки пользователей и номеров."""
import math
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, joinedload

import models
from constants import ROLE_CUSTOMER, ROLE_RECEPTIONIST, ROLE_SUPERADMIN, ROLE_LABELS
from database import get_db
from deps import flash, has_perm, manager_only, parse_int, render
from services import create_user, validate_new_user

router = APIRouter(prefix="/manager")

USERS_PAGE = 30


def back(path: str) -> RedirectResponse:
    return RedirectResponse(path, status_code=303)


def now() -> datetime:
    return models.utcnow()


# ---------- обзор ----------

@router.get("", response_class=HTMLResponse)
def dashboard(
    request: Request, db: Session = Depends(get_db), user: models.User = Depends(manager_only),
):
    refunds_due = db.scalar(
        select(func.count(models.Payment.id)).join(models.Payment.booking)
        .where(models.Payment.status == "paid", models.Booking.status == "cancelled")
    )
    stats = {
        "pending_hotels": db.scalar(select(func.count(models.Hotel.id)).where(models.Hotel.status == "pending")),
        "open_disputes": db.scalar(select(func.count(models.Dispute.id)).where(models.Dispute.status == "open")),
        "refunds_due": refunds_due,
        "blocked_users": db.scalar(select(func.count(models.User.id)).where(models.User.is_blocked.is_(True))),
        "active_blocks": db.scalar(select(func.count(models.RoomBlock.id)).where(models.RoomBlock.date_to > date.today())),
    }
    return render(request, "manager_dashboard.html", db, user, active="manager",
                  context={"stats": stats, "sub": "dashboard"})


# ---------- отели: модерация, добавление отелей и номеров ----------

@router.get("/hotels", response_class=HTMLResponse)
def hotels(
    request: Request, db: Session = Depends(get_db), user: models.User = Depends(manager_only),
):
    all_hotels = db.scalars(select(models.Hotel).order_by(models.Hotel.status.desc(), models.Hotel.id)).all()
    room_counts = dict(db.execute(select(models.Room.hotel_id, func.count(models.Room.id)).group_by(models.Room.hotel_id)).all())
    return render(
        request, "manager_hotels.html", db, user, active="manager",
        context={"hotels": all_hotels, "room_counts": room_counts,
                 "pending": [h for h in all_hotels if h.status == "pending"],
                 "can_add": has_perm(user, "can_add_hotels"), "sub": "hotels"},
    )


@router.post("/hotels/new")
def create_hotel(
    request: Request,
    name: str = Form(""), city: str = Form(""), address: str = Form(""), stars: int = Form(3),
    description: str = Form(""), amenities: str = Form(""), phone: str = Form(""), email: str = Form(""),
    db: Session = Depends(get_db), user: models.User = Depends(manager_only),
):
    if not has_perm(user, "can_add_hotels"):
        flash(request, "Суперадминистратор ограничил вам добавление отелей.", "error")
        return back("/manager/hotels")
    name, city, address = name.strip(), city.strip(), address.strip()
    description, amenities, phone, email = description.strip(), amenities.strip(), phone.strip(), email.strip()
    if not (name and city and address and description and phone and email):
        flash(request, "Заполните все обязательные поля.", "error")
    elif not 1 <= stars <= 5:
        flash(request, "Количество звёзд — от 1 до 5.", "error")
    elif db.scalar(select(models.Hotel.id).where(models.Hotel.name == name)):
        flash(request, "Отель с таким названием уже существует.", "error")
    else:
        # Суперадмин публикует сразу; отель менеджера уходит на модерацию другому менеджеру/суперадмину
        status = "approved" if user.role == ROLE_SUPERADMIN else "pending"
        db.add(models.Hotel(
            name=name, city=city, address=address, stars=stars, description=description,
            amenities=amenities, phone=phone, email=email, status=status, submitted_by_id=user.id,
        ))
        db.commit()
        flash(request, "Отель добавлен." if status == "approved"
              else "Отель отправлен на модерацию. Одобрить его должен другой менеджер или суперадмин.")
    return back("/manager/hotels")


@router.post("/hotels/{hotel_id}/moderate")
def moderate_hotel(
    request: Request, hotel_id: int, action: str = Form(...), note: str = Form(""),
    db: Session = Depends(get_db), user: models.User = Depends(manager_only),
):
    hotel = db.get(models.Hotel, hotel_id)
    note = note.strip()
    if hotel is None or hotel.status != "pending":
        flash(request, "Отель не найден или уже обработан.", "error")
    elif hotel.submitted_by_id == user.id and user.role != ROLE_SUPERADMIN:
        flash(request, "Нельзя модерировать собственный отель — это должен сделать другой менеджер.", "error")
    elif action == "reject" and len(note) < 3:
        flash(request, "Укажите причину отклонения.", "error")
    elif action in ("approve", "reject"):
        hotel.status = "approved" if action == "approve" else "rejected"
        hotel.moderation_note = note or None
        db.commit()
        flash(request, f"Отель «{hotel.name}» {'одобрен' if action == 'approve' else 'отклонён'}.")
    return back("/manager/hotels")


@router.post("/rooms/new")
def create_room(
    request: Request,
    hotel_id: int = Form(...), number: int = Form(...), name: str = Form(""),
    capacity: int = Form(...), rooms_count: int = Form(...), price_per_night: int = Form(...),
    description: str = Form(""),
    db: Session = Depends(get_db), user: models.User = Depends(manager_only),
):
    if not has_perm(user, "can_add_hotels"):
        flash(request, "Суперадминистратор ограничил вам добавление номеров.", "error")
        return back("/manager/hotels")
    name, description = name.strip(), description.strip()
    if db.get(models.Hotel, hotel_id) is None:
        flash(request, "Отель не найден.", "error")
    elif not name or not description:
        flash(request, "Заполните тип и описание номера.", "error")
    elif number <= 0 or capacity <= 0 or rooms_count <= 0 or price_per_night <= 0:
        flash(request, "Номер, вместимость, число комнат и цена должны быть положительными.", "error")
    elif db.scalar(select(models.Room.id).where(models.Room.hotel_id == hotel_id, models.Room.number == number)):
        flash(request, f"Номер {number} в этом отеле уже существует.", "error")
    else:
        db.add(models.Room(hotel_id=hotel_id, number=number, name=name, capacity=capacity,
                           rooms_count=rooms_count, price_per_night=price_per_night, description=description))
        db.commit()
        flash(request, f"Номер {number} добавлен.")
    return back("/manager/hotels")


# ---------- споры и возвраты ----------

@router.get("/disputes", response_class=HTMLResponse)
def disputes(
    request: Request, db: Session = Depends(get_db), user: models.User = Depends(manager_only),
):
    booking_load = joinedload(models.Dispute.booking)
    open_disputes = db.scalars(
        select(models.Dispute).where(models.Dispute.status == "open")
        .options(joinedload(models.Dispute.user),
                 booking_load.joinedload(models.Booking.room).joinedload(models.Room.hotel),
                 booking_load.joinedload(models.Booking.payment))
        .order_by(models.Dispute.created_at)
    ).all()
    resolved = db.scalars(
        select(models.Dispute).where(models.Dispute.status == "resolved")
        .options(joinedload(models.Dispute.user)).order_by(models.Dispute.resolved_at.desc()).limit(20)
    ).all()
    refunds_due = db.scalars(
        select(models.Payment).join(models.Payment.booking)
        .where(models.Payment.status == "paid", models.Booking.status == "cancelled")
        .options(joinedload(models.Payment.booking).joinedload(models.Booking.user),
                 joinedload(models.Payment.booking).joinedload(models.Booking.room).joinedload(models.Room.hotel))
        .order_by(models.Payment.paid_at)
    ).all()
    return render(request, "manager_disputes.html", db, user, active="manager",
                  context={"open_disputes": open_disputes, "resolved": resolved,
                           "refunds_due": refunds_due, "sub": "disputes"})


@router.post("/disputes/{dispute_id}/resolve")
def resolve_dispute(
    request: Request, dispute_id: int, resolution: str = Form(""),
    db: Session = Depends(get_db), user: models.User = Depends(manager_only),
):
    dispute = db.get(models.Dispute, dispute_id)
    resolution = resolution.strip()
    if dispute is None or dispute.status != "open":
        flash(request, "Спор не найден или уже закрыт.", "error")
    elif len(resolution) < 5:
        flash(request, "Опишите решение (минимум 5 символов).", "error")
    else:
        dispute.status = "resolved"
        dispute.resolution = resolution
        dispute.resolved_by = user.full_name
        dispute.resolved_at = now()
        db.commit()
        flash(request, "Спор закрыт.")
    return back("/manager/disputes")


@router.post("/payments/{payment_id}/refund")
def refund(
    request: Request, payment_id: int, reason: str = Form(""),
    db: Session = Depends(get_db), user: models.User = Depends(manager_only),
):
    payment = db.get(models.Payment, payment_id)
    reason = reason.strip()
    if payment is None or payment.status != "paid":
        flash(request, "Платёж не найден или уже возвращён.", "error")
    elif len(reason) < 3:
        flash(request, "Укажите причину возврата.", "error")
    else:
        # Ручной возврат (демо): реальный возврат через шлюз подключается здесь
        payment.status = "refunded"
        payment.refunded_at = now()
        payment.refunded_by = user.full_name
        payment.refund_reason = reason
        if payment.booking.status == "paid":  # ещё не заселены — бронь снимаем
            payment.booking.status = "cancelled"
        db.commit()
        flash(request, f"Возврат {payment.amount} ₸ оформлен.")
    return back("/manager/disputes")


# ---------- пользователи ----------

@router.get("/users", response_class=HTMLResponse)
def users(
    request: Request, q: str = "", page: str | None = None,
    db: Session = Depends(get_db), user: models.User = Depends(manager_only),
):
    conds = []
    if user.role != ROLE_SUPERADMIN:
        conds.append(models.User.role == ROLE_CUSTOMER)  # менеджер видит только клиентов
    else:
        conds.append(models.User.role != ROLE_SUPERADMIN)
    q = q.strip()
    if q:
        like = f"%{q.lower()}%"
        conds.append(or_(func.lower(models.User.email).like(like), func.lower(models.User.full_name).like(like)))
    total = db.scalar(select(func.count(models.User.id)).where(*conds))
    pages = max(1, math.ceil(total / USERS_PAGE))
    current = min(max(1, parse_int(page) or 1), pages)
    items = db.scalars(
        select(models.User).where(*conds)
        .order_by(models.User.is_blocked.desc(), models.User.id.desc())
        .limit(USERS_PAGE).offset((current - 1) * USERS_PAGE)
    ).all()
    return render(request, "manager_users.html", db, user, active="manager",
                  context={"items": items, "q": q, "total": total, "page": current,
                           "pages": pages, "sub": "users"})


def _can_moderate_user(actor: models.User, target: models.User | None) -> str | None:
    if target is None:
        return "Пользователь не найден."
    if target.id == actor.id:
        return "Нельзя заблокировать самого себя."
    if target.role == ROLE_SUPERADMIN:
        return "Суперадминистратора заблокировать нельзя."
    if actor.role != ROLE_SUPERADMIN and target.role != ROLE_CUSTOMER:
        return "Менеджер может блокировать только клиентов."
    return None


@router.post("/users/{user_id}/block")
def block_user(
    request: Request, user_id: int, reason: str = Form(""),
    db: Session = Depends(get_db), actor: models.User = Depends(manager_only),
):
    target = db.get(models.User, user_id)
    reason = reason.strip()
    error = _can_moderate_user(actor, target)
    if not error and len(reason) < 3:
        error = "Укажите причину блокировки."
    if error:
        flash(request, error, "error")
    else:
        target.is_blocked = True
        target.blocked_reason = reason
        db.commit()
        flash(request, f"{target.full_name} заблокирован.")
    return back("/manager/users")


@router.post("/users/{user_id}/unblock")
def unblock_user(
    request: Request, user_id: int,
    db: Session = Depends(get_db), actor: models.User = Depends(manager_only),
):
    target = db.get(models.User, user_id)
    error = _can_moderate_user(actor, target)
    if error:
        flash(request, error, "error")
    else:
        target.is_blocked = False
        target.blocked_reason = None
        db.commit()
        flash(request, f"{target.full_name} разблокирован.")
    return back("/manager/users")


# ---------- временная блокировка номеров ----------

@router.get("/blocks", response_class=HTMLResponse)
def blocks(
    request: Request, db: Session = Depends(get_db), user: models.User = Depends(manager_only),
):
    items = db.scalars(
        select(models.RoomBlock).where(models.RoomBlock.date_to > date.today())
        .options(joinedload(models.RoomBlock.room).joinedload(models.Room.hotel))
        .order_by(models.RoomBlock.date_from)
    ).all()
    hotels = db.scalars(select(models.Hotel).where(models.Hotel.status == "approved").order_by(models.Hotel.id)).all()
    return render(request, "manager_blocks.html", db, user, active="manager",
                  context={"items": items, "hotels": hotels, "sub": "blocks"})


@router.post("/blocks")
def create_block(
    request: Request,
    hotel_id: int = Form(...), room_number: int = Form(...),
    date_from: date = Form(...), date_to: date = Form(...), reason: str = Form(""),
    db: Session = Depends(get_db), user: models.User = Depends(manager_only),
):
    reason = reason.strip()
    room = db.scalar(select(models.Room).where(models.Room.hotel_id == hotel_id, models.Room.number == room_number))
    end_exclusive = date_to + timedelta(days=1)  # «по» вводится включительно
    if room is None:
        flash(request, "Такого номера в этом отеле нет.", "error")
    elif date_from < date.today():
        flash(request, "Дата начала не может быть в прошлом.", "error")
    elif date_to < date_from:
        flash(request, "Дата окончания не может быть раньше даты начала.", "error")
    elif len(reason) < 3:
        flash(request, "Укажите причину (техобслуживание, уборка и т. д.).", "error")
    else:
        conflict = db.scalar(
            select(models.Booking).where(
                models.Booking.room_id == room.id,
                models.Booking.status.in_(("pending", "paid", "checked_in")),
                models.Booking.check_in < end_exclusive, models.Booking.check_out > date_from,
            )
        )
        if conflict:
            flash(request, f"В эти даты у номера есть бронь ({conflict.check_in:%d.%m.%Y}–{conflict.check_out:%d.%m.%Y}). "
                           "Сначала решите вопрос с ней.", "error")
        else:
            db.add(models.RoomBlock(room_id=room.id, date_from=date_from, date_to=end_exclusive,
                                    reason=reason, created_by=user.full_name))
            db.commit()
            flash(request, f"Номер {room.number} заблокирован на выбранный период.")
    return back("/manager/blocks")


@router.post("/blocks/{block_id}/delete")
def delete_block(
    request: Request, block_id: int,
    db: Session = Depends(get_db), user: models.User = Depends(manager_only),
):
    block = db.get(models.RoomBlock, block_id)
    if block:
        db.delete(block)
        db.commit()
        flash(request, "Блокировка снята.")
    return back("/manager/blocks")


# ---------- создание ресепшена (если суперадмин дал право) ----------

@router.get("/staff", response_class=HTMLResponse)
def staff(
    request: Request, db: Session = Depends(get_db), user: models.User = Depends(manager_only),
):
    if not has_perm(user, "can_create_receptionists"):
        flash(request, "У вас нет права создавать сотрудников ресепшена.", "error")
        return back("/manager")
    staff_list = db.scalars(select(models.User).where(models.User.role == ROLE_RECEPTIONIST).order_by(models.User.id)).all()
    return render(request, "staff.html", db, user, active="manager",
                  context={"staff_list": staff_list, "is_admin": False, "sub": "staff"})


@router.post("/staff")
def create_receptionist(
    request: Request, full_name: str = Form(""), email: str = Form(""), password: str = Form(""),
    db: Session = Depends(get_db), user: models.User = Depends(manager_only),
):
    if not has_perm(user, "can_create_receptionists"):
        flash(request, "У вас нет права создавать сотрудников ресепшена.", "error")
        return back("/manager")
    full_name, email = full_name.strip(), email.strip().lower()
    error = validate_new_user(db, full_name, email, password)
    if error:
        flash(request, error, "error")
    else:
        create_user(db, full_name, email, password, role=ROLE_RECEPTIONIST)
        flash(request, f"Сотрудник ресепшена {full_name} создан.")
    return back("/manager/staff")

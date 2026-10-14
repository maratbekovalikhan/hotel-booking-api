"""Клиент: бронирование, оплата (демо), отмена, отзывы, споры, личный кабинет.
Все действия — только над СВОИМИ бронями."""
from datetime import date

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

import models
from constants import ROLE_CUSTOMER
from database import get_db
from deps import customer_only, flash, render, require_user, safe_redirect
from security import hash_password, verify_password
from services import validate_booking

router = APIRouter()


def own_booking(db: Session, booking_id: int, user: models.User) -> models.Booking | None:
    booking = db.get(models.Booking, booking_id)
    return booking if booking and booking.user_id == user.id else None


@router.get("/account", response_class=HTMLResponse)
def account(
    request: Request, db: Session = Depends(get_db), user: models.User = Depends(require_user),
):
    bookings = []
    if user.role == ROLE_CUSTOMER:
        bookings = db.scalars(
            select(models.Booking).where(models.Booking.user_id == user.id)
            .options(
                joinedload(models.Booking.room).joinedload(models.Room.hotel),
                joinedload(models.Booking.payment), joinedload(models.Booking.review),
            )
            .order_by(models.Booking.check_in.desc())
        ).unique().all()
    return render(request, "account.html", db, user, active="account",
                  context={"bookings": bookings})


@router.post("/book")
def book(
    request: Request,
    room_id: int = Form(...), check_in: date = Form(...), check_out: date = Form(...),
    redirect_to: str = Form("/rooms"),
    db: Session = Depends(get_db), user: models.User = Depends(customer_only),
):
    room, error = validate_booking(db, room_id, check_in, check_out)
    if error:
        flash(request, error, "error")
        return RedirectResponse(safe_redirect(redirect_to, "/rooms"), status_code=303)
    db.add(models.Booking(user_id=user.id, room_id=room.id, check_in=check_in,
                          check_out=check_out, status="pending"))
    db.commit()
    flash(request, f"Номер {room.number} ({room.hotel.name}) забронирован. Оплатите бронь, чтобы подтвердить её.")
    return RedirectResponse("/account", status_code=303)


@router.post("/bookings/{booking_id}/pay")
def pay(
    request: Request, booking_id: int,
    db: Session = Depends(get_db), user: models.User = Depends(customer_only),
):
    booking = own_booking(db, booking_id, user)
    if booking is None:
        flash(request, "Бронь не найдена.", "error")
    elif not booking.can_pay:
        flash(request, "Эта бронь не требует оплаты.", "error")
    else:
        # ДЕМО-оплата: реального платёжного шлюза нет. Подключение шлюза — на этом месте.
        db.add(models.Payment(booking_id=booking.id, amount=booking.total_price, status="paid"))
        booking.status = "paid"
        db.commit()
        flash(request, f"Оплата прошла успешно (демо): {booking.total_price} ₸.")
    return RedirectResponse("/account", status_code=303)


@router.post("/bookings/{booking_id}/cancel")
def cancel(
    request: Request, booking_id: int,
    db: Session = Depends(get_db), user: models.User = Depends(customer_only),
):
    booking = own_booking(db, booking_id, user)
    if booking is None:
        flash(request, "Бронь не найдена.", "error")
    elif not booking.can_cancel:
        flash(request, "Эту бронь уже нельзя отменить.", "error")
    else:
        paid = booking.payment is not None and booking.payment.status == "paid"
        booking.status = "cancelled"
        db.commit()
        flash(request, "Бронь отменена. Возврат оплаты оформит менеджер платформы." if paid
              else "Бронь отменена.")
    return RedirectResponse("/account", status_code=303)


@router.post("/bookings/{booking_id}/review")
def review(
    request: Request, booking_id: int,
    rating: int = Form(...), text: str = Form(""),
    db: Session = Depends(get_db), user: models.User = Depends(customer_only),
):
    booking = own_booking(db, booking_id, user)
    text = text.strip()
    if booking is None:
        flash(request, "Бронь не найдена.", "error")
    elif not booking.can_review:
        flash(request, "Отзыв можно оставить после выезда, один раз на бронь.", "error")
    elif not 1 <= rating <= 5:
        flash(request, "Оценка должна быть от 1 до 5.", "error")
    elif len(text) < 5 or len(text) > 1000:
        flash(request, "Отзыв: от 5 до 1000 символов.", "error")
    else:
        db.add(models.Review(booking_id=booking.id, user_id=user.id,
                             hotel_id=booking.room.hotel_id, rating=rating, text=text))
        db.commit()
        flash(request, "Спасибо за отзыв!")
    return RedirectResponse("/account", status_code=303)


@router.post("/bookings/{booking_id}/dispute")
def dispute(
    request: Request, booking_id: int, message: str = Form(""),
    db: Session = Depends(get_db), user: models.User = Depends(customer_only),
):
    booking = own_booking(db, booking_id, user)
    message = message.strip()
    if booking is None:
        flash(request, "Бронь не найдена.", "error")
    elif booking.has_open_dispute:
        flash(request, "По этой брони уже есть открытый спор.", "error")
    elif len(message) < 10 or len(message) > 2000:
        flash(request, "Опишите проблему подробнее (10–2000 символов).", "error")
    else:
        db.add(models.Dispute(booking_id=booking.id, user_id=user.id, message=message))
        db.commit()
        flash(request, "Обращение отправлено менеджеру платформы.")
    return RedirectResponse("/account", status_code=303)


@router.post("/account/profile")
def update_profile(
    request: Request, full_name: str = Form(""),
    db: Session = Depends(get_db), user: models.User = Depends(require_user),
):
    full_name = full_name.strip()
    if len(full_name) < 2:
        flash(request, "Введите имя (минимум 2 символа).", "error")
    else:
        user.full_name = full_name
        db.commit()
        flash(request, "Профиль обновлён.")
    return RedirectResponse("/account", status_code=303)


@router.post("/account/password")
def change_password(
    request: Request,
    old_password: str = Form(""), new_password: str = Form(""), new_password2: str = Form(""),
    db: Session = Depends(get_db), user: models.User = Depends(require_user),
):
    if not verify_password(old_password, user.password_hash):
        flash(request, "Текущий пароль указан неверно.", "error")
    elif len(new_password) < 6:
        flash(request, "Новый пароль должен быть не короче 6 символов.", "error")
    elif new_password != new_password2:
        flash(request, "Новые пароли не совпадают.", "error")
    else:
        user.password_hash = hash_password(new_password)
        db.commit()
        flash(request, "Пароль изменён.")
    return RedirectResponse("/account", status_code=303)

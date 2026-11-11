"""Клиент: бронирование, оплата (демо), отмена, отзывы, споры, личный кабинет.
Все действия — только над СВОИМИ бронями."""
from datetime import date

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

import models
from constants import ROLE_CUSTOMER
from database import get_db
from deps import customer_only, flash, render, require_user, safe_redirect
from security import ahash_password, averify_password
from services import validate_booking

router = APIRouter()

# Всё, что нужно шаблонам и свойствам Booking, грузим сразу (в async lazy-загрузка недоступна)
BOOKING_LOAD = (
    joinedload(models.Booking.room).joinedload(models.Room.hotel),
    joinedload(models.Booking.payment),
    joinedload(models.Booking.review),
    selectinload(models.Booking.disputes),
)


async def own_booking(db: AsyncSession, booking_id: int, user: models.User) -> models.Booking | None:
    return await db.scalar(
        select(models.Booking)
        .where(models.Booking.id == booking_id, models.Booking.user_id == user.id)
        .options(*BOOKING_LOAD)
    )


@router.get("/account", response_class=HTMLResponse)
async def account(
    request: Request, db: AsyncSession = Depends(get_db), user: models.User = Depends(require_user),
):
    bookings = []
    if user.role == ROLE_CUSTOMER:
        bookings = (await db.scalars(
            select(models.Booking).where(models.Booking.user_id == user.id)
            .options(*BOOKING_LOAD)
            .order_by(models.Booking.check_in.desc())
        )).all()
    return await render(request, "account.html", db, user, active="account",
                        context={"bookings": bookings})


@router.post("/book")
async def book(
    request: Request,
    room_id: int = Form(...), check_in: date = Form(...), check_out: date = Form(...),
    redirect_to: str = Form("/rooms"),
    db: AsyncSession = Depends(get_db), user: models.User = Depends(customer_only),
):
    target = safe_redirect(redirect_to, "/rooms")
    room, error = await validate_booking(db, room_id, check_in, check_out)
    if error:
        flash(request, error, "error")
        return RedirectResponse(target, status_code=303)

    db.add(models.Booking(user_id=user.id, room_id=room.id, check_in=check_in,
                          check_out=check_out, status="pending"))
    try:
        await db.commit()
    except IntegrityError:
        # Сработало ограничение БД (bookings_no_overlap): номер заняли одновременным запросом
        await db.rollback()
        flash(request, f"Номер {room.number} только что забронировал другой гость. Выберите другие даты или номер.", "error")
        return RedirectResponse(target, status_code=303)

    flash(request, f"Номер {room.number} ({room.hotel.name}) забронирован. Оплатите бронь, чтобы подтвердить её.")
    return RedirectResponse("/account", status_code=303)


@router.post("/bookings/{booking_id}/pay")
async def pay(
    request: Request, booking_id: int,
    db: AsyncSession = Depends(get_db), user: models.User = Depends(customer_only),
):
    booking = await own_booking(db, booking_id, user)
    if booking is None:
        flash(request, "Бронь не найдена.", "error")
    elif not booking.can_pay:
        flash(request, "Эта бронь не требует оплаты.", "error")
    else:
        # ДЕМО-оплата: реального платёжного шлюза нет. Подключение шлюза — на этом месте.
        amount = booking.total_price
        db.add(models.Payment(booking_id=booking.id, amount=amount, status="paid"))
        booking.status = "paid"
        await db.commit()
        flash(request, f"Оплата прошла успешно (демо): {amount} ₸.")
    return RedirectResponse("/account", status_code=303)


@router.post("/bookings/{booking_id}/cancel")
async def cancel(
    request: Request, booking_id: int,
    db: AsyncSession = Depends(get_db), user: models.User = Depends(customer_only),
):
    booking = await own_booking(db, booking_id, user)
    if booking is None:
        flash(request, "Бронь не найдена.", "error")
    elif not booking.can_cancel:
        flash(request, "Эту бронь уже нельзя отменить.", "error")
    else:
        paid = booking.payment is not None and booking.payment.status == "paid"
        booking.status = "cancelled"
        await db.commit()
        flash(request, "Бронь отменена. Возврат оплаты оформит менеджер платформы." if paid
              else "Бронь отменена.")
    return RedirectResponse("/account", status_code=303)


@router.post("/bookings/{booking_id}/review")
async def review(
    request: Request, booking_id: int,
    rating: int = Form(...), text: str = Form(""),
    db: AsyncSession = Depends(get_db), user: models.User = Depends(customer_only),
):
    booking = await own_booking(db, booking_id, user)
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
        await db.commit()
        flash(request, "Спасибо за отзыв!")
    return RedirectResponse("/account", status_code=303)


@router.post("/bookings/{booking_id}/dispute")
async def dispute(
    request: Request, booking_id: int, message: str = Form(""),
    db: AsyncSession = Depends(get_db), user: models.User = Depends(customer_only),
):
    booking = await own_booking(db, booking_id, user)
    message = message.strip()
    if booking is None:
        flash(request, "Бронь не найдена.", "error")
    elif booking.has_open_dispute:
        flash(request, "По этой брони уже есть открытый спор.", "error")
    elif len(message) < 10 or len(message) > 2000:
        flash(request, "Опишите проблему подробнее (10–2000 символов).", "error")
    else:
        db.add(models.Dispute(booking_id=booking.id, user_id=user.id, message=message))
        await db.commit()
        flash(request, "Обращение отправлено менеджеру платформы.")
    return RedirectResponse("/account", status_code=303)


@router.post("/account/profile")
async def update_profile(
    request: Request, full_name: str = Form(""),
    db: AsyncSession = Depends(get_db), user: models.User = Depends(require_user),
):
    full_name = full_name.strip()
    if len(full_name) < 2:
        flash(request, "Введите имя (минимум 2 символа).", "error")
    else:
        user.full_name = full_name
        await db.commit()
        flash(request, "Профиль обновлён.")
    return RedirectResponse("/account", status_code=303)


@router.post("/account/password")
async def change_password(
    request: Request,
    old_password: str = Form(""), new_password: str = Form(""), new_password2: str = Form(""),
    db: AsyncSession = Depends(get_db), user: models.User = Depends(require_user),
):
    if not await averify_password(old_password, user.password_hash):
        flash(request, "Текущий пароль указан неверно.", "error")
    elif len(new_password) < 6:
        flash(request, "Новый пароль должен быть не короче 6 символов.", "error")
    elif new_password != new_password2:
        flash(request, "Новые пароли не совпадают.", "error")
    else:
        user.password_hash = await ahash_password(new_password)
        await db.commit()
        flash(request, "Пароль изменён.")
    return RedirectResponse("/account", status_code=303)

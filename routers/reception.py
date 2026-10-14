"""Ресепшен: просмотр всех броней, заселение/выселение, статус уборки."""
import math
from datetime import date
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

import models
from constants import BOOKING_STATUS_LABELS, HOUSEKEEPING_LABELS
from database import get_db
from deps import flash, parse_int, reception_only, render, safe_redirect

router = APIRouter(prefix="/reception")

PAGE = 20
ROOMS_PAGE = 30
BOOKING_OPTS = (
    joinedload(models.Booking.user),
    joinedload(models.Booking.room).joinedload(models.Room.hotel),
)


def hotels_list(db: Session):
    return db.scalars(select(models.Hotel).order_by(models.Hotel.id)).all()


@router.get("", response_class=HTMLResponse)
def reception(
    request: Request, hotel_id: str | None = None, status: str | None = None, page: str | None = None,
    db: Session = Depends(get_db), user: models.User = Depends(reception_only),
):
    today = date.today()
    hid = parse_int(hotel_id)
    hconds = []
    if hid:
        hconds.append(models.Booking.room_id.in_(select(models.Room.id).where(models.Room.hotel_id == hid)))

    arrivals = db.scalars(
        select(models.Booking).where(
            models.Booking.status == "paid",
            models.Booking.check_in <= today, models.Booking.check_out > today, *hconds,
        ).options(*BOOKING_OPTS).order_by(models.Booking.check_in)
    ).all()
    in_house = db.scalars(
        select(models.Booking).where(models.Booking.status == "checked_in", *hconds)
        .options(*BOOKING_OPTS).order_by(models.Booking.check_out)
    ).all()

    all_conds = list(hconds)
    if status in BOOKING_STATUS_LABELS:
        all_conds.append(models.Booking.status == status)
    total = db.scalar(select(func.count(models.Booking.id)).where(*all_conds))
    pages = max(1, math.ceil(total / PAGE))
    current = min(max(1, parse_int(page) or 1), pages)
    all_bookings = db.scalars(
        select(models.Booking).where(*all_conds).options(*BOOKING_OPTS)
        .order_by(models.Booking.check_in.desc(), models.Booking.id.desc())
        .limit(PAGE).offset((current - 1) * PAGE)
    ).all()

    qs = urlencode({k: v for k, v in {"hotel_id": hid, "status": status if status in BOOKING_STATUS_LABELS else None}.items() if v})
    return render(
        request, "reception.html", db, user, active="reception",
        context={
            "arrivals": arrivals, "in_house": in_house, "all_bookings": all_bookings,
            "total": total, "page": current, "pages": pages, "qs": qs,
            "hid": hid, "status": status, "hotels": hotels_list(db), "sub": "bookings",
        },
    )


@router.post("/bookings/{booking_id}/check-in")
def check_in(
    request: Request, booking_id: int, redirect_to: str = Form("/reception"),
    db: Session = Depends(get_db), user: models.User = Depends(reception_only),
):
    booking = db.get(models.Booking, booking_id)
    today = date.today()
    if booking is None:
        flash(request, "Бронь не найдена.", "error")
    elif booking.status != "paid":
        flash(request, "Заселить можно только оплаченную бронь.", "error")
    elif not (booking.check_in <= today < booking.check_out):
        flash(request, "Заселение возможно только в период проживания.", "error")
    elif booking.room.housekeeping != "clean":
        flash(request, f"Номер {booking.room.number} ещё не готов: статус уборки — "
                       f"«{HOUSEKEEPING_LABELS[booking.room.housekeeping]}».", "error")
    else:
        booking.status = "checked_in"
        db.commit()
        flash(request, f"Гость {booking.user.full_name} заселён в номер {booking.room.number}.")
    return RedirectResponse(safe_redirect(redirect_to, "/reception"), status_code=303)


@router.post("/bookings/{booking_id}/check-out")
def check_out(
    request: Request, booking_id: int, redirect_to: str = Form("/reception"),
    db: Session = Depends(get_db), user: models.User = Depends(reception_only),
):
    booking = db.get(models.Booking, booking_id)
    if booking is None:
        flash(request, "Бронь не найдена.", "error")
    elif booking.status != "checked_in":
        flash(request, "Выселить можно только проживающего гостя.", "error")
    else:
        booking.status = "checked_out"
        booking.room.housekeeping = "dirty"  # после выезда номер нужно убрать
        db.commit()
        flash(request, f"Гость выселен. Номер {booking.room.number} отмечен как «Нужна уборка».")
    return RedirectResponse(safe_redirect(redirect_to, "/reception"), status_code=303)


@router.get("/rooms", response_class=HTMLResponse)
def housekeeping_page(
    request: Request, hotel_id: str | None = None, hk: str | None = None, page: str | None = None,
    db: Session = Depends(get_db), user: models.User = Depends(reception_only),
):
    hid = parse_int(hotel_id)
    conds = []
    if hid:
        conds.append(models.Room.hotel_id == hid)
    if hk in HOUSEKEEPING_LABELS:
        conds.append(models.Room.housekeeping == hk)
    total = db.scalar(select(func.count(models.Room.id)).where(*conds))
    pages = max(1, math.ceil(total / ROOMS_PAGE))
    current = min(max(1, parse_int(page) or 1), pages)
    rooms = db.scalars(
        select(models.Room).where(*conds).options(joinedload(models.Room.hotel))
        .order_by(models.Room.hotel_id, models.Room.number)
        .limit(ROOMS_PAGE).offset((current - 1) * ROOMS_PAGE)
    ).all()
    occupied = set(db.scalars(select(models.Booking.room_id).where(models.Booking.status == "checked_in")))
    qs = urlencode({k: v for k, v in {"hotel_id": hid, "hk": hk if hk in HOUSEKEEPING_LABELS else None}.items() if v})
    return render(
        request, "reception_rooms.html", db, user, active="reception",
        context={"rooms": rooms, "occupied": occupied, "total": total, "page": current,
                 "pages": pages, "qs": qs, "hid": hid, "hk": hk,
                 "hotels": hotels_list(db), "sub": "rooms"},
    )


@router.post("/rooms/{room_id}/housekeeping")
def set_housekeeping(
    request: Request, room_id: int, status: str = Form(...), redirect_to: str = Form("/reception/rooms"),
    db: Session = Depends(get_db), user: models.User = Depends(reception_only),
):
    room = db.get(models.Room, room_id)
    if room is None or status not in HOUSEKEEPING_LABELS:
        flash(request, "Некорректный запрос.", "error")
    else:
        room.housekeeping = status
        db.commit()
        flash(request, f"Номер {room.number}: статус — «{HOUSEKEEPING_LABELS[status]}».")
    return RedirectResponse(safe_redirect(redirect_to, "/reception/rooms"), status_code=303)

"""Страницы, доступные всем (включая гостей): каталог, номера, отзывы, сравнение цен."""
import math
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

import models
from constants import BOOKING_STATUS_LABELS
from database import get_db
from deps import flash, get_current_user, parse_date, parse_int, render
from services import approved_hotel_ids, availability_conditions, validate_dates

router = APIRouter()

PAGE_SIZE = 12
SEARCH_LIMIT = 24
SORTS = {
    "number": "По номеру",
    "price_asc": "Сначала дешёвые",
    "price_desc": "Сначала дорогие",
    "capacity": "По вместимости",
}


def approved_hotels(db: Session):
    return db.scalars(
        select(models.Hotel).where(models.Hotel.status == "approved").order_by(models.Hotel.id)
    ).all()


def hotel_ratings(db: Session) -> dict[int, tuple[float, int]]:
    rows = db.execute(
        select(models.Review.hotel_id, func.avg(models.Review.rating), func.count(models.Review.id))
        .group_by(models.Review.hotel_id)
    ).all()
    return {h: (round(float(avg), 1), cnt) for h, avg, cnt in rows}


@router.get("/", response_class=HTMLResponse)
def index(
    request: Request,
    check_in: str | None = None,
    check_out: str | None = None,
    hotel_id: str | None = None,
    guests: str | None = None,
    db: Session = Depends(get_db),
    user: models.User | None = Depends(get_current_user),
):
    hotels = approved_hotels(db)
    ci, co = parse_date(check_in), parse_date(check_out)
    hid, gst = parse_int(hotel_id), parse_int(guests)

    searched = bool(check_in or check_out)
    search_error, rooms, total = None, [], 0
    if searched:
        if ci is None or co is None:
            search_error = "Укажите обе даты: заезда и выезда."
        else:
            search_error = validate_dates(ci, co)
        if not search_error:
            conds = availability_conditions(ci, co, hid, gst)
            total = db.scalar(select(func.count(models.Room.id)).where(*conds))
            rooms = db.scalars(
                select(models.Room).where(*conds)
                .options(joinedload(models.Room.hotel))
                .order_by(models.Room.price_per_night, models.Room.hotel_id, models.Room.number)
                .limit(SEARCH_LIMIT)
            ).all()

    return render(
        request, "index.html", db, user, active="home",
        context={
            "hotels": hotels, "ratings": hotel_ratings(db),
            "searched": searched, "search_error": search_error,
            "rooms": rooms, "total": total, "shown_limit": SEARCH_LIMIT,
            "ci": ci.isoformat() if ci else "", "co": co.isoformat() if co else "",
            "hid": hid, "guests": gst or "",
        },
    )


@router.get("/rooms", response_class=HTMLResponse)
def rooms_page(
    request: Request,
    hotel_id: str | None = None,
    rooms_count: str | None = None,
    sort: str = "number",
    page: str | None = None,
    db: Session = Depends(get_db),
    user: models.User | None = Depends(get_current_user),
):
    hid, rc = parse_int(hotel_id), parse_int(rooms_count)
    if sort not in SORTS:
        sort = "number"

    visible = models.Room.hotel_id.in_(approved_hotel_ids())
    conds = [visible]
    if hid:
        conds.append(models.Room.hotel_id == hid)
    if rc:
        conds.append(models.Room.rooms_count == rc)

    order = {
        "number": (models.Room.hotel_id, models.Room.number),
        "price_asc": (models.Room.price_per_night, models.Room.number),
        "price_desc": (models.Room.price_per_night.desc(), models.Room.number),
        "capacity": (models.Room.capacity.desc(), models.Room.number),
    }[sort]

    total = db.scalar(select(func.count(models.Room.id)).where(*conds))
    pages = max(1, math.ceil(total / PAGE_SIZE))
    current = min(max(1, parse_int(page) or 1), pages)
    rooms = db.scalars(
        select(models.Room).where(*conds)
        .options(joinedload(models.Room.hotel))
        .order_by(*order).limit(PAGE_SIZE).offset((current - 1) * PAGE_SIZE)
    ).all()

    qs = urlencode({k: v for k, v in {
        "hotel_id": hid, "rooms_count": rc, "sort": sort if sort != "number" else None,
    }.items() if v})
    hotels = approved_hotels(db)
    return render(
        request, "rooms.html", db, user, active="rooms",
        context={
            "rooms": rooms, "total": total,
            "total_rooms": db.scalar(select(func.count(models.Room.id)).where(visible)),
            "page": current, "pages": pages, "qs": qs,
            "hid": hid, "rc": rc, "sort": sort, "sorts": SORTS, "hotels": hotels,
            "rooms_count_options": db.scalars(
                select(models.Room.rooms_count).where(visible).distinct().order_by(models.Room.rooms_count)
            ).all(),
        },
    )


@router.get("/hotels", response_class=HTMLResponse)
def hotels_page(
    request: Request,
    db: Session = Depends(get_db),
    user: models.User | None = Depends(get_current_user),
):
    """Каталог отелей + сравнение цен по типам номеров."""
    hotels = approved_hotels(db)
    rows = db.execute(
        select(models.Room.name, models.Room.hotel_id, func.min(models.Room.price_per_night))
        .where(models.Room.hotel_id.in_(approved_hotel_ids()))
        .group_by(models.Room.name, models.Room.hotel_id)
    ).all()
    table: dict[str, dict[int, int]] = {}
    for name, hotel_id, price in rows:
        table.setdefault(name, {})[hotel_id] = price
    types = sorted(table, key=lambda t: min(table[t].values()))
    cheapest = {t: min(table[t].values()) for t in types}
    return render(
        request, "hotels.html", db, user, active="hotels",
        context={"hotels": hotels, "ratings": hotel_ratings(db),
                 "types": types, "table": table, "cheapest": cheapest},
    )


@router.get("/hotels/{hotel_id}", response_class=HTMLResponse)
def hotel_detail(
    request: Request,
    hotel_id: int,
    db: Session = Depends(get_db),
    user: models.User | None = Depends(get_current_user),
):
    hotel = db.get(models.Hotel, hotel_id)
    if hotel is None or hotel.status != "approved":
        flash(request, "Отель не найден.", "error")
        return RedirectResponse("/hotels", status_code=303)

    room_types = db.execute(
        select(
            models.Room.name, func.count(models.Room.id), func.min(models.Room.price_per_night),
            func.max(models.Room.capacity), func.max(models.Room.rooms_count),
        ).where(models.Room.hotel_id == hotel.id).group_by(models.Room.name)
        .order_by(func.min(models.Room.price_per_night))
    ).all()
    reviews = db.scalars(
        select(models.Review).where(models.Review.hotel_id == hotel.id)
        .options(joinedload(models.Review.user))
        .order_by(models.Review.created_at.desc()).limit(50)
    ).all()
    return render(
        request, "hotel_detail.html", db, user, active="hotels",
        context={"hotel": hotel, "room_types": room_types, "reviews": reviews,
                 "rating": hotel_ratings(db).get(hotel.id)},
    )


@router.get("/about", response_class=HTMLResponse)
def about(
    request: Request,
    db: Session = Depends(get_db),
    user: models.User | None = Depends(get_current_user),
):
    return render(
        request, "about.html", db, user, active="about",
        context={
            "hotels": approved_hotels(db),
            "total_rooms": db.scalar(
                select(func.count(models.Room.id)).where(models.Room.hotel_id.in_(approved_hotel_ids()))
            ),
        },
    )

import math
import os
import re
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from urllib.parse import urlencode

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload
from starlette.middleware.sessions import SessionMiddleware

import models
from database import Base, SessionLocal, engine, get_db
from security import hash_password, verify_password
from seed import seed_database

BASE_DIR = Path(__file__).resolve().parent
SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-me")

PAGE_SIZE = 12
SEARCH_LIMIT = 24
MAX_NIGHTS = 30
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Контакты для футера (демо-данные — замените на свои)
CONTACTS = {
    "phone": "+7 (700) 000-00-00",
    "email": "info@hotelbooking.example",
    "address": "г. Астана, пр. Достык, 12 (главный офис)",
    "app_store": "#",
    "google_play": "#",
}

SORTS = {
    "number": "По номеру",
    "price_asc": "Сначала дешёвые",
    "price_desc": "Сначала дорогие",
    "capacity": "По вместимости",
}


def money(value: int) -> str:
    return f"{int(value):,}".replace(",", " ")


templates = Jinja2Templates(directory=BASE_DIR / "templates")
templates.env.filters["money"] = money


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        seed_database(db)
    yield


app = FastAPI(title="Hotel Booking", lifespan=lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=SECRET_KEY,
    max_age=14 * 24 * 3600,
    same_site="lax",
)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")


# ---------- вспомогательные функции ----------

class NotAuthenticated(Exception):
    pass


@app.exception_handler(NotAuthenticated)
async def not_authenticated_handler(request: Request, exc: NotAuthenticated):
    flash(request, "Войдите в аккаунт, чтобы продолжить.", "error")
    nxt = request.url.path if request.method == "GET" else "/"
    return RedirectResponse(f"/login?{urlencode({'next': nxt})}", status_code=303)


def get_current_user(
    request: Request, db: Session = Depends(get_db)
) -> models.User | None:
    user_id = request.session.get("user_id")
    return db.get(models.User, user_id) if user_id else None


def require_user(user: models.User | None = Depends(get_current_user)) -> models.User:
    if user is None:
        raise NotAuthenticated()
    return user


def flash(request: Request, message: str, category: str = "success") -> None:
    request.session["flash"] = {"category": category, "message": message}


def safe_redirect(path: str | None, default: str = "/") -> str:
    """Разрешаем только локальные пути (защита от open redirect)."""
    if path and path.startswith("/") and not path.startswith("//") and "\\" not in path:
        return path
    return default


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
        "footer_hotels": db.scalars(select(models.Hotel).order_by(models.Hotel.id)).all(),
        "today": date.today().isoformat(),
    }
    ctx.update(context or {})
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)


def validate_dates(check_in: date, check_out: date) -> str | None:
    if check_in < date.today():
        return "Дата заезда не может быть в прошлом."
    if check_out <= check_in:
        return "Дата выезда должна быть позже даты заезда."
    if (check_out - check_in).days > MAX_NIGHTS:
        return f"Максимальный срок бронирования — {MAX_NIGHTS} ночей."
    return None


def free_room_filters(check_in: date, check_out: date, hotel_id=None, guests=None):
    # Интервалы [заезд, выезд) пересекаются, если
    # существующий.заезд < новый.выезд И существующий.выезд > новый.заезд
    busy = select(models.Booking.room_id).where(
        models.Booking.check_in < check_out,
        models.Booking.check_out > check_in,
    )
    conds = [models.Room.id.not_in(busy)]
    if hotel_id:
        conds.append(models.Room.hotel_id == hotel_id)
    if guests:
        conds.append(models.Room.capacity >= guests)
    return conds


def validate_booking(
    db: Session, room_id: int, check_in: date, check_out: date
) -> tuple[models.Room | None, str | None]:
    room = db.get(models.Room, room_id)
    if room is None:
        return None, "Такого номера не существует."

    error = validate_dates(check_in, check_out)
    if error:
        return None, error

    conflict = db.scalar(
        select(models.Booking).where(
            models.Booking.room_id == room.id,
            models.Booking.check_in < check_out,
            models.Booking.check_out > check_in,
        )
    )
    if conflict:
        return None, (
            f"Номер {room.number} уже занят с "
            f"{conflict.check_in:%d.%m.%Y} по {conflict.check_out:%d.%m.%Y}."
        )
    return room, None


# ---------- страницы ----------

@app.get("/", response_class=HTMLResponse)
def index(
    request: Request,
    check_in: str | None = None,
    check_out: str | None = None,
    hotel_id: str | None = None,
    guests: str | None = None,
    db: Session = Depends(get_db),
    user: models.User | None = Depends(get_current_user),
):
    hotels = db.scalars(select(models.Hotel).order_by(models.Hotel.id)).all()
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
            conds = free_room_filters(ci, co, hid, gst)
            total = db.scalar(select(func.count(models.Room.id)).where(*conds))
            rooms = db.scalars(
                select(models.Room)
                .where(*conds)
                .options(joinedload(models.Room.hotel))
                .order_by(models.Room.price_per_night, models.Room.hotel_id, models.Room.number)
                .limit(SEARCH_LIMIT)
            ).all()

    return render(
        request, "index.html", db, user, active="home",
        context={
            "hotels": hotels, "searched": searched, "search_error": search_error,
            "rooms": rooms, "total": total, "shown_limit": SEARCH_LIMIT,
            "ci": ci.isoformat() if ci else "", "co": co.isoformat() if co else "",
            "hid": hid, "guests": gst or "",
        },
    )


@app.get("/rooms", response_class=HTMLResponse)
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

    conds = []
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
        select(models.Room)
        .where(*conds)
        .options(joinedload(models.Room.hotel))
        .order_by(*order)
        .limit(PAGE_SIZE)
        .offset((current - 1) * PAGE_SIZE)
    ).all()

    qs = urlencode(
        {k: v for k, v in {
            "hotel_id": hid, "rooms_count": rc,
            "sort": sort if sort != "number" else None,
        }.items() if v}
    )
    return render(
        request, "rooms.html", db, user, active="rooms",
        context={
            "rooms": rooms, "total": total, "total_rooms": db.scalar(select(func.count(models.Room.id))),
            "page": current, "pages": pages, "qs": qs,
            "hid": hid, "rc": rc, "sort": sort, "sorts": SORTS,
            "hotels": db.scalars(select(models.Hotel).order_by(models.Hotel.id)).all(),
            "rooms_count_options": db.scalars(
                select(models.Room.rooms_count).distinct().order_by(models.Room.rooms_count)
            ).all(),
        },
    )


@app.get("/about", response_class=HTMLResponse)
def about(
    request: Request,
    db: Session = Depends(get_db),
    user: models.User | None = Depends(get_current_user),
):
    hotels = db.scalars(select(models.Hotel).order_by(models.Hotel.id)).all()
    return render(
        request, "about.html", db, user, active="about",
        context={
            "hotels": hotels,
            "total_rooms": db.scalar(select(func.count(models.Room.id))),
        },
    )


# ---------- бронирование ----------

@app.post("/book")
def book(
    request: Request,
    room_id: int = Form(...),
    check_in: date = Form(...),
    check_out: date = Form(...),
    redirect_to: str = Form("/rooms"),
    db: Session = Depends(get_db),
    user: models.User = Depends(require_user),
):
    room, error = validate_booking(db, room_id, check_in, check_out)
    if error:
        flash(request, error, "error")
        return RedirectResponse(safe_redirect(redirect_to, "/rooms"), status_code=303)

    db.add(models.Booking(user_id=user.id, room_id=room.id, check_in=check_in, check_out=check_out))
    db.commit()
    flash(request, f"Номер {room.number} ({room.hotel.name}) забронирован!", "success")
    return RedirectResponse("/account", status_code=303)


@app.post("/bookings/{booking_id}/cancel")
def cancel_booking(
    request: Request,
    booking_id: int,
    db: Session = Depends(get_db),
    user: models.User = Depends(require_user),
):
    booking = db.get(models.Booking, booking_id)
    if booking is None or booking.user_id != user.id:
        flash(request, "Бронь не найдена.", "error")
    elif booking.check_in <= date.today():
        flash(request, "Нельзя отменить бронь, которая уже началась.", "error")
    else:
        db.delete(booking)
        db.commit()
        flash(request, "Бронь отменена.", "success")
    return RedirectResponse("/account", status_code=303)


# ---------- авторизация ----------

@app.get("/account", response_class=HTMLResponse)
def account(
    request: Request,
    db: Session = Depends(get_db),
    user: models.User = Depends(require_user),
):
    bookings = db.scalars(
        select(models.Booking)
        .where(models.Booking.user_id == user.id)
        .options(joinedload(models.Booking.room).joinedload(models.Room.hotel))
        .order_by(models.Booking.check_in)
    ).all()
    today = date.today()
    return render(
        request, "account.html", db, user, active="account",
        context={
            "upcoming": [b for b in bookings if b.check_out >= today],
            "past": [b for b in reversed(bookings) if b.check_out < today],
            "now": today,
        },
    )


@app.get("/register", response_class=HTMLResponse)
def register_form(
    request: Request,
    next: str = "/",
    db: Session = Depends(get_db),
    user: models.User | None = Depends(get_current_user),
):
    if user:
        return RedirectResponse("/account", status_code=303)
    return render(request, "register.html", db, user, context={"next": next, "form": {}})


@app.post("/register")
def register(
    request: Request,
    full_name: str = Form(""),
    email: str = Form(""),
    password: str = Form(""),
    password2: str = Form(""),
    next_url: str = Form("/", alias="next"),
    db: Session = Depends(get_db),
):
    full_name, email = full_name.strip(), email.strip().lower()
    error = None
    if len(full_name) < 2:
        error = "Введите имя (минимум 2 символа)."
    elif not EMAIL_RE.match(email):
        error = "Введите корректный email."
    elif len(password) < 6:
        error = "Пароль должен быть не короче 6 символов."
    elif password != password2:
        error = "Пароли не совпадают."
    elif db.scalar(select(models.User.id).where(models.User.email == email)):
        error = "Пользователь с таким email уже зарегистрирован."

    if error:
        return render(
            request, "register.html", db, None, status_code=400,
            context={"error": error, "next": next_url,
                     "form": {"full_name": full_name, "email": email}},
        )

    user = models.User(email=email, full_name=full_name, password_hash=hash_password(password))
    db.add(user)
    db.commit()
    request.session.clear()
    request.session["user_id"] = user.id
    flash(request, f"Добро пожаловать, {user.full_name}!")
    return RedirectResponse(safe_redirect(next_url), status_code=303)


@app.get("/login", response_class=HTMLResponse)
def login_form(
    request: Request,
    next: str = "/",
    db: Session = Depends(get_db),
    user: models.User | None = Depends(get_current_user),
):
    if user:
        return RedirectResponse("/account", status_code=303)
    return render(request, "login.html", db, user, context={"next": next, "form": {}})


@app.post("/login")
def login(
    request: Request,
    email: str = Form(""),
    password: str = Form(""),
    next_url: str = Form("/", alias="next"),
    db: Session = Depends(get_db),
):
    email = email.strip().lower()
    user = db.scalar(select(models.User).where(models.User.email == email))
    if user is None or not verify_password(password, user.password_hash):
        return render(
            request, "login.html", db, None, status_code=400,
            context={"error": "Неверный email или пароль.", "next": next_url,
                     "form": {"email": email}},
        )
    request.session.clear()
    request.session["user_id"] = user.id
    return RedirectResponse(safe_redirect(next_url), status_code=303)


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", status_code=303)

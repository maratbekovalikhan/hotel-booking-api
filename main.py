from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

import models
from database import Base, SessionLocal, engine, get_db

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=BASE_DIR / "templates")

SEED_ROOMS = [
    (101, "Стандарт", 2, 4500),
    (102, "Стандарт", 2, 4500),
    (201, "Комфорт", 3, 6500),
    (202, "Семейный", 4, 8000),
    (301, "Люкс", 2, 12000),
]


def seed_rooms() -> None:
    with SessionLocal() as db:
        if db.scalar(select(models.Room.id).limit(1)) is None:
            db.add_all(
                models.Room(number=n, name=name, capacity=cap, price_per_night=price)
                for n, name, cap, price in SEED_ROOMS
            )
            db.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    seed_rooms()
    yield


app = FastAPI(title="Hotel Booking", lifespan=lifespan)


def validate_booking(
    db: Session, room_number: int, check_in: date, check_out: date
) -> tuple[models.Room | None, str | None]:
    """Возвращает (комната, None) при успехе или (None, текст ошибки)."""
    room = db.scalar(select(models.Room).where(models.Room.number == room_number))
    if room is None:
        return None, f"Комнаты с номером {room_number} не существует."

    if check_in < date.today():
        return None, "Дата заезда не может быть в прошлом."

    if check_out <= check_in:
        return None, "Дата выезда должна быть позже даты заезда."

    # Интервалы [заезд, выезд) пересекаются, если:
    #   существующий.заезд < новый.выезд И существующий.выезд > новый.заезд
    # День выезда одного гостя может быть днём заезда другого.
    conflict = db.scalar(
        select(models.Booking).where(
            models.Booking.room_id == room.id,
            models.Booking.check_in < check_out,
            models.Booking.check_out > check_in,
        )
    )
    if conflict is not None:
        return None, (
            f"Комната {room.number} уже занята с "
            f"{conflict.check_in:%d.%m.%Y} по {conflict.check_out:%d.%m.%Y}."
        )

    return room, None


def render_index(
    request: Request,
    db: Session,
    error: str | None = None,
    form: dict | None = None,
    status_code: int = 200,
):
    rooms = db.scalars(select(models.Room).order_by(models.Room.number)).all()
    bookings = db.scalars(
        select(models.Booking)
        .where(models.Booking.check_out >= date.today())
        .order_by(models.Booking.check_in)
    ).all()
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "rooms": rooms,
            "bookings": bookings,
            "error": error,
            "form": form or {},
            "today": date.today().isoformat(),
        },
        status_code=status_code,
    )


@app.get("/", response_class=HTMLResponse)
def index(request: Request, db: Session = Depends(get_db)):
    return render_index(request, db)


@app.post("/book")
def book(
    request: Request,
    room_number: int = Form(...),
    check_in: date = Form(...),
    check_out: date = Form(...),
    db: Session = Depends(get_db),
):
    room, error = validate_booking(db, room_number, check_in, check_out)
    form = {
        "room_number": room_number,
        "check_in": check_in.isoformat(),
        "check_out": check_out.isoformat(),
    }
    if error:
        return render_index(request, db, error=error, form=form, status_code=400)

    db.add(models.Booking(room_id=room.id, check_in=check_in, check_out=check_out))
    db.commit()
    return RedirectResponse("/", status_code=303)


@app.post("/bookings/{booking_id}/delete")
def delete_booking(booking_id: int, db: Session = Depends(get_db)):
    booking = db.get(models.Booking, booking_id)
    if booking:
        db.delete(booking)
        db.commit()
    return RedirectResponse("/", status_code=303)

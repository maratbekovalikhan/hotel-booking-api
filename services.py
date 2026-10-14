import re
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

import models
from constants import ACTIVE_BOOKING_STATUSES, ROLE_CUSTOMER
from security import hash_password

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MAX_NIGHTS = 30


def validate_dates(check_in: date, check_out: date) -> str | None:
    if check_in < date.today():
        return "Дата заезда не может быть в прошлом."
    if check_out <= check_in:
        return "Дата выезда должна быть позже даты заезда."
    if (check_out - check_in).days > MAX_NIGHTS:
        return f"Максимальный срок бронирования — {MAX_NIGHTS} ночей."
    return None


def approved_hotel_ids():
    return select(models.Hotel.id).where(models.Hotel.status == "approved")


def availability_conditions(check_in: date, check_out: date, hotel_id=None, guests=None):
    """Условия для номеров, свободных на период [check_in, check_out).
    Интервалы пересекаются, если существующий.заезд < новый.выезд И существующий.выезд > новый.заезд."""
    busy_by_bookings = select(models.Booking.room_id).where(
        models.Booking.status.in_(ACTIVE_BOOKING_STATUSES),
        models.Booking.check_in < check_out,
        models.Booking.check_out > check_in,
    )
    busy_by_blocks = select(models.RoomBlock.room_id).where(
        models.RoomBlock.date_from < check_out,
        models.RoomBlock.date_to > check_in,
    )
    conds = [
        models.Room.hotel_id.in_(approved_hotel_ids()),
        models.Room.id.not_in(busy_by_bookings),
        models.Room.id.not_in(busy_by_blocks),
    ]
    if hotel_id:
        conds.append(models.Room.hotel_id == hotel_id)
    if guests:
        conds.append(models.Room.capacity >= guests)
    return conds


def validate_booking(
    db: Session, room_id: int, check_in: date, check_out: date
) -> tuple[models.Room | None, str | None]:
    room = db.get(models.Room, room_id)
    if room is None or room.hotel.status != "approved":
        return None, "Такого номера не существует."

    error = validate_dates(check_in, check_out)
    if error:
        return None, error

    conflict = db.scalar(
        select(models.Booking).where(
            models.Booking.room_id == room.id,
            models.Booking.status.in_(ACTIVE_BOOKING_STATUSES),
            models.Booking.check_in < check_out,
            models.Booking.check_out > check_in,
        )
    )
    if conflict:
        return None, (
            f"Номер {room.number} уже занят с "
            f"{conflict.check_in:%d.%m.%Y} по {conflict.check_out:%d.%m.%Y}."
        )

    block = db.scalar(
        select(models.RoomBlock).where(
            models.RoomBlock.room_id == room.id,
            models.RoomBlock.date_from < check_out,
            models.RoomBlock.date_to > check_in,
        )
    )
    if block:
        return None, f"Номер {room.number} временно недоступен в выбранные даты."
    return room, None


def validate_new_user(db: Session, full_name: str, email: str, password: str) -> str | None:
    if len(full_name) < 2:
        return "Введите имя (минимум 2 символа)."
    if not EMAIL_RE.match(email):
        return "Введите корректный email."
    if len(password) < 6:
        return "Пароль должен быть не короче 6 символов."
    if db.scalar(select(models.User.id).where(models.User.email == email)):
        return "Пользователь с таким email уже зарегистрирован."
    return None


def create_user(
    db: Session, full_name: str, email: str, password: str, role: str = ROLE_CUSTOMER, **extra
) -> models.User:
    user = models.User(
        email=email, full_name=full_name, password_hash=hash_password(password),
        role=role, **extra,
    )
    db.add(user)
    db.commit()
    return user


def get_setting(db: Session, key: str, default: str) -> str:
    row = db.get(models.Setting, key)
    return row.value if row else default


def set_setting(db: Session, key: str, value: str) -> None:
    row = db.get(models.Setting, key)
    if row:
        row.value = value
    else:
        db.add(models.Setting(key=key, value=value))
    db.commit()

from datetime import date, datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import (
    Boolean, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(100))
    password_hash: Mapped[str] = mapped_column(String(255))
    # customer | receptionist | manager | superadmin  (Guest — это просто не вошедший пользователь)
    role: Mapped[str] = mapped_column(String(20), default="customer", index=True)
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    blocked_reason: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    # Права менеджера, которыми управляет суперадмин
    can_create_receptionists: Mapped[bool] = mapped_column(Boolean, default=False)
    can_add_hotels: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    bookings: Mapped[list["Booking"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Hotel(Base):
    __tablename__ = "hotels"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    city: Mapped[str] = mapped_column(String(100))
    address: Mapped[str] = mapped_column(String(255))
    stars: Mapped[int] = mapped_column(Integer)
    description: Mapped[str] = mapped_column(Text)
    amenities: Mapped[str] = mapped_column(Text)  # через запятую
    phone: Mapped[str] = mapped_column(String(50))
    email: Mapped[str] = mapped_column(String(100))
    # pending | approved | rejected — публично видны только approved
    status: Mapped[str] = mapped_column(String(20), default="approved", index=True)
    submitted_by_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    moderation_note: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    rooms: Mapped[list["Room"]] = relationship(
        back_populates="hotel", cascade="all, delete-orphan"
    )
    reviews: Mapped[list["Review"]] = relationship(
        back_populates="hotel", cascade="all, delete-orphan"
    )

    @property
    def amenity_list(self) -> list[str]:
        return [a.strip() for a in self.amenities.split(",") if a.strip()]


class Room(Base):
    __tablename__ = "rooms"
    __table_args__ = (UniqueConstraint("hotel_id", "number", name="uq_room_number_per_hotel"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    hotel_id: Mapped[int] = mapped_column(ForeignKey("hotels.id"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(100))  # тип номера
    capacity: Mapped[int] = mapped_column(Integer)
    rooms_count: Mapped[int] = mapped_column(Integer)  # количество комнат в номере
    price_per_night: Mapped[int] = mapped_column(Integer)
    description: Mapped[str] = mapped_column(String(255))
    # clean | dirty | cleaning — меняет ресепшен
    housekeeping: Mapped[str] = mapped_column(String(20), default="clean")

    hotel: Mapped[Hotel] = relationship(back_populates="rooms")
    bookings: Mapped[list["Booking"]] = relationship(
        back_populates="room", cascade="all, delete-orphan"
    )
    blocks: Mapped[list["RoomBlock"]] = relationship(
        back_populates="room", cascade="all, delete-orphan"
    )


class Booking(Base):
    __tablename__ = "bookings"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    room_id: Mapped[int] = mapped_column(ForeignKey("rooms.id"), index=True)
    check_in: Mapped[date] = mapped_column(Date)
    check_out: Mapped[date] = mapped_column(Date)
    # pending | paid | checked_in | checked_out | cancelled
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    user: Mapped[User] = relationship(back_populates="bookings")
    room: Mapped[Room] = relationship(back_populates="bookings")
    payment: Mapped[Optional["Payment"]] = relationship(
        back_populates="booking", uselist=False, cascade="all, delete-orphan"
    )
    review: Mapped[Optional["Review"]] = relationship(
        back_populates="booking", uselist=False, cascade="all, delete-orphan"
    )
    disputes: Mapped[list["Dispute"]] = relationship(
        back_populates="booking", cascade="all, delete-orphan"
    )

    @property
    def nights(self) -> int:
        return (self.check_out - self.check_in).days

    @property
    def total_price(self) -> int:
        return self.nights * self.room.price_per_night

    @property
    def days_left(self) -> int:
        """Сколько дней осталось до даты выезда (срок действия брони)."""
        return (self.check_out - date.today()).days

    @property
    def can_pay(self) -> bool:
        return self.status == "pending"

    @property
    def can_cancel(self) -> bool:
        return self.status in ("pending", "paid") and self.check_in > date.today()

    @property
    def can_review(self) -> bool:
        return (
            self.status in ("paid", "checked_in", "checked_out")
            and self.check_out <= date.today()
            and self.review is None
        )

    @property
    def has_open_dispute(self) -> bool:
        return any(d.status == "open" for d in self.disputes)


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    booking_id: Mapped[int] = mapped_column(ForeignKey("bookings.id"), unique=True)
    amount: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="paid", index=True)  # paid | refunded
    paid_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    refunded_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    refunded_by: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    refund_reason: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    booking: Mapped[Booking] = relationship(back_populates="payment")


class Review(Base):
    __tablename__ = "reviews"

    id: Mapped[int] = mapped_column(primary_key=True)
    booking_id: Mapped[int] = mapped_column(ForeignKey("bookings.id"), unique=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    hotel_id: Mapped[int] = mapped_column(ForeignKey("hotels.id"), index=True)
    rating: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    booking: Mapped[Booking] = relationship(back_populates="review")
    user: Mapped[User] = relationship()
    hotel: Mapped[Hotel] = relationship(back_populates="reviews")


class Dispute(Base):
    __tablename__ = "disputes"

    id: Mapped[int] = mapped_column(primary_key=True)
    booking_id: Mapped[int] = mapped_column(ForeignKey("bookings.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    message: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="open", index=True)  # open | resolved
    resolution: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    resolved_by: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    booking: Mapped[Booking] = relationship(back_populates="disputes")
    user: Mapped[User] = relationship()


class RoomBlock(Base):
    """Временная блокировка номера (техобслуживание, уборка). Период [date_from, date_to)."""
    __tablename__ = "room_blocks"

    id: Mapped[int] = mapped_column(primary_key=True)
    room_id: Mapped[int] = mapped_column(ForeignKey("rooms.id"), index=True)
    date_from: Mapped[date] = mapped_column(Date)
    date_to: Mapped[date] = mapped_column(Date)  # не включительно
    reason: Mapped[str] = mapped_column(String(255))
    created_by: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    room: Mapped[Room] = relationship(back_populates="blocks")

    @property
    def last_day(self) -> date:
        return self.date_to - timedelta(days=1)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(50), primary_key=True)
    value: Mapped[str] = mapped_column(String(255))

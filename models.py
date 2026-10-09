from datetime import date

from sqlalchemy import Date, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(100))
    password_hash: Mapped[str] = mapped_column(String(255))

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

    rooms: Mapped[list["Room"]] = relationship(
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
    capacity: Mapped[int] = mapped_column(Integer)  # максимум гостей
    rooms_count: Mapped[int] = mapped_column(Integer)  # количество комнат в номере
    price_per_night: Mapped[int] = mapped_column(Integer)
    description: Mapped[str] = mapped_column(String(255))

    hotel: Mapped[Hotel] = relationship(back_populates="rooms")
    bookings: Mapped[list["Booking"]] = relationship(
        back_populates="room", cascade="all, delete-orphan"
    )


class Booking(Base):
    __tablename__ = "bookings"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    room_id: Mapped[int] = mapped_column(ForeignKey("rooms.id"), index=True)
    check_in: Mapped[date] = mapped_column(Date)
    check_out: Mapped[date] = mapped_column(Date)

    user: Mapped[User] = relationship(back_populates="bookings")
    room: Mapped[Room] = relationship(back_populates="bookings")

    @property
    def nights(self) -> int:
        return (self.check_out - self.check_in).days

    @property
    def total_price(self) -> int:
        return self.nights * self.room.price_per_night

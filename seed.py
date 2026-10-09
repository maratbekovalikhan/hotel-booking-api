"""Демо-данные: 3 отеля и 197 номеров (60 + 72 + 65).
Адреса, телефоны и описания вымышленные — замените на свои."""
from sqlalchemy import select
from sqlalchemy.orm import Session

import models

# (тип, гостей, комнат в номере, множитель цены, описание)
ROOM_TYPES = [
    ("Стандарт", 2, 1, 1.0, "Уютный номер с удобной кроватью и рабочей зоной."),
    ("Комфорт", 3, 2, 1.4, "Просторный номер со спальней и отдельной гостиной зоной."),
    ("Семейный", 4, 3, 1.8, "Две спальни и гостиная — удобно для семьи с детьми."),
    ("Люкс", 4, 4, 2.8, "Апартаменты с гостиной, спальней, кабинетом и панорамным видом."),
]
# Шаблон этажа из 12 номеров: индексы типов из ROOM_TYPES
FLOOR_LAYOUT = [0] * 6 + [1] * 3 + [2] * 2 + [3]

HOTELS = [
    {
        "name": "Grand Astana Hotel",
        "city": "Астана",
        "address": "пр. Достык, 12",
        "stars": 5,
        "description": "Флагманский отель в деловом центре столицы с видом на город.",
        "amenities": "СПА-салон, Ресторан, Бассейн, Фитнес-зал, Бесплатный Wi-Fi, Парковка, Трансфер из аэропорта",
        "phone": "+7 (7172) 00-00-01",
        "email": "astana@hotelbooking.example",
        "rooms": 60,
        "base_price": 20000,
    },
    {
        "name": "Almaty Residence",
        "city": "Алматы",
        "address": "ул. Фурманова, 88",
        "stars": 4,
        "description": "Современный отель у подножия гор, рядом с главными достопримечательностями.",
        "amenities": "Ресторан, Бизнес-центр, Фитнес-зал, Бесплатный Wi-Fi, Завтрак «шведский стол»",
        "phone": "+7 (727) 000-00-02",
        "email": "almaty@hotelbooking.example",
        "rooms": 72,
        "base_price": 15000,
    },
    {
        "name": "Burabay Lake Resort",
        "city": "Бурабай",
        "address": "ул. Абылай хана, 5",
        "stars": 4,
        "description": "Курортный отель на берегу озера, окружённый сосновым лесом.",
        "amenities": "СПА-салон, Ресторан, Бассейн, Собственный пляж, Прокат велосипедов, Парковка",
        "phone": "+7 (71636) 0-00-03",
        "email": "burabay@hotelbooking.example",
        "rooms": 65,
        "base_price": 17000,
    },
]


def seed_database(db: Session) -> None:
    if db.scalar(select(models.Hotel.id).limit(1)) is not None:
        return

    for data in HOTELS:
        hotel = models.Hotel(
            **{k: v for k, v in data.items() if k not in ("rooms", "base_price")}
        )
        for i in range(data["rooms"]):
            floor, pos = divmod(i, len(FLOOR_LAYOUT))
            name, capacity, rooms_count, mult, desc = ROOM_TYPES[FLOOR_LAYOUT[pos]]
            hotel.rooms.append(
                models.Room(
                    number=(floor + 1) * 100 + pos + 1,
                    name=name,
                    capacity=capacity,
                    rooms_count=rooms_count,
                    price_per_night=round(data["base_price"] * mult, -2),
                    description=desc,
                )
            )
        db.add(hotel)
    db.commit()

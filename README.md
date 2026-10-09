# Система бронирования отеля

FastAPI + SQLAlchemy + SQLite + Jinja2.

## Структура

- `main.py` — приложение, маршруты, валидация броней
- `database.py` — подключение к SQLite, сессии
- `models.py` — модели `Room` и `Booking`
- `templates/index.html` — HTML-интерфейс

## Запуск

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --reload
```

Откройте http://127.0.0.1:8000

При первом запуске создаётся файл `hotel.db` и добавляются 5 тестовых комнат.

## Валидация

- комната должна существовать;
- дата заезда не в прошлом;
- дата выезда позже даты заезда;
- брони на одну комнату не пересекаются (день выезда одного гостя
  может быть днём заезда другого).

"""Сброс пароля пользователя напрямую в базе (например, суперадмина).

Запуск из корня проекта (с активированным .venv):
    python reset_password.py                 # сбросить пароль суперадмина
    python reset_password.py user@mail.com   # сбросить пароль конкретного пользователя
"""
import asyncio
import getpass
import sys

from sqlalchemy import select

import models
from constants import ROLE_SUPERADMIN
from database import AsyncSessionLocal, engine
from security import hash_password


async def main(email: str | None) -> int:
    async with AsyncSessionLocal() as db:
        if email:
            stmt = select(models.User).where(models.User.email == email.strip().lower())
        else:
            stmt = select(models.User).where(models.User.role == ROLE_SUPERADMIN).order_by(models.User.id).limit(1)
        user = await db.scalar(stmt)
        if user is None:
            print("Пользователь не найден.")
            return 1

        print(f"Пользователь: {user.email} ({user.role})")
        password = getpass.getpass("Новый пароль (от 6 символов): ")
        if len(password) < 6:
            print("Пароль слишком короткий.")
            return 1
        if password != getpass.getpass("Повторите пароль: "):
            print("Пароли не совпадают.")
            return 1

        user.password_hash = hash_password(password)
        await db.commit()
        print("Готово. Теперь войдите с новым паролем.")
    await engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else None)))

import os
from contextlib import asynccontextmanager
from urllib.parse import urlencode

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import ProgrammingError
from starlette.middleware.sessions import SessionMiddleware

from database import IS_POSTGRES, AsyncSessionLocal, Base, engine
from deps import BASE_DIR, Forbidden, NotAuthenticated, flash
from routers import admin, auth, customer, manager, public, reception
from seed import seed_database, seed_settings, seed_superadmin

SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-me")
if SECRET_KEY == "dev-secret-change-me":
    print("[!] SECRET_KEY не задан — используется небезопасный ключ по умолчанию. Задайте его в .env")


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not IS_POSTGRES:
        # Локальная SQLite для разработки: схема создаётся автоматически.
        # Для PostgreSQL (Supabase) схему создаёт только Alembic: alembic upgrade head
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    try:
        async with AsyncSessionLocal() as db:
            await seed_database(db)
            await seed_superadmin(db)
            await seed_settings(db)
    except ProgrammingError as exc:
        raise RuntimeError("Таблиц в базе нет. Сначала выполните: alembic upgrade head") from exc
    yield
    await engine.dispose()


app = FastAPI(title="Hotel Booking", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, max_age=14 * 24 * 3600, same_site="lax")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")


@app.exception_handler(NotAuthenticated)
async def not_authenticated_handler(request: Request, exc: NotAuthenticated):
    flash(request, "Войдите в аккаунт, чтобы продолжить.", "error")
    nxt = request.url.path if request.method == "GET" else "/"
    return RedirectResponse(f"/login?{urlencode({'next': nxt})}", status_code=303)


@app.exception_handler(Forbidden)
async def forbidden_handler(request: Request, exc: Forbidden):
    flash(request, "Недостаточно прав для этого действия.", "error")
    return RedirectResponse("/", status_code=303)


for r in (public.router, auth.router, customer.router, reception.router, manager.router, admin.router):
    app.include_router(r)

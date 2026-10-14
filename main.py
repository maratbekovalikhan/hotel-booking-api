import os
from contextlib import asynccontextmanager
from urllib.parse import urlencode

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from database import Base, SessionLocal, engine
from deps import BASE_DIR, Forbidden, NotAuthenticated, flash
from routers import admin, auth, customer, manager, public, reception
from seed import seed_database, seed_settings, seed_superadmin

SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-me")


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        seed_database(db)
        seed_superadmin(db)
        seed_settings(db)
    yield


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

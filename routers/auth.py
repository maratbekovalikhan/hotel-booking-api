from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import models
from constants import ROLE_CUSTOMER
from database import get_db
from deps import flash, get_current_user, render, safe_redirect
from security import averify_password
from services import create_user, validate_new_user

router = APIRouter()


@router.get("/register", response_class=HTMLResponse)
async def register_form(
    request: Request, next: str = "/",
    db: AsyncSession = Depends(get_db), user: models.User | None = Depends(get_current_user),
):
    if user:
        return RedirectResponse("/account", status_code=303)
    return await render(request, "register.html", db, user, context={"next": next, "form": {}})


@router.post("/register")
async def register(
    request: Request,
    full_name: str = Form(""), email: str = Form(""),
    password: str = Form(""), password2: str = Form(""),
    next_url: str = Form("/", alias="next"),
    db: AsyncSession = Depends(get_db),
):
    full_name, email = full_name.strip(), email.strip().lower()
    error = await validate_new_user(db, full_name, email, password)
    if not error and password != password2:
        error = "Пароли не совпадают."
    if error:
        return await render(
            request, "register.html", db, None, status_code=400,
            context={"error": error, "next": next_url,
                     "form": {"full_name": full_name, "email": email}},
        )
    # Публичная регистрация ВСЕГДА создаёт клиента. Персонал создают только менеджер/суперадмин.
    user = await create_user(db, full_name, email, password, role=ROLE_CUSTOMER)
    request.session.clear()
    request.session["user_id"] = user.id
    flash(request, f"Добро пожаловать, {user.full_name}!")
    return RedirectResponse(safe_redirect(next_url), status_code=303)


@router.get("/login", response_class=HTMLResponse)
async def login_form(
    request: Request, next: str = "/",
    db: AsyncSession = Depends(get_db), user: models.User | None = Depends(get_current_user),
):
    if user:
        return RedirectResponse("/account", status_code=303)
    return await render(request, "login.html", db, user, context={"next": next, "form": {}})


@router.post("/login")
async def login(
    request: Request,
    email: str = Form(""), password: str = Form(""),
    next_url: str = Form("/", alias="next"),
    db: AsyncSession = Depends(get_db),
):
    email = email.strip().lower()
    user = await db.scalar(select(models.User).where(models.User.email == email))
    error = None
    if user is None or not await averify_password(password, user.password_hash):
        error = "Неверный email или пароль."
    elif user.is_blocked:
        reason = f" Причина: {user.blocked_reason}." if user.blocked_reason else ""
        error = f"Аккаунт заблокирован.{reason}"
    if error:
        return await render(
            request, "login.html", db, None, status_code=400,
            context={"error": error, "next": next_url, "form": {"email": email}},
        )
    request.session.clear()
    request.session["user_id"] = user.id
    return RedirectResponse(safe_redirect(next_url), status_code=303)


@router.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", status_code=303)

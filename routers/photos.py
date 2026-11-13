"""Фото отелей и номеров (Supabase Storage). Доступно менеджерам с правом
«добавлять отели и номера» и суперадмину."""
import math
import uuid

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

import models
import storage
from database import get_db
from deps import Forbidden, flash, has_perm, manager_only, parse_int, render, safe_redirect

router = APIRouter(prefix="/manager/photos")

MAX_PHOTOS = 10  # на один отель / один номер
ROOMS_PAGE = 15


async def photo_manager(user: models.User = Depends(manager_only)) -> models.User:
    if not has_perm(user, "can_add_hotels"):
        raise Forbidden()
    return user


def back(path: str, default: str = "/manager/photos") -> RedirectResponse:
    return RedirectResponse(safe_redirect(path, default), status_code=303)


@router.get("", response_class=HTMLResponse)
async def photos_page(
    request: Request, hotel_id: str | None = None, page: str | None = None,
    db: AsyncSession = Depends(get_db), user: models.User = Depends(photo_manager),
):
    hotels = (await db.scalars(select(models.Hotel).order_by(models.Hotel.id))).all()
    hid = parse_int(hotel_id) or (hotels[0].id if hotels else None)
    hotel = next((h for h in hotels if h.id == hid), None)

    hotel_photos, rooms, total, pages, current = [], [], 0, 1, 1
    if hotel:
        hotel_photos = (await db.scalars(
            select(models.Photo).where(models.Photo.hotel_id == hotel.id).order_by(models.Photo.id)
        )).all()
        total = await db.scalar(select(func.count(models.Room.id)).where(models.Room.hotel_id == hotel.id))
        pages = max(1, math.ceil(total / ROOMS_PAGE))
        current = min(max(1, parse_int(page) or 1), pages)
        rooms = (await db.scalars(
            select(models.Room).where(models.Room.hotel_id == hotel.id)
            .options(selectinload(models.Room.photos))
            .order_by(models.Room.number).limit(ROOMS_PAGE).offset((current - 1) * ROOMS_PAGE)
        )).all()

    return await render(
        request, "manager_photos.html", db, user, active="manager",
        context={"hotels": hotels, "hotel": hotel, "hotel_photos": hotel_photos, "rooms": rooms,
                 "total": total, "page": current, "pages": pages,
                 "qs": f"hotel_id={hotel.id}" if hotel else "",
                 "storage_ok": storage.is_configured(), "max_photos": MAX_PHOTOS,
                 "max_mb": storage.MAX_BYTES // (1024 * 1024), "sub": "photos"},
    )


async def save_photos(
    db: AsyncSession, files: list[UploadFile], user: models.User,
    *, hotel_id: int | None = None, room_id: int | None = None,
) -> tuple[int, list[str]]:
    target_cond = models.Photo.hotel_id == hotel_id if hotel_id else models.Photo.room_id == room_id
    existing = await db.scalar(select(func.count(models.Photo.id)).where(target_cond))
    folder = f"hotels/{hotel_id}" if hotel_id else f"rooms/{room_id}"

    uploaded_paths: list[str] = []
    errors: list[str] = []
    for f in files:
        data = await f.read(storage.MAX_BYTES + 1)
        if not data:
            continue  # пустое поле формы
        name = f.filename or "файл"
        if existing + len(uploaded_paths) >= MAX_PHOTOS:
            errors.append(f"лимит — {MAX_PHOTOS} фото")
            break
        if len(data) > storage.MAX_BYTES:
            errors.append(f"«{name}»: больше {storage.MAX_BYTES // (1024 * 1024)} МБ")
            continue
        content_type = storage.detect_type(data)
        if content_type is None:
            errors.append(f"«{name}»: допустимы только JPG, PNG и WEBP")
            continue
        path = f"{folder}/{uuid.uuid4().hex}.{storage.ALLOWED[content_type]}"  # имя клиента не используем
        try:
            await storage.upload(path, data, content_type)
        except storage.StorageError as exc:
            errors.append(f"«{name}»: {exc}")
            continue
        uploaded_paths.append(path)
        db.add(models.Photo(hotel_id=hotel_id, room_id=room_id, storage_path=path,
                            url=storage.public_url(path), uploaded_by=user.full_name))

    if uploaded_paths:
        try:
            await db.commit()
        except Exception:
            await db.rollback()
            for p in uploaded_paths:  # не оставляем «осиротевшие» файлы в хранилище
                await storage.delete(p)
            raise
    return len(uploaded_paths), errors


def report(request: Request, count: int, errors: list[str]) -> None:
    if count and not errors:
        flash(request, f"Загружено фото: {count}.")
    elif count:
        flash(request, f"Загружено: {count}. Не загружено — " + "; ".join(errors), "error")
    elif errors:
        flash(request, "Не загружено — " + "; ".join(errors), "error")
    else:
        flash(request, "Выберите файлы для загрузки.", "error")


@router.post("/hotel/{hotel_id}")
async def upload_hotel_photos(
    request: Request, hotel_id: int,
    files: list[UploadFile] | None = File(None), redirect_to: str = Form("/manager/photos"),
    db: AsyncSession = Depends(get_db), user: models.User = Depends(photo_manager),
):
    if not storage.is_configured():
        flash(request, "Хранилище не настроено: задайте SUPABASE_URL и SUPABASE_SERVICE_ROLE_KEY в .env.", "error")
    elif await db.get(models.Hotel, hotel_id) is None:
        flash(request, "Отель не найден.", "error")
    else:
        count, errors = await save_photos(db, files or [], user, hotel_id=hotel_id)
        report(request, count, errors)
    return back(redirect_to)


@router.post("/room/{room_id}")
async def upload_room_photos(
    request: Request, room_id: int,
    files: list[UploadFile] | None = File(None), redirect_to: str = Form("/manager/photos"),
    db: AsyncSession = Depends(get_db), user: models.User = Depends(photo_manager),
):
    if not storage.is_configured():
        flash(request, "Хранилище не настроено: задайте SUPABASE_URL и SUPABASE_SERVICE_ROLE_KEY в .env.", "error")
    elif await db.get(models.Room, room_id) is None:
        flash(request, "Номер не найден.", "error")
    else:
        count, errors = await save_photos(db, files or [], user, room_id=room_id)
        report(request, count, errors)
    return back(redirect_to)


@router.post("/{photo_id}/delete")
async def delete_photo(
    request: Request, photo_id: int, redirect_to: str = Form("/manager/photos"),
    db: AsyncSession = Depends(get_db), user: models.User = Depends(photo_manager),
):
    photo = await db.get(models.Photo, photo_id)
    if photo is None:
        flash(request, "Фото не найдено.", "error")
    elif not storage.is_configured() or not await storage.delete(photo.storage_path):
        flash(request, "Не удалось удалить файл из хранилища — фото оставлено.", "error")
    else:
        await db.delete(photo)
        await db.commit()
        flash(request, "Фото удалено.")
    return back(redirect_to)

"""Клиент Supabase Storage (REST). Загрузка идёт с сервера секретным ключом,
браузер получает только публичные ссылки на фото."""
import os

import httpx
from dotenv import load_dotenv

load_dotenv()

ALLOWED = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
MAX_BYTES = 5 * 1024 * 1024  # 5 МБ на файл


class StorageError(Exception):
    pass


def _url() -> str:
    return os.getenv("SUPABASE_URL", "").rstrip("/")


def _key() -> str:
    return os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")


def _bucket() -> str:
    return os.getenv("SUPABASE_BUCKET", "hotel-photos")


def is_configured() -> bool:
    return bool(_url() and _key())


def detect_type(data: bytes) -> str | None:
    """Тип определяем по содержимому файла, а не по имени/заголовку от клиента."""
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def public_url(path: str) -> str:
    return f"{_url()}/storage/v1/object/public/{_bucket()}/{path}"


def _headers(content_type: str | None = None) -> dict:
    key = _key()
    headers = {"apikey": key}
    if key.startswith("eyJ"):  # legacy service_role — это JWT
        headers["Authorization"] = f"Bearer {key}"
    if content_type:
        headers["Content-Type"] = content_type
    return headers


def _object_url(path: str) -> str:
    return f"{_url()}/storage/v1/object/{_bucket()}/{path}"


async def upload(path: str, data: bytes, content_type: str) -> None:
    headers = _headers(content_type)
    headers["x-upsert"] = "false"
    headers["cache-control"] = "max-age=31536000"  # имена уникальны, можно кешировать надолго
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(_object_url(path), content=data, headers=headers)
    except httpx.HTTPError as exc:
        raise StorageError("нет связи с хранилищем") from exc
    if resp.status_code in (401, 403):
        raise StorageError("хранилище отклонило ключ (проверьте SUPABASE_SERVICE_ROLE_KEY)")
    if resp.status_code == 404:
        raise StorageError(f"бакет «{_bucket()}» не найден")
    if resp.status_code >= 400:
        raise StorageError(f"ошибка хранилища ({resp.status_code})")


async def delete(path: str) -> bool:
    """True — объект удалён (или его уже нет)."""
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.delete(_object_url(path), headers=_headers())
    except httpx.HTTPError:
        return False
    return resp.status_code < 400 or resp.status_code == 404

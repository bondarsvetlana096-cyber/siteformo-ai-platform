from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.core.config import settings
from app.services.demo_enhancer import inject_demo_cta
from app.services.html_postprocess import rewrite_asset_urls
from app.services.storage import get_storage


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _master_storage_key(request_id: str) -> str:
    return f'masters/{request_id}/index.html'


def _build_continue_url(request_id: str, token: str) -> str:
    return (
        f"{settings.main_site_base_url.rstrip('/')}"
        f"/{settings.main_site_continue_path.lstrip('/')}"
        f"?request_id={request_id}&demo_token={token}"
    )


def publish_demo(request_id: str, html: str) -> tuple[str, str, datetime, datetime, str]:
    token = uuid4().hex + uuid4().hex
    master_storage_key = _master_storage_key(str(request_id))
    master_html = rewrite_asset_urls(html)
    get_storage().put_text(master_storage_key, master_html)
    demo_url = f'{settings.demo_base_url}/demo/{token}'
    expires_at = _utcnow() + timedelta(minutes=settings.demo_ttl_minutes)
    retention_expires_at = _utcnow() + timedelta(hours=settings.demo_retention_hours)
    return token, master_storage_key, expires_at, retention_expires_at, demo_url


def build_demo_delivery_html(request_id: str, demo_token: str, master_html: str) -> str:
    continue_url = _build_continue_url(str(request_id), demo_token)
    return inject_demo_cta(master_html, str(request_id), demo_token, continue_url)

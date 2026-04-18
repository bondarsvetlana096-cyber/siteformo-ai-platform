from __future__ import annotations

import mimetypes
from pathlib import Path

import boto3
import httpx
from botocore.client import Config as BotoConfig

from app.core.config import settings


class StorageError(Exception):
    pass


class BaseStorage:
    def put_text(self, key: str, content: str) -> None:
        raise NotImplementedError

    def get_bytes(self, key: str) -> tuple[bytes, str]:
        raise NotImplementedError

    def delete(self, key: str) -> None:
        raise NotImplementedError


class LocalStorage(BaseStorage):
    def __init__(self, root: str):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def put_text(self, key: str, content: str) -> None:
        path = self.root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')

    def get_bytes(self, key: str) -> tuple[bytes, str]:
        path = self.root / key
        if not path.exists():
            raise StorageError('missing asset')
        mime, _ = mimetypes.guess_type(path.name)
        return path.read_bytes(), mime or 'application/octet-stream'

    def delete(self, key: str) -> None:
        path = self.root / key
        if path.exists():
            path.unlink()


class SupabaseStorage(BaseStorage):
    def _headers(self) -> dict[str, str]:
        return {
            'apikey': settings.supabase_service_role_key,
            'Authorization': f'Bearer {settings.supabase_service_role_key}',
        }

    def put_text(self, key: str, content: str) -> None:
        url = f"{settings.supabase_url}/storage/v1/object/{settings.supabase_storage_bucket}/{key}"
        response = httpx.put(url, headers={**self._headers(), 'content-type': 'text/html; charset=utf-8', 'x-upsert': 'true'}, content=content.encode('utf-8'), timeout=30.0)
        if response.status_code >= 300:
            raise StorageError(response.text)

    def get_bytes(self, key: str) -> tuple[bytes, str]:
        url = f"{settings.supabase_url}/storage/v1/object/{settings.supabase_storage_bucket}/{key}"
        response = httpx.get(url, headers=self._headers(), timeout=30.0)
        if response.status_code >= 300:
            raise StorageError(response.text)
        return response.content, response.headers.get('content-type', 'application/octet-stream')

    def delete(self, key: str) -> None:
        url = f"{settings.supabase_url}/storage/v1/object/{settings.supabase_storage_bucket}"
        response = httpx.request('DELETE', url, headers={**self._headers(), 'content-type': 'application/json'}, json={'prefixes': [key]}, timeout=30.0)
        if response.status_code >= 300:
            raise StorageError(response.text)


class S3Storage(BaseStorage):
    def __init__(self) -> None:
        self.client = boto3.client(
            's3',
            endpoint_url=settings.s3_endpoint_url or None,
            aws_access_key_id=settings.s3_access_key_id,
            aws_secret_access_key=settings.s3_secret_access_key,
            region_name=settings.s3_region,
            config=BotoConfig(signature_version='s3v4'),
        )

    def put_text(self, key: str, content: str) -> None:
        self.client.put_object(Bucket=settings.s3_bucket, Key=key, Body=content.encode('utf-8'), ContentType='text/html; charset=utf-8')

    def get_bytes(self, key: str) -> tuple[bytes, str]:
        obj = self.client.get_object(Bucket=settings.s3_bucket, Key=key)
        return obj['Body'].read(), obj.get('ContentType', 'application/octet-stream')

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=settings.s3_bucket, Key=key)


_storage: BaseStorage | None = None


def get_storage() -> BaseStorage:
    global _storage
    if _storage is not None:
        return _storage
    backend = settings.storage_backend.lower()
    if backend == 'supabase':
        _storage = SupabaseStorage()
    elif backend == 's3':
        _storage = S3Storage()
    else:
        _storage = LocalStorage(settings.demo_storage_dir)
    return _storage

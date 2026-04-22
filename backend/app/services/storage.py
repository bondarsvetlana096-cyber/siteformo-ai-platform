from __future__ import annotations

import logging
import mimetypes
from pathlib import Path

import boto3
import httpx
from botocore.client import Config as BotoConfig

from app.core.config import settings


logger = logging.getLogger("siteformo.storage")


class StorageError(Exception):
    pass


class BaseStorage:
    def put_text(self, key: str, content: str, content_type: str = "text/html; charset=utf-8") -> None:
        raise NotImplementedError

    def put_bytes(self, key: str, content: bytes, content_type: str = "application/octet-stream") -> None:
        raise NotImplementedError

    def get_bytes(self, key: str) -> tuple[bytes, str]:
        raise NotImplementedError

    def read_text(self, key: str) -> str:
        data, _ = self.get_bytes(key)
        return data.decode("utf-8")

    def delete(self, key: str) -> None:
        raise NotImplementedError


class LocalStorage(BaseStorage):
    def __init__(self, root: str):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def put_text(self, key: str, content: str, content_type: str = "text/html; charset=utf-8") -> None:
        self.put_bytes(key, content.encode("utf-8"), content_type=content_type)

    def put_bytes(self, key: str, content: bytes, content_type: str = "application/octet-stream") -> None:
        path = self.root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        logger.info("[STORAGE][local] put_bytes key=%s size=%s", key, len(content))

    def get_bytes(self, key: str) -> tuple[bytes, str]:
        path = self.root / key
        if not path.exists():
            raise StorageError("missing asset")
        mime, _ = mimetypes.guess_type(path.name)
        return path.read_bytes(), mime or "application/octet-stream"

    def delete(self, key: str) -> None:
        path = self.root / key
        if path.exists():
            path.unlink()
            parent = path.parent
            while parent != self.root and parent.exists():
                try:
                    parent.rmdir()
                except OSError:
                    break
                parent = parent.parent


class SupabaseStorage(BaseStorage):
    def _headers(self) -> dict[str, str]:
        return {
            "apikey": settings.supabase_service_role_key,
            "Authorization": f"Bearer {settings.supabase_service_role_key}",
        }

    def put_text(self, key: str, content: str, content_type: str = "text/html; charset=utf-8") -> None:
        self.put_bytes(key, content.encode("utf-8"), content_type=content_type)

    def put_bytes(self, key: str, content: bytes, content_type: str = "application/octet-stream") -> None:
        bucket = settings.supabase_storage_bucket
        url = f"{settings.supabase_url}/storage/v1/object/{bucket}/{key}"

        logger.info(
            "[STORAGE][supabase] upload start bucket=%s key=%s size=%s content_type=%s",
            bucket,
            key,
            len(content),
            content_type,
        )

        response = httpx.post(
            url,
            headers={
                **self._headers(),
                "content-type": content_type,
                "x-upsert": "true",
            },
            content=content,
            timeout=30.0,
        )

        if response.status_code >= 300:
            logger.error(
                "[STORAGE][supabase] upload failed bucket=%s key=%s status=%s body=%s",
                bucket,
                key,
                response.status_code,
                response.text,
            )
            raise StorageError(response.text)

        logger.info(
            "[STORAGE][supabase] upload ok bucket=%s key=%s status=%s",
            bucket,
            key,
            response.status_code,
        )

        verify_response = httpx.get(
            url,
            headers=self._headers(),
            timeout=30.0,
        )

        if verify_response.status_code >= 300:
            logger.error(
                "[STORAGE][supabase] verify failed bucket=%s key=%s status=%s body=%s",
                bucket,
                key,
                verify_response.status_code,
                verify_response.text,
            )
            raise StorageError(
                f"Supabase upload verification failed for key={key}: {verify_response.text}"
            )

        logger.info(
            "[STORAGE][supabase] verify ok bucket=%s key=%s size=%s content_type=%s",
            bucket,
            key,
            len(verify_response.content),
            verify_response.headers.get("content-type", "application/octet-stream"),
        )

    def get_bytes(self, key: str) -> tuple[bytes, str]:
        bucket = settings.supabase_storage_bucket
        url = f"{settings.supabase_url}/storage/v1/object/{bucket}/{key}"

        logger.info("[STORAGE][supabase] get bucket=%s key=%s", bucket, key)

        response = httpx.get(url, headers=self._headers(), timeout=30.0)
        if response.status_code >= 300:
            logger.error(
                "[STORAGE][supabase] get failed bucket=%s key=%s status=%s body=%s",
                bucket,
                key,
                response.status_code,
                response.text,
            )
            raise StorageError(response.text)

        return response.content, response.headers.get("content-type", "application/octet-stream")

    def delete(self, key: str) -> None:
        bucket = settings.supabase_storage_bucket
        url = f"{settings.supabase_url}/storage/v1/object/{bucket}"

        logger.info("[STORAGE][supabase] delete bucket=%s key=%s", bucket, key)

        response = httpx.request(
            "DELETE",
            url,
            headers={**self._headers(), "content-type": "application/json"},
            json={"prefixes": [key]},
            timeout=30.0,
        )
        if response.status_code >= 300:
            logger.error(
                "[STORAGE][supabase] delete failed bucket=%s key=%s status=%s body=%s",
                bucket,
                key,
                response.status_code,
                response.text,
            )
            raise StorageError(response.text)


class S3Storage(BaseStorage):
    def __init__(self) -> None:
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url or None,
            aws_access_key_id=settings.s3_access_key_id,
            aws_secret_access_key=settings.s3_secret_access_key,
            region_name=settings.s3_region,
            config=BotoConfig(signature_version="s3v4"),
        )

    def put_text(self, key: str, content: str, content_type: str = "text/html; charset=utf-8") -> None:
        self.put_bytes(key, content.encode("utf-8"), content_type=content_type)

    def put_bytes(self, key: str, content: bytes, content_type: str = "application/octet-stream") -> None:
        self.client.put_object(
            Bucket=settings.s3_bucket,
            Key=key,
            Body=content,
            ContentType=content_type,
        )

    def get_bytes(self, key: str) -> tuple[bytes, str]:
        obj = self.client.get_object(Bucket=settings.s3_bucket, Key=key)
        return obj["Body"].read(), obj.get("ContentType", "application/octet-stream")

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=settings.s3_bucket, Key=key)


_storage: BaseStorage | None = None


def get_storage() -> BaseStorage:
    global _storage
    if _storage is not None:
        return _storage

    backend = settings.storage_backend.lower()
    if backend == "auto":
        if settings.supabase_url and settings.supabase_service_role_key and settings.supabase_storage_bucket:
            backend = "supabase"
        else:
            backend = "local"

    logger.info("[STORAGE] init backend=%s", backend)

    if backend == "supabase":
        logger.info("[STORAGE] using supabase bucket=%s", settings.supabase_storage_bucket)
        _storage = SupabaseStorage()
    elif backend == "s3":
        _storage = S3Storage()
    else:
        logger.info("[STORAGE] using local dir=%s", settings.demo_storage_dir)
        _storage = LocalStorage(settings.demo_storage_dir)

    return _storage
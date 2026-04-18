from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.request import Job, JobStatus

logger = logging.getLogger("siteformo.queue")
_supabase = None


QUEUE_NAMES = {
    "generate_demo": "generate_demo_queue",
    "expire_demo": "expire_demo_queue",
    "follow_up_check": "follow_up_queue",
    "cleanup_demo": "cleanup_demo_queue",
}


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _get_supabase_client():
    global _supabase
    if _supabase is not None:
        return _supabase
    if not settings.supabase_url or not settings.supabase_service_role_key:
        raise RuntimeError("Supabase queue backend is enabled but credentials are missing")
    from supabase import create_client
    _supabase = create_client(settings.supabase_url, settings.supabase_service_role_key)
    logger.info("[QUEUE] supabase client initialized")
    return _supabase


def _enqueue_supabase(job_type: str, payload: dict[str, Any], scheduled_at: datetime | None = None) -> None:
    queue_name = QUEUE_NAMES[job_type]
    body = {"job_type": job_type, **payload}
    rpc_payload: dict[str, Any] = {"queue_name": queue_name, "msg": body}
    if scheduled_at is not None:
        target = scheduled_at.astimezone(timezone.utc).replace(tzinfo=None) if scheduled_at.tzinfo else scheduled_at
        delay = int(max(0, (target - _utcnow_naive()).total_seconds()))
        rpc_payload["delay"] = delay
    result = _get_supabase_client().rpc("pgmq_send", rpc_payload).execute()
    logger.info("[QUEUE] pgmq_send result queue=%s data=%s", queue_name, result.data)


def enqueue_job(db: Session, job_type: str, payload: dict, scheduled_at: datetime | None = None) -> Job | None:
    logger.info("[QUEUE] enqueue_job called job_type=%s payload=%s scheduled_at=%s", job_type, payload, scheduled_at)
    logger.info("[QUEUE] queue_backend=%s", settings.queue_backend)
    if settings.queue_backend.lower() == "supabase":
        logger.info("[QUEUE] using supabase backend for job_type=%s", job_type)
        _enqueue_supabase(job_type, payload, scheduled_at)
        return None


def fetch_next_job(db: Session) -> Job | None:
    stmt = select(Job).where(Job.status == JobStatus.PENDING).where(or_(Job.scheduled_at.is_(None), Job.scheduled_at <= _utcnow_naive())).order_by(Job.created_at.asc()).limit(1)
    job = db.execute(stmt).scalar_one_or_none()
    if job:
        logger.info("[QUEUE] fetched db job id=%s type=%s", job.id, job.job_type)
    return job

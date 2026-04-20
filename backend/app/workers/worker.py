from __future__ import annotations

import asyncio
import json
import logging

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.request import JobStatus
from app.services.analytics import log_exception
from app.services.queue import QUEUE_NAMES, fetch_next_job
from app.services.request_service import process_cleanup_job, process_expire_job, process_follow_up_job, process_generate_job

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
logger = logging.getLogger("siteformo.worker")


def _get_supabase_client():
    from supabase import create_client
    return create_client(settings.supabase_url, settings.supabase_service_role_key)


async def _handle_request(job_type: str, payload: dict) -> None:
    request_id = payload["request_id"]
    db = SessionLocal()
    try:
        if job_type == "generate_demo":
            await process_generate_job(db, request_id)
        elif job_type == "expire_demo":
            await process_expire_job(db, request_id)
        elif job_type == "follow_up_check":
            await process_follow_up_job(db, request_id, payload.get("reason", "checkout"))
        elif job_type == "cleanup_demo":
            await process_cleanup_job(db, request_id)
        else:
            logger.warning("[WORKER] unknown job_type=%s request_id=%s", job_type, request_id)
    finally:
        db.close()


async def run_once_db() -> None:
    db = SessionLocal()
    try:
        job = fetch_next_job(db)
        if job is None:
            logger.info("[WORKER][db] no jobs found")
            return
        job.status = JobStatus.PROCESSING
        job.attempts += 1
        db.commit()
        try:
            await _handle_request(job.job_type, job.payload)
            job.status = JobStatus.DONE
            job.last_error = None
            db.commit()
        except Exception as exc:
            job.status = JobStatus.FAILED
            job.last_error = str(exc)
            db.commit()
            logger.exception("[WORKER][db] job failed id=%s type=%s error=%s", getattr(job, "id", None), job.job_type, exc)
            log_exception(exc, {"stage": "worker_db", "job_type": job.job_type, "request_id": job.payload.get("request_id")})
            raise
    finally:
        db.close()


async def run_loop_db() -> None:
    logger.info("[WORKER][db] polling started")
    while True:
        try:
            await run_once_db()
        except Exception as exc:
            logger.error("[WORKER][db] loop error: %s", exc, exc_info=True)
        await asyncio.sleep(settings.queue_poll_seconds)


async def run_loop_supabase() -> None:
    client = _get_supabase_client()
    vt = settings.queue_visibility_timeout_seconds
    logger.info("[WORKER][supabase] polling started vt=%s", vt)
    queue_pairs = [(name, job_type) for job_type, name in QUEUE_NAMES.items()]
    while True:
        found_message = False
        for queue_name, job_type in queue_pairs:
            try:
                res = client.rpc("pgmq_read", {"queue_name": queue_name, "vt": vt, "qty": 1}).execute()
                messages = res.data or []
                for msg in messages:
                    found_message = True
                    body = msg.get("message")
                    if isinstance(body, str):
                        body = json.loads(body)
                    try:
                        await _handle_request(job_type, body)
                        client.rpc("pgmq_delete", {"queue_name": queue_name, "msg_id": msg["msg_id"]}).execute()
                    except Exception as exc:
                        logger.exception("[WORKER] failed job queue=%s payload=%s error=%s", queue_name, body, exc)
                        log_exception(exc, {"stage": "worker_supabase", "queue_name": queue_name, "request_id": body.get("request_id")})
            except Exception as exc:
                logger.exception("[WORKER] queue poll error queue=%s error=%s", queue_name, exc)
                log_exception(exc, {"stage": "worker_queue_poll", "queue_name": queue_name})
        if not found_message:
            logger.info("[WORKER] no messages in queues")
            await asyncio.sleep(settings.queue_poll_seconds)


async def main() -> None:
    logger.info("[WORKER] queue_backend=%s", settings.queue_backend)
    logger.info("[WORKER] supabase_url_set=%s", bool(settings.supabase_url))
    logger.info("[WORKER] supabase_service_role_key_set=%s", bool(settings.supabase_service_role_key))
    if settings.queue_backend.lower() == "supabase":
        await run_loop_supabase()
    else:
        await run_loop_db()


if __name__ == "__main__":
    asyncio.run(main())

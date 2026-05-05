import os
import asyncio
import traceback
import time

from sqlalchemy.orm import Session
from sqlalchemy import text

from app.db.session import SessionLocal
from app.models.order import Order
from app.services import generation_service
from app.services.email_service import send_email
from app.services.design_quality_pipeline_service import DesignQualityPipelineService
from app.services.pipeline_result_service import persist_quality_pipeline_result


# =========================
# SAFE DB EXECUTION
# =========================
def safe_db_call(fn, retries=5, delay=3):
    for attempt in range(retries):
        try:
            return fn()
        except Exception as e:
            print(f"⚠️ DB error (attempt {attempt+1}): {e}")
            time.sleep(delay)
    raise Exception("DB failed after retries")


def _get_client_email(order: Order):
    if getattr(order, "client", None) and getattr(order.client, "email", None):
        return order.client.email

    extended_brief = getattr(order, "extended_brief", None) or {}
    if isinstance(extended_brief, dict):
        contact = extended_brief.get("contact", {}) or {}
        return contact.get("email")

    return None


async def _send_preview_email(order: Order, order_id: str):
    client_email = _get_client_email(order)

    if not client_email:
        print("⚠️ No client email found")
        return

    preview_link = f"{os.getenv('APP_BASE_URL')}/design-previews?order_id={order_id}"

    await send_email(
        to=client_email,
        subject="Your design previews are ready",
        html=f"<a href='{preview_link}'>View designs</a>",
    )

    print("✅ Preview email sent")


async def process_job(job):
    order_id = str(job["order_id"])
    job_type = job.get("job_type") or "DESIGN_PREVIEWS"

    print(f"🚀 Processing {job_type} for {order_id}")

    db: Session = SessionLocal()

    try:
        order = safe_db_call(lambda: db.query(Order).filter(Order.id == order_id).first())

        if not order:
            raise Exception("Order not found")

        service = generation_service.GenerationService()

        if job_type == "DESIGN_PREVIEWS":
            result = service.generate_design_previews_for_order(db, order, order.extended_brief)

            quality = DesignQualityPipelineService().run_for_order(
                order=order,
                preview_payload=result,
                extended_brief=order.extended_brief,
            )

            persist_quality_pipeline_result(db, order, quality)

            if quality.get("status") == "READY_TO_SEND":
                await _send_preview_email(order, order_id)

        elif job_type == "FINAL_GENERATION":
            service.start_full_generation_for_order(
                db=db,
                order=order,
                note="Final generation started",
            )

    finally:
        db.close()


def claim_next_job():
    def _query():
        db: Session = SessionLocal()

        try:
            job = db.execute(text("""
                select *
                from generation_jobs
                where status = 'PENDING'
                order by created_at asc
                limit 1
                for update skip locked
            """)).mappings().first()

            if not job:
                return None

            db.execute(text("""
                update generation_jobs
                set status = 'PROCESSING',
                    attempts = attempts + 1
                where id = :id
            """), {"id": job["id"]})

            db.commit()

            return job

        finally:
            db.close()

    return safe_db_call(_query)


def mark_completed(job_id):
    safe_db_call(lambda: _mark_completed(job_id))


def _mark_completed(job_id):
    db: Session = SessionLocal()
    try:
        db.execute(text("""
            update generation_jobs
            set status = 'COMPLETED'
            where id = :id
        """), {"id": job_id})
        db.commit()
    finally:
        db.close()


def mark_failed(job_id, error):
    safe_db_call(lambda: _mark_failed(job_id, error))


def _mark_failed(job_id, error):
    db: Session = SessionLocal()
    try:
        db.execute(text("""
            update generation_jobs
            set status = 'FAILED',
                error = :error
            where id = :id
        """), {"id": job_id, "error": error[:2000]})
        db.commit()
    finally:
        db.close()


# =========================
# MAIN LOOP
# =========================
async def worker_loop():
    print("🔥 Worker started")

    while True:
        try:
            job = claim_next_job()

            if not job:
                await asyncio.sleep(10)  # 🔥 меньше нагрузки
                continue

            await process_job(job)
            mark_completed(job["id"])

        except Exception as e:
            print("❌ Worker crash:", e)
            await asyncio.sleep(5)  # 🔥 пауза при падении

        await asyncio.sleep(2)


if __name__ == "__main__":
    asyncio.run(worker_loop())
import os
import asyncio
import traceback

from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.order import Order, OrderStatus
from app.services import generation_service
from app.services.design_quality_pipeline_service import DesignQualityPipelineService
from app.services.email_service import send_email
from app.services.generation_queue_service import (
    claim_next_generation_job,
    mark_job_completed,
    mark_job_failed,
)
from app.services.pipeline_result_service import persist_quality_pipeline_result


def _job_value(job, key, default=None):
    if job is None:
        return default
    try:
        return job._mapping.get(key, default)
    except Exception:
        pass
    try:
        return job[key]
    except Exception:
        return getattr(job, key, default)


def _client_email(order: Order):
    client = getattr(order, "client", None)
    return (
        getattr(client, "email", None)
        or getattr(order, "email", None)
        or ((getattr(order, "brief_answers", None) or {}).get("email"))
        or ((getattr(order, "extended_brief", None) or {}).get("email"))
    )


async def process_preview_job(db: Session, order: Order, order_id: str):
    service = generation_service.GenerationService()

    result = service.generate_design_previews_for_order(
        db, order, getattr(order, "extended_brief", None) or {}
    )

    quality_pipeline = DesignQualityPipelineService()
    quality_result = quality_pipeline.run_for_order(
        order=order,
        preview_payload=result,
        extended_brief=getattr(order, "extended_brief", None) or {},
    )

    persist_quality_pipeline_result(db, order, quality_result)
    print("Generated previews + quality result:", quality_result)

    if quality_result.get("status") != "READY_TO_SEND":
        print("Manual review required, skipping client email")
        return

    client_email = _client_email(order)
    if client_email:
        preview_link = f"{os.getenv('APP_BASE_URL', 'https://siteformo.com').rstrip('/')}/design-previews?order_id={order_id}"
        await send_email(
            to=client_email,
            subject="Your design previews are ready",
            html=f"""
            <h2>Your design previews are ready</h2>
            <p>Click below to view and select your design:</p>
            <p><a href="{preview_link}">View designs</a></p>
            """,
        )


async def process_final_generation_job(db: Session, order: Order):
    service = generation_service.GenerationService()
    service.start_full_generation_for_order(
        db,
        order,
        note="Full generation started after client selected a design preview.",
    )


async def process_job(job):
    order_id = str(_job_value(job, "order_id"))
    job_type = str(_job_value(job, "job_type", "DESIGN_PREVIEWS") or "DESIGN_PREVIEWS")

    print(f"Processing {job_type} job for order {order_id}")

    db: Session = SessionLocal()
    try:
        order = db.query(Order).filter(Order.id == order_id).first()
        if not order:
            raise Exception("Order not found")

        if job_type == "FINAL_GENERATION":
            await process_final_generation_job(db, order)
        else:
            await process_preview_job(db, order, order_id)

    finally:
        db.close()


async def worker_loop():
    print("Generation worker started")

    while True:
        job = claim_next_generation_job()

        if not job:
            await asyncio.sleep(5)
            continue

        job_id = str(_job_value(job, "id"))

        try:
            await process_job(job)
            mark_job_completed(job_id)

        except Exception as e:
            print("Worker error:", e)
            error = "".join(traceback.format_exception_only(type(e), e))
            mark_job_failed(job_id, error)

        await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(worker_loop())

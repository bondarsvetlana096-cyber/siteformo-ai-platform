import os
import asyncio
import traceback

from sqlalchemy.orm import Session
from sqlalchemy import text

from app.db.session import SessionLocal
from app.models.order import Order
from app.services import generation_service
from app.services.email_service import send_email
from app.services.design_quality_pipeline_service import DesignQualityPipelineService
from app.services.pipeline_result_service import persist_quality_pipeline_result


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
        print("⚠️ No client email found, skipping preview email")
        return

    preview_link = (
        f"{os.getenv('APP_BASE_URL', 'https://siteformo.com').rstrip('/')}"
        f"/design-previews?order_id={order_id}"
    )

    print(f"📧 Sending preview email to {client_email}")

    await send_email(
        to=client_email,
        subject="Your design previews are ready",
        html=f"""
        <div style="font-family:Arial,sans-serif;line-height:1.5;color:#111827;">
            <h2>Your website design previews are ready</h2>

            <p>
                Your project brief has been received and your design options are ready.
            </p>

            <p>
                Please open the link below and choose one design direction from the five screenshots.
            </p>

            <p>
                <a href="{preview_link}"
                   style="padding:12px 20px;background:#4f46e5;color:white;text-decoration:none;border-radius:8px;display:inline-block;font-weight:bold;">
                    View your designs
                </a>
            </p>

            <p>
                After you select one design, your 1-hour refund window starts and full website production begins.
            </p>

            <p>SiteFormo Team</p>
        </div>
        """,
    )

    print("✅ Preview email sent")


async def process_design_previews_job(db: Session, order: Order, order_id: str):
    service = generation_service.GenerationService()

    extended_brief = getattr(order, "extended_brief", None) or {}

    result = service.generate_design_previews_for_order(
        db,
        order,
        extended_brief,
    )

    print("✅ Generated previews")

    quality_pipeline = DesignQualityPipelineService()
    quality_result = quality_pipeline.run_for_order(
        order=order,
        preview_payload=result,
        extended_brief=extended_brief,
    )

    persist_quality_pipeline_result(db, order, quality_result)

    print(
        f"✅ Quality pipeline finished: "
        f"{quality_result.get('status')} score={quality_result.get('average_score')}"
    )

    if quality_result.get("status") != "READY_TO_SEND":
        print("⚠️ Preview email skipped because manual review is required")
        return

    await _send_preview_email(order, order_id)


async def process_final_generation_job(db: Session, order: Order, order_id: str):
    service = generation_service.GenerationService()

    service.start_full_generation_for_order(
        db=db,
        order=order,
        note="Full generation started after client selected design preview.",
    )

    print(f"✅ Final generation finished for order {order_id}")


async def process_job(job):
    order_id = str(job["order_id"])
    job_type = job.get("job_type") or "DESIGN_PREVIEWS"

    print(f"🚀 Processing {job_type} job for order {order_id}")

    db: Session = SessionLocal()

    try:
        order = db.query(Order).filter(Order.id == order_id).first()

        if not order:
            raise Exception("Order not found")

        if job_type == "DESIGN_PREVIEWS":
            await process_design_previews_job(db, order, order_id)

        elif job_type == "FINAL_GENERATION":
            await process_final_generation_job(db, order, order_id)

        else:
            raise Exception(f"Unknown job_type: {job_type}")

    finally:
        db.close()


def claim_next_job():
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
                attempts = attempts + 1,
                updated_at = now()
            where id = :id
        """), {"id": job["id"]})

        db.commit()

        return job

    finally:
        db.close()


def mark_completed(job_id):
    db: Session = SessionLocal()

    try:
        db.execute(text("""
            update generation_jobs
            set status = 'COMPLETED',
                finished_at = now(),
                updated_at = now()
            where id = :id
        """), {"id": job_id})

        db.commit()

    finally:
        db.close()


def mark_failed(job_id, error):
    db: Session = SessionLocal()

    try:
        db.execute(text("""
            update generation_jobs
            set status = case
                when attempts >= max_attempts then 'FAILED'
                else 'PENDING'
            end,
            error = :error,
            updated_at = now()
            where id = :id
        """), {"id": job_id, "error": error[:3000]})

        db.commit()

    finally:
        db.close()


async def worker_loop():
    print("🔥 Worker started")

    while True:
        job = claim_next_job()

        if not job:
            await asyncio.sleep(5)
            continue

        try:
            await process_job(job)
            mark_completed(job["id"])

        except Exception as e:
            print("❌ Worker error:", e)
            error = "".join(traceback.format_exception_only(type(e), e))
            mark_failed(job["id"], error)

        await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(worker_loop())
import asyncio
import traceback

from sqlalchemy.orm import Session
from sqlalchemy import text

from app.db.session import SessionLocal
from app.models.order import Order
from app.services import generation_service
from app.services.email_service import send_email


async def process_job(job):
    order_id = job["order_id"]

    print(f"🚀 Processing job for order {order_id}")

    db: Session = SessionLocal()

    try:
        order = db.query(Order).filter(Order.id == order_id).first()

        if not order:
            raise Exception("Order not found")

        service = generation_service.GenerationService()

        # 🔥 ГЕНЕРАЦИЯ ПРЕВЬЮ
        result = service.generate_design_previews_for_order(
            db,
            order,
            order.extended_brief or {}
        )

        # 💾 СОХРАНЕНИЕ
        order.status = "DESIGN_PREVIEWS_READY"
        order.design_previews = result.get("design_previews", [])
        order.logo_previews = result.get("logo_previews", [])

        db.commit()
        db.refresh(order)

        print("✅ Generated previews")

        # 📧 EMAIL
        client_email = getattr(order.client, "email", None)

        if client_email:
            preview_link = f"https://siteformo.com/design-previews?order_id={order_id}"

            await send_email(
                to=client_email,
                subject="Your design previews are ready",
                html=f"""
                <h2>Your design previews are ready</h2>
                <p>Click below to view and select your design:</p>
                <a href="{preview_link}">View designs</a>
                """
            )

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
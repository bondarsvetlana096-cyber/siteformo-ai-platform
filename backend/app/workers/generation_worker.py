import os
import asyncio
import traceback

from app.services.generation_queue_service import (
    claim_next_generation_job,
    mark_job_completed,
    mark_job_failed,
)
from app.services import generation_service
from app.services.email_service import send_email
from app.models.order import Order
from sqlalchemy.orm import Session
from app.db.session import SessionLocal


async def process_job(job):
    order_id = job["order_id"]

    print(f"Processing job for order {order_id}")

    db: Session = SessionLocal()

    try:
        order = db.query(Order).filter(Order.id == order_id).first()

        if not order:
            raise Exception("Order not found")

        service = generation_service.GenerationService()

        # 🔥 генерация превью
        result = service.generate_design_previews_for_order(
            db, order, order.extended_brief or {}
        )

        # 💾 сохраняем результат
        order.status = "DESIGN_PREVIEWS_READY"
        order.design_previews = result.get("design_previews", [])
        order.logo_previews = result.get("logo_previews", [])

        db.commit()
        db.refresh(order)

        print("Generated previews:", result)

        # 📧 отправляем email клиенту
        client_email = getattr(order.client, "email", None)

        if client_email:
            preview_link = f"{os.getenv('APP_BASE_URL', 'https://siteformo.com').rstrip('/')}/design-previews?order_id={order_id}"

            await send_email(
                to=client_email,
                subject="Your design previews are ready",
                html=f"""
                <h2>Your design previews are ready</h2>
                <p>Click below to view and select your design:</p>
                <a href="{preview_link}">View designs</a>
                """,
            )

    finally:
        db.close()


async def worker_loop():
    print("🚀 Generation worker started")

    while True:
        job = await claim_next_generation_job()

        if not job:
            await asyncio.sleep(5)
            continue

        try:
            await process_job(job)
            await mark_job_completed(job["id"])

        except Exception as e:
            print("Worker error:", e)
            error = "".join(traceback.format_exception_only(type(e), e))
            await mark_job_failed(job["id"], error)

        await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(worker_loop())
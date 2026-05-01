from sqlalchemy.orm import Session
from app.db.session import SessionLocal
from sqlalchemy import text


def create_generation_job(order_id: str, job_type: str = "DESIGN_PREVIEWS"):
    db: Session = SessionLocal()

    try:
        existing = db.execute(
            text("""
                select id
                from generation_jobs
                where order_id = :order_id
                and job_type = :job_type
                and status in ('PENDING', 'PROCESSING', 'COMPLETED')
                limit 1
            """),
            {"order_id": order_id, "job_type": job_type}
        ).fetchone()

        if existing:
            return existing[0]

        result = db.execute(
            text("""
                insert into generation_jobs (order_id, job_type, status)
                values (:order_id, :job_type, 'PENDING')
                returning id
            """),
            {"order_id": order_id, "job_type": job_type}
        ).fetchone()

        db.commit()

        return result[0]

    finally:
        db.close()


def claim_next_generation_job():
    db: Session = SessionLocal()

    try:
        job = db.execute(text("""
            select *
            from generation_jobs
            where status = 'PENDING'
            order by created_at asc
            limit 1
            for update skip locked
        """)).fetchone()

        if not job:
            return None

        db.execute(text("""
            update generation_jobs
            set status = 'PROCESSING',
                attempts = attempts + 1,
                updated_at = now()
            where id = :id
        """), {"id": job.id})

        db.commit()

        return job

    finally:
        db.close()


def mark_job_completed(job_id: str):
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


def mark_job_failed(job_id: str, error: str):
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
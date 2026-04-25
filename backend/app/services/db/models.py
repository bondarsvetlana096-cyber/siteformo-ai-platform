from sqlalchemy import Boolean, Column, DateTime, Integer, JSON, String, Text, func
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class Lead(Base):
    __tablename__ = "leads"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String(255), index=True, nullable=False)
    channel = Column(String(50), nullable=True)
    contact_channel = Column(String(50), nullable=True)
    service = Column(String(255), nullable=True)
    city = Column(String(255), nullable=True)
    urgency = Column(String(255), nullable=True)
    contact = Column(String(255), nullable=True)
    raw_text = Column(Text, nullable=True)
    status = Column(String(50), nullable=True, default="new")
    is_hot = Column(Boolean, nullable=False, default=False)
    followup_stage = Column(Integer, nullable=False, default=0)
    last_contacted = Column(DateTime(timezone=True), nullable=True)
    history = Column(JSON, nullable=False, default=list)
    estimate = Column(JSON, nullable=True)
    offer_url = Column(String(500), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

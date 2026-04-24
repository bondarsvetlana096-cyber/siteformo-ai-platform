from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.guided_flow import GuidedFlowService

router = APIRouter(prefix="/channels", tags=["web-chat"])


class WebChatStartRequest(BaseModel):
    session_id: str | None = None
    reset: bool = False


class WebChatAnswerRequest(BaseModel):
    session_id: str | None = None
    answer: str | None = Field(default=None, description="Selected option value or contact text for the contact step")
    message: str | None = Field(default=None, description="Legacy alias for answer")


@router.post("/web-chat/start")
def web_chat_start(payload: WebChatStartRequest, db: Session = Depends(get_db)):
    return GuidedFlowService.start(db=db, session_id=payload.session_id, reset=payload.reset)


@router.post("/web-chat")
async def web_chat_answer(payload: WebChatAnswerRequest, db: Session = Depends(get_db)):
    if (payload.answer or payload.message) == "restart":
        return GuidedFlowService.start(db=db, session_id=payload.session_id, reset=True)
    try:
        return await GuidedFlowService.answer(db=db, session_id=payload.session_id, answer=payload.answer or payload.message)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/web-chat/message")
async def web_chat_message(payload: WebChatAnswerRequest, db: Session = Depends(get_db)):
    """Backward-compatible alias for the old web-chat route, now guided instead of free chat."""
    return await web_chat_answer(payload, db)

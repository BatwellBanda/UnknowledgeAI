from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.core.rate_limit import limit_by_user
from app.models.user import User
from app.schemas.chat import ChatRequest, ChatResponse
from app.services import chat_service
from database.connection import get_db

router = APIRouter(prefix="/chat", tags=["Assistant"])


@router.post("", response_model=ChatResponse)
def chat(data: ChatRequest, db: Session = Depends(get_db), current_user: User = Depends(limit_by_user("chat", 10, 60))):
    return chat_service.answer(db, data, current_user)

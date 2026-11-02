from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.core.security import get_current_user
from app.models.user import User
from app.schemas.search import SearchRequest, SearchResponse
from app.services import search_service
from database.connection import get_db

router = APIRouter(prefix="/search", tags=["Search"])


@router.post("", response_model=SearchResponse)
def search(data: SearchRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return search_service.search(db, data, current_user)

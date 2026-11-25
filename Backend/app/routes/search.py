from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.core.rate_limit import limit_by_user
from app.models.user import User
from app.schemas.search import SearchRequest, SearchResponse
from app.services import search_service
from database.connection import get_db

router = APIRouter(prefix="/search", tags=["Search"])


@router.post("", response_model=SearchResponse)
def search(data: SearchRequest, db: Session = Depends(get_db), current_user: User = Depends(limit_by_user("search", 60, 60))):
    return search_service.search(db, data, current_user)

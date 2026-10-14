from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from database.connection import get_db
from app.schemas.user import UserRegister, UserLogin, UserResponse, Token
from app.services import auth_service

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post("/register", response_model=UserResponse)
def register(data: UserRegister, db: Session = Depends(get_db)):
    user = auth_service.register_user(db, data)
    return UserResponse(id=user.id, full_name=user.full_name, email=user.email, role=user.role.name)


@router.post("/login", response_model=Token)
def login(data: UserLogin, db: Session = Depends(get_db)):
    token = auth_service.authenticate_user(db, data)
    return Token(access_token=token)
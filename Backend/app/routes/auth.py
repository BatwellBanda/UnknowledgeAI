from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session
from database.connection import get_db
from app.core.rate_limit import client_ip, enforce, limit_by_ip, limit_by_user
from app.core.security import get_current_user
from app.models.user import User
from app.schemas.user import PasswordChange, UserRegister, UserLogin, UserResponse, Token
from app.services import auth_service

router = APIRouter(prefix="/auth", tags=["Authentication"])


# Per-IP ceilings stay generous because a campus network can share one public address.
@router.post("/register", response_model=UserResponse, dependencies=[Depends(limit_by_ip("register", 30, 3600))])
def register(data: UserRegister, db: Session = Depends(get_db)):
    user = auth_service.register_user(db, data)
    return UserResponse(id=user.id, full_name=user.full_name, email=user.email, role=user.role.name)


@router.post("/login", response_model=Token, dependencies=[Depends(limit_by_ip("login", 120, 60))])
def login(data: UserLogin, request: Request, db: Session = Depends(get_db)):
    # Password guessing against one account is limited tightly, per address.
    enforce(("login-account", client_ip(request), data.email.lower()), 10, 900)
    token = auth_service.authenticate_user(db, data)
    return Token(access_token=token)


@router.post("/logout", status_code=204)
def logout(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Sign out on every device by revoking all tokens issued to this account."""
    auth_service.revoke_tokens(db, current_user)
    return Response(status_code=204)


@router.post("/change-password", response_model=Token)
def change_password(data: PasswordChange, db: Session = Depends(get_db), current_user: User = Depends(limit_by_user("password", 5, 900))):
    return Token(access_token=auth_service.change_password(db, current_user, data))

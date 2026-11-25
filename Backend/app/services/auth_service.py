from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from fastapi import HTTPException, status
from app.models.user import User
from app.models.role import Role
from app.core.security import burn_password_check, hash_password, verify_password, create_access_token
from app.schemas.user import PasswordChange, UserRegister, UserLogin

def register_user(db: Session, data: UserRegister) -> User:
    if data.role != "STUDENT":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Public registration only supports STUDENT accounts")
    existing_user = db.query(User).filter(User.email == data.email).first()
    if existing_user:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already registered")
    
    role = db.query(Role).filter(Role.name == "STUDENT").first()
    if not role:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Registration is temporarily unavailable")
    
    new_user = User(
        full_name=data.full_name,
        email=data.email,
        hashed_password=hash_password(data.password),
        role_id=role.id,
    )
    db.add(new_user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        if db.query(User).filter(User.email == data.email).first():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already registered")
        raise
    db.refresh(new_user)
    return new_user

def authenticate_user(db: Session, data: UserLogin) -> str:
    user = db.query(User).filter(User.email == data.email).first()
    if user is None:
        burn_password_check(data.password)
    if not user or not verify_password(data.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    return issue_token(user)


def issue_token(user: User) -> str:
    return create_access_token({"sub": str(user.id), "role": user.role.name, "ver": user.token_version or 0})


def revoke_tokens(db: Session, user: User) -> None:
    """Invalidate every token issued to this user, on all devices."""
    user.token_version = (user.token_version or 0) + 1
    db.commit()


def change_password(db: Session, user: User, data: PasswordChange) -> str:
    if not verify_password(data.current_password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Current password is incorrect")
    user.hashed_password = hash_password(data.new_password)
    revoke_tokens(db, user)
    return issue_token(user)

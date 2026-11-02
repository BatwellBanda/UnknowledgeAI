import uuid
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.core.security import get_current_user, require_role
from app.models.role import Role
from app.models.user import User
from app.schemas.user import RoleChange, UserResponse
from database.connection import get_db

router = APIRouter(prefix="/users", tags=["Users"])


def user_view(user: User) -> UserResponse:
    return UserResponse(id=user.id, full_name=user.full_name, email=user.email, role=user.role.name)


@router.get("/me", response_model=UserResponse)
def me(current_user: User = Depends(get_current_user)):
    return user_view(current_user)


@router.patch("/{user_id}/role", response_model=UserResponse)
def change_role(
    user_id: uuid.UUID, data: RoleChange, db: Session = Depends(get_db),
    admin: User = Depends(require_role("ADMIN")),
):
    if user_id == admin.id:
        raise HTTPException(status_code=400, detail="Admins cannot change their own role")
    user = db.get(User, user_id)
    role = db.query(Role).filter(Role.name == data.role).first()
    if user is None or role is None:
        raise HTTPException(status_code=404, detail="User not found")
    user.role_id = role.id
    db.commit()
    db.refresh(user)
    return user_view(user)

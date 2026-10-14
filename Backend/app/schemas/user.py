import uuid
from typing import Literal
from pydantic import BaseModel, EmailStr, Field, field_validator

class UserRegister(BaseModel):
    full_name: str = Field(min_length=1, max_length=150)
    email: EmailStr = Field(max_length=150)
    password: str = Field(min_length=12, max_length=72, repr=False)
    role: Literal["STUDENT"] = "STUDENT"

    @field_validator("full_name", mode="before")
    @classmethod
    def strip_name(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator("role", mode="before")
    @classmethod
    def normalise_role(cls, value):
        return value.upper() if isinstance(value, str) else value

    @field_validator("password")
    @classmethod
    def check_password_bytes(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 72:
            raise ValueError("Password must be at most 72 UTF-8 bytes")
        return value

class UserLogin(BaseModel):
    email: EmailStr
    password: str = Field(max_length=4096, repr=False)

class UserResponse(BaseModel):
    id: uuid.UUID
    full_name: str
    email: EmailStr
    role: str

    class Config:
        from_attributes = True

class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"

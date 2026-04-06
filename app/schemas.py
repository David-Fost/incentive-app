from pydantic import BaseModel
from typing import Optional

class UserCreate(BaseModel):
    username: str
    password: str
    full_name: Optional[str] = None
    department: str
    role: str = "head"  # head | admin

class UserResponse(BaseModel):
    id: int
    username: str
    full_name: Optional[str]
    department: str
    role: str
    is_active: bool

    class Config:
        from_attributes = True  # Для совместимости с SQLAlchemy (Pydantic V2)

class Token(BaseModel):
    access_token: str
    token_type: str
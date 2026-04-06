# app/schemas.py
from pydantic import BaseModel, ConfigDict
from typing import Optional
from datetime import datetime
from decimal import Decimal

# =========== USERS ===========
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
    model_config = ConfigDict(from_attributes=True)

class Token(BaseModel):
    access_token: str
    token_type: str

# =========== TOPICS ===========
class TopicCreate(BaseModel):
    title: str
    code: Optional[str] = None
    period_year: int
    period_month: int
    department: str

class TopicResponse(TopicCreate):
    id: int
    is_active: bool
    model_config = ConfigDict(from_attributes=True)

# =========== REPORTS ===========
class ReportCreate(BaseModel):
    user_id: int
    topic_id: int
    period_year: int
    period_month: int
    publications_count: int = 0
    doi_list: Optional[str] = None
    additional_notes: Optional[str] = None

class ReportUpdate(BaseModel):
    publications_count: Optional[int] = None
    doi_list: Optional[str] = None
    additional_notes: Optional[str] = None

class ReportResponse(BaseModel):
    id: int
    user_id: int
    topic_id: int
    period_year: int
    period_month: int
    publications_count: int
    doi_list: Optional[str]
    additional_notes: Optional[str]
    nir_score: Optional[Decimal]
    additional_score: Optional[Decimal]
    total_score: Optional[Decimal]
    status: str
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)
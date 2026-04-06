# app/schemas.py
from pydantic import BaseModel, ConfigDict
from typing import Optional, List
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

# =========== REPORTS (LEGACY / СТАРАЯ СТРУКТУРА) ===========
# ⚠️ Оставлены для совместимости. Новые отчёты используют MonthlyReportCreate/Response ниже.
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

# =========== КАЛЕНДАРЬ & ТАБЕЛЬ (ШАГ 33) ===========
class WorkingDaysCreate(BaseModel):
    year: int
    month: int
    total_days: int

class WorkingDaysResponse(WorkingDaysCreate):
    id: int
    model_config = ConfigDict(from_attributes=True)


class AttendanceCreate(BaseModel):
    employee_id: int
    year: int
    month: int
    working_days: int

class AttendanceResponse(AttendanceCreate):
    id: int
    model_config = ConfigDict(from_attributes=True)


class ServiceActivityCreate(BaseModel):
    employee_id: int
    year: int
    month: int
    criterion_name: str
    criterion_weight: float
    quantity: float = 1.0
    notes: Optional[str] = None

class ServiceActivityResponse(ServiceActivityCreate):
    id: int
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)

# =============================================================================
# 🔹 НОВЫЕ СХЕМЫ ОТЧЁТОВ (ШАГ 35)
# =============================================================================
class ReportEntryCreate(BaseModel):
    topic_id: int
    employee_id: int
    work_title: Optional[str] = None
    doi_or_link: Optional[str] = None
    publications_count: int = 0
    points_earned: float = 0.0
    notes: Optional[str] = None

class ReportEntryResponse(ReportEntryCreate):
    id: int
    report_id: int
    model_config = ConfigDict(from_attributes=True)


class MonthlyReportCreate(BaseModel):
    lab_head_id: int
    department: str
    year: int
    month: int
    entries: List[ReportEntryCreate] = []  # Список строк отчёта

class MonthlyReportResponse(BaseModel):
    id: int
    lab_head_id: int
    department: str
    year: int
    month: int
    status: str
    entries: List[ReportEntryResponse] = []
    model_config = ConfigDict(from_attributes=True)
# app/main.py
from fastapi import FastAPI, Depends, HTTPException, status, Query
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from datetime import timedelta
from decimal import Decimal
from typing import List, Optional

from .calculations import calculate_nir_score_raw, normalize_by_working_days, apply_additional_cap, calculate_final_total
from .database import get_db
# 🔹 ДОБАВЛЕНО: новые модели для Шаг 34
from .models import User, ResearchTopic, MonthlyReport, Attendance, WorkingDaysCalendar
from .schemas import (
    UserCreate, UserResponse, Token,
    TopicCreate, TopicResponse,
    ReportCreate, ReportUpdate, ReportResponse,
    # 🔹 ДОБАВЛЕНО: новые схемы для Шаг 34
    AttendanceCreate, AttendanceResponse,
    WorkingDaysCreate, WorkingDaysResponse
)
from .auth import (
    get_password_hash, verify_password, create_access_token, get_current_user,
    ACCESS_TOKEN_EXPIRE_MINUTES
)

app = FastAPI(title="Система расчёта СН", version="0.3.0")

# ================= AUTH =================
@app.post("/token", response_model=Token)
def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db)
):
    user = db.query(User).filter(User.username == form_data.username).first()
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Неверный логин или пароль")
    if not user.is_active:
        raise HTTPException(status_code=400, detail="Пользователь отключён")
    return {
        "access_token": create_access_token(
            data={"sub": user.username, "role": user.role},
            expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
        ),
        "token_type": "bearer"
    }

@app.post("/users/", response_model=UserResponse, status_code=201)
def create_user(
    user: UserCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    if current_user.role != "admin":
        raise HTTPException(403, "Только администратор может создавать пользователей")
    if db.query(User).filter(User.username == user.username).first():
        raise HTTPException(400, "Пользователь уже существует")
    new_user = User(
        username=user.username,
        hashed_password=get_password_hash(user.password),
        full_name=user.full_name,
        department=user.department,
        role=user.role
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return new_user

@app.get("/users/me", response_model=UserResponse)
def read_me(current_user: User = Depends(get_current_user)):
    return current_user

# ================= TOPICS =================
@app.post("/topics/", response_model=TopicResponse, status_code=201)
def create_topic(
    topic: TopicCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    if current_user.role != "admin" and topic.department != current_user.department:
        raise HTTPException(403, "Нельзя создавать темы для чужого подразделения")
    db_topic = ResearchTopic(**topic.model_dump())
    db.add(db_topic)
    db.commit()
    db.refresh(db_topic)
    return db_topic

@app.get("/topics/", response_model=List[TopicResponse])
def list_topics(
    year: Optional[int] = Query(None),
    month: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    query = db.query(ResearchTopic).filter(ResearchTopic.is_active == True)
    if current_user.role != "admin":
        query = query.filter(ResearchTopic.department == current_user.department)
    if year:
        query = query.filter(ResearchTopic.period_year == year)
    if month:
        query = query.filter(ResearchTopic.period_month == month)
    return query.all()

# ================= REPORTS =================
# ⚠️ ВРЕМЕННО ЗАКОММЕНТИРОВАНО ДО ШАГА 35
# Модель MonthlyReport изменилась. Старые поля (user_id, topic_id, period_year...) 
# теперь находятся в ReportEntry. Эти маршруты вызовут ошибку импорта, пока мы их не перепишем.
"""
@app.post("/reports/", response_model=ReportResponse, status_code=201)
def create_report(...): ...

@app.get("/reports/", response_model=List[ReportResponse])
def list_reports(...): ...

@app.patch("/reports/{report_id}/finalize", response_model=ReportResponse)
def finalize_report(...): ...

@app.patch("/reports/{report_id}/approve", response_model=ReportResponse)
def approve_report(...): ...
"""

# ================= ТАБЕЛЬ & КАЛЕНДАРЬ (ШАГ 34) =================
@app.post("/attendance/", response_model=AttendanceResponse, status_code=201)
def set_attendance(
    data: AttendanceCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Создать или обновить табель сотрудника за месяц (upsert)"""
    existing = db.query(Attendance).filter(
        Attendance.employee_id == data.employee_id,
        Attendance.year == data.year,
        Attendance.month == data.month
    ).first()
    
    if existing:
        for k, v in data.model_dump().items():
            setattr(existing, k, v)
        db.commit()
        db.refresh(existing)
        return existing
        
    record = Attendance(**data.model_dump())
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


@app.post("/calendar/", response_model=WorkingDaysResponse, status_code=201)
def set_working_days(
    data: WorkingDaysCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Установить норму рабочих дней в месяце (только admin)"""
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Только администратор может управлять календарём")
        
    existing = db.query(WorkingDaysCalendar).filter(
        WorkingDaysCalendar.year == data.year,
        WorkingDaysCalendar.month == data.month
    ).first()
    
    if existing:
        existing.total_days = data.total_days
        db.commit()
        db.refresh(existing)
        return existing
        
    record = WorkingDaysCalendar(**data.model_dump())
    db.add(record)
    db.commit()
    db.refresh(record)
    return record
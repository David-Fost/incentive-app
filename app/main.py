# app/main.py
from fastapi import FastAPI, Depends, HTTPException, status, Query
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from datetime import timedelta
from decimal import Decimal
from typing import List, Optional
from .calculations import calculate_nir_score_raw, normalize_by_working_days, apply_additional_cap, calculate_final_total
from .database import get_db
from .models import User, ResearchTopic, MonthlyReport
from .schemas import (
    UserCreate, UserResponse, Token,
    TopicCreate, TopicResponse,
    ReportCreate, ReportUpdate, ReportResponse
)
from .auth import (
    get_password_hash, verify_password, create_access_token, get_current_user,
    ACCESS_TOKEN_EXPIRE_MINUTES
)

app = FastAPI(title="Система расчёта СН", version="0.2.0")

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
@app.post("/reports/", response_model=ReportResponse, status_code=201)
def create_report(
    report: ReportCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    if current_user.role != "admin":
        topic = db.query(ResearchTopic).filter(
            ResearchTopic.id == report.topic_id,
            ResearchTopic.department == current_user.department
        ).first()
        if not topic:
            raise HTTPException(403, "Тема не найдена или не относится вашему подразделению")
        user = db.query(User).filter(User.id == report.user_id).first()
        if user and user.department != current_user.department:
            raise HTTPException(403, "Нельзя создавать отчёт для сотрудника чужого отдела")

    exists = db.query(MonthlyReport).filter(
        MonthlyReport.user_id == report.user_id,
        MonthlyReport.topic_id == report.topic_id,
        MonthlyReport.period_year == report.period_year,
        MonthlyReport.period_month == report.period_month
    ).first()
    if exists:
        raise HTTPException(400, "Отчёт за этот период по данной теме уже существует")

    db_report = MonthlyReport(**report.model_dump())
    db.add(db_report)
    db.commit()
    db.refresh(db_report)
    return db_report

@app.get("/reports/", response_model=List[ReportResponse])
def list_reports(
    year: Optional[int] = Query(None),
    month: Optional[int] = Query(None),
    status_filter: Optional[str] = Query(None, alias="status"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    query = db.query(MonthlyReport)
    if current_user.role != "admin":
        query = query.join(User, MonthlyReport.user_id == User.id).filter(User.department == current_user.department)
    if year:
        query = query.filter(MonthlyReport.period_year == year)
    if month:
        query = query.filter(MonthlyReport.period_month == month)
    if status_filter:
        query = query.filter(MonthlyReport.status == status_filter)
    return query.all()


@app.patch("/reports/{report_id}/finalize", response_model=ReportResponse)
def finalize_report(
    report_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    report = db.query(MonthlyReport).filter(MonthlyReport.id == report_id).first()
    if not report:
        raise HTTPException(404, "Отчёт не найден")
    if report.status != "draft":
        raise HTTPException(400, f"Отчёт уже в статусе {report.status}")
    if current_user.role != "admin":
        user = db.query(User).filter(User.id == report.user_id).first()
        if user.department != current_user.department:
            raise HTTPException(403, "Нет доступа к этому отчёту")

    # 🔹 РЕАЛЬНЫЙ РАСЧЁТ ПО ФОРМУЛАМ ИЗ ПЛАНА 🔹
    
    # 1. Блок 1: Сырой балл за НИР (берём публикации из отчёта)
    # TODO: Позже подключим запросы к БД для RID, НМД и реального кол-ва соавторов
    nir_raw = calculate_nir_score_raw(
        publications_count=report.publications_count or 0,
        rid_count=0,        # Пока заглушка
        nmd_count=0,        # Пока заглушка
        coauthors_count=3   # Пока m=1 (≤3 соавтора)
    )
    
    # 2. Нормировка на рабочие дни
    # TODO: Позже заменим на запрос к таблице attendance (табель)
    worked_days = 22        # Заглушка: полная занятость
    month_total_days = 22   # Заглушка: норма месяца
    nir_normalized = normalize_by_working_days(nir_raw, worked_days, month_total_days)
    
    # 3. Блоки 2+3: Дополнительные баллы
    # TODO: Позже подключим service_activities + ref_center_activities
    additional_scores = [Decimal("0")]  # Пока заглушка
    additional_total = apply_additional_cap(additional_scores)
    
    # 4. Итоговый балл (cap основной части + доп.)
    report.nir_score = min(nir_normalized, Decimal("5"))  # Cap основной части
    report.additional_score = additional_total
    report.total_score = calculate_final_total(nir_normalized, additional_total)
    
    report.status = "finalized"
    db.commit()
    db.refresh(report)
    return report

@app.patch("/reports/{report_id}/approve", response_model=ReportResponse)
def approve_report(
    report_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    if current_user.role != "admin":
        raise HTTPException(403, "Только администратор может утверждать отчёты")
    report = db.query(MonthlyReport).filter(MonthlyReport.id == report_id).first()
    if not report:
        raise HTTPException(404, "Отчёт не найден")
    if report.status != "finalized":
        raise HTTPException(400, "Можно утвердить только отчёт в статусе 'finalized'")
    report.status = "approved"
    db.commit()
    db.refresh(report)
    return report
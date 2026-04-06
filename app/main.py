# app/main.py
from fastapi import FastAPI, Depends, HTTPException, status, Query
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session, joinedload
from datetime import timedelta
from decimal import Decimal
from typing import List, Optional

from .calculations import calculate_nir_score_raw, normalize_by_working_days, apply_additional_cap, calculate_final_total
from .database import get_db
from .models import User, ResearchTopic, MonthlyReport, ReportEntry, Attendance, WorkingDaysCalendar, ServiceActivity
from .schemas import (
    UserCreate, UserResponse, Token,
    TopicCreate, TopicResponse,
    MonthlyReportCreate, MonthlyReportResponse, ReportEntryCreate, ReportEntryResponse,
    AttendanceCreate, AttendanceResponse,
    WorkingDaysCreate, WorkingDaysResponse
)
from .auth import (
    get_password_hash, verify_password, create_access_token, get_current_user,
    ACCESS_TOKEN_EXPIRE_MINUTES
)

app = FastAPI(title="Система расчёта СН", version="0.4.0")

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

# ================= REPORTS (НОВАЯ СТРУКТУРА) =================
@app.post("/reports/", response_model=MonthlyReportResponse, status_code=201)
def create_report(
    report_data: MonthlyReportCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    if current_user.role != "admin" and report_data.department != current_user.department:
        raise HTTPException(403, "Доступ только к отчётам своего подразделения")

    # Проверка на дубликат заголовка
    exists = db.query(MonthlyReport).filter(
        MonthlyReport.lab_head_id == report_data.lab_head_id,
        MonthlyReport.year == report_data.year,
        MonthlyReport.month == report_data.month
    ).first()
    if exists:
        raise HTTPException(400, "Отчёт за этот период у данного завлаба уже существует")

    # Создаём заголовок
    new_report = MonthlyReport(
        lab_head_id=report_data.lab_head_id,
        department=report_data.department,
        year=report_data.year,
        month=report_data.month,
        status="draft"
    )
    db.add(new_report)
    db.flush()  # Получаем ID заголовка до коммита

    # Добавляем строки отчёта
    for entry_data in report_data.entries:
        entry = ReportEntry(
            report_id=new_report.id,
            **entry_data.model_dump()
        )
        db.add(entry)

    db.commit()
    db.refresh(new_report)
    return new_report


@app.get("/reports/", response_model=List[MonthlyReportResponse])
def list_reports(
    year: Optional[int] = Query(None),
    month: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    query = db.query(MonthlyReport).options(joinedload(MonthlyReport.entries))
    if current_user.role != "admin":
        query = query.filter(MonthlyReport.department == current_user.department)
    if year:
        query = query.filter(MonthlyReport.year == year)
    if month:
        query = query.filter(MonthlyReport.month == month)
    return query.all()


@app.patch("/reports/{report_id}/finalize", response_model=MonthlyReportResponse)
def finalize_report(
    report_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    report = db.query(MonthlyReport).options(joinedload(MonthlyReport.entries)).filter(MonthlyReport.id == report_id).first()
    if not report:
        raise HTTPException(404, "Отчёт не найден")
    if report.status != "draft":
        raise HTTPException(400, f"Отчёт уже в статусе {report.status}")
    if current_user.role != "admin" and report.department != current_user.department:
        raise HTTPException(403, "Нет доступа к этому отчёту")

    # 🔹 1. Получаем реальные данные из БД вместо заглушек
    attendance = db.query(Attendance).filter(
        Attendance.employee_id == report.lab_head_id,
        Attendance.year == report.year,
        Attendance.month == report.month
    ).first()
    worked_days = attendance.working_days if attendance else 22  # fallback

    calendar = db.query(WorkingDaysCalendar).filter(
        WorkingDaysCalendar.year == report.year,
        WorkingDaysCalendar.month == report.month
    ).first()
    month_total_days = calendar.total_days if calendar else 22  # fallback

    service_entries = db.query(ServiceActivity).filter(
        ServiceActivity.employee_id == report.lab_head_id,
        ServiceActivity.year == report.year,
        ServiceActivity.month == report.month
    ).all()
    service_total = sum(Decimal(str(e.quantity)) * Decimal(str(e.criterion_weight)) for e in service_entries)
    additional_total = apply_additional_cap([service_total])

    # 🔹 2. Считаем баллы для каждой строки отчёта
    for entry in report.entries:
        nir_raw = calculate_nir_score_raw(
            publications_count=entry.publications_count or 0,
            rid_count=0,        # TODO: подключить запрос к БД RID
            nmd_count=0,        # TODO: подключить запрос к БД НМД
            coauthors_count=3   # TODO: рассчитать динамически
        )
        nir_normalized = normalize_by_working_days(nir_raw, worked_days, month_total_days)
        # Cap основной части = 5.0
        entry.points_earned = float(min(nir_normalized, Decimal("5.0")))

    report.status = "finalized"
    db.commit()
    db.refresh(report)
    return report


@app.patch("/reports/{report_id}/approve", response_model=MonthlyReportResponse)
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


# ================= ТАБЕЛЬ & КАЛЕНДАРЬ =================
@app.post("/attendance/", response_model=AttendanceResponse, status_code=201)
def set_attendance(
    data: AttendanceCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
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
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Только администратор")
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
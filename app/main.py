# app/main.py
from fastapi import FastAPI, Depends, HTTPException, status, Query, Request, Form, Response
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session, joinedload
from datetime import timedelta, datetime, date
from decimal import Decimal
from typing import List, Optional
from types import SimpleNamespace

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

# ================= ИНИЦИАЛИЗАЦИЯ =================
app = FastAPI(title="Система расчёта СН", version="0.5.0")
templates = Jinja2Templates(directory="app/templates")
# Раскомментируйте, когда добавите папку app/static/
# app.mount("/static", StaticFiles(directory="app/static"), name="static")


# ================= AUTH & API (БЕЗ ИЗМЕНЕНИЙ) =================
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

@app.post("/reports/", response_model=MonthlyReportResponse, status_code=201)
def create_report(
    report_data: MonthlyReportCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    if current_user.role != "admin" and report_data.department != current_user.department:
        raise HTTPException(403, "Доступ только к отчётам своего подразделения")
    exists = db.query(MonthlyReport).filter(
        MonthlyReport.lab_head_id == report_data.lab_head_id,
        MonthlyReport.year == report_data.year,
        MonthlyReport.month == report_data.month
    ).first()
    if exists:
        raise HTTPException(400, "Отчёт за этот период у данного завлаба уже существует")
    new_report = MonthlyReport(
        lab_head_id=report_data.lab_head_id,
        department=report_data.department,
        year=report_data.year,
        month=report_data.month,
        status="draft"
    )
    db.add(new_report)
    db.flush()
    for entry_data in report_data.entries:
        entry = ReportEntry(report_id=new_report.id, **entry_data.model_dump())
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

    attendance = db.query(Attendance).filter(
        Attendance.employee_id == report.lab_head_id,
        Attendance.year == report.year,
        Attendance.month == report.month
    ).first()
    worked_days = attendance.working_days if attendance else 22
    calendar = db.query(WorkingDaysCalendar).filter(
        WorkingDaysCalendar.year == report.year,
        WorkingDaysCalendar.month == report.month
    ).first()
    month_total_days = calendar.total_days if calendar else 22
    service_entries = db.query(ServiceActivity).filter(
        ServiceActivity.employee_id == report.lab_head_id,
        ServiceActivity.year == report.year,
        ServiceActivity.month == report.month
    ).all()
    service_total = sum(Decimal(str(e.quantity)) * Decimal(str(e.criterion_weight)) for e in service_entries)
    additional_total = apply_additional_cap([service_total])

    for entry in report.entries:
        nir_raw = calculate_nir_score_raw(
            publications_count=entry.publications_count or 0,
            rid_count=0, nmd_count=0, coauthors_count=3
        )
        nir_normalized = normalize_by_working_days(nir_raw, worked_days, month_total_days)
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


# ================= WEB UI (НОВЫЕ ШАБЛОНЫ) =================
@app.get("/", response_class=HTMLResponse)
def web_index(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse("index.html", {
        "request": request,
        "employees_count": db.query(User).count(),
        "topics_count": db.query(ResearchTopic).count(),
        "reports_count": db.query(MonthlyReport).count(),
        "attendance_count": db.query(Attendance).count(),
        "recent_reports": db.query(MonthlyReport).order_by(MonthlyReport.id.desc()).limit(5).all(),
    })

@app.get("/topics", response_class=HTMLResponse)
def web_topics(request: Request, db: Session = Depends(get_db)):
    topics = db.query(ResearchTopic).order_by(ResearchTopic.id.desc()).all()
    users = db.query(User).order_by(User.full_name).all()  # 🔹 Нужно для выпадающих списков
    return templates.TemplateResponse("topics.html", {
        "request": request,
        "topics": topics,
        "users": users,  # 🔹 Передаём в шаблон
        "current_year": datetime.now().year,
        "current_month": datetime.now().month,
    })

@app.post("/topics", response_class=HTMLResponse)
async def web_topics_post(
    request: Request,
    title: str = Form(...),
    code: Optional[str] = Form(None),
    head_id: Optional[int] = Form(None),
    responsible_id: Optional[int] = Form(None),
    date_start: Optional[date] = Form(None),
    date_end: Optional[date] = Form(None),
    year: int = Form(...),
    month: int = Form(...),
    department: str = Form(...),
    executor_ids: List[int] = Form(default_factory=list),  # 🔹 Для множественного выбора
    db: Session = Depends(get_db),
    response: Response = None
):
    # 1. Создаём объект темы
    new_topic = ResearchTopic(
        title=title, code=code,
        period_year=year, period_month=month,
        department=department,
        head_id=head_id,
        responsible_id=responsible_id,
        date_start=date_start,
        date_end=date_end
    )
    db.add(new_topic)
    db.flush()  # Получаем new_topic.id до фиксации транзакции

    # 2. Привязываем исполнителей (Many-to-Many)
    if executor_ids:
        executors = db.query(User).filter(User.id.in_(executor_ids)).all()
        new_topic.executors = executors

    # 3. Фиксируем всё одной транзакцией
    db.commit()
    response.headers["X-Toast-Success"] = "Тема успешно добавлена"
    return RedirectResponse(url="/topics", status_code=303)

@app.get("/calendar", response_class=HTMLResponse)
def web_calendar(request: Request, db: Session = Depends(get_db)):
    norms = db.query(WorkingDaysCalendar).order_by(WorkingDaysCalendar.year.desc(), WorkingDaysCalendar.month.desc()).all()
    return templates.TemplateResponse("calendar.html", {
        "request": request,
        "norms": norms,
        "current_year": datetime.now().year,
        "current_month": datetime.now().month,
    })

@app.post("/calendar", response_class=HTMLResponse)
async def web_calendar_post(
    request: Request,
    year: int = Form(...),
    month: int = Form(...),
    work_days_norm: int = Form(...),
    db: Session = Depends(get_db),
    response: Response = None
):
    existing = db.query(WorkingDaysCalendar).filter_by(year=year, month=month).first()
    if existing:
        existing.total_days = work_days_norm
    else:
        db.add(WorkingDaysCalendar(year=year, month=month, total_days=work_days_norm))
    db.commit()
    response.headers["X-Toast-Success"] = "Норма сохранена"
    return RedirectResponse(url="/calendar", status_code=303)

@app.get("/attendance", response_class=HTMLResponse)
def web_attendance(request: Request, db: Session = Depends(get_db)):
    records_db = db.query(Attendance).order_by(Attendance.year.desc(), Attendance.month.desc()).all()
    employees = db.query(User).order_by(User.full_name).all()
    
    # Готовим данные под шаблон (расчёт коэффициента на лету)
    records = []
    for rec in records_db:
        norm = db.query(WorkingDaysCalendar).filter_by(year=rec.year, month=rec.month).first()
        coeff = round(rec.working_days / norm.total_days, 2) if norm else None
        records.append(SimpleNamespace(
            employee=SimpleNamespace(full_name=rec.employee.full_name if rec.employee else "—"),
            year=rec.year,
            month=rec.month,
            days_worked=rec.working_days,
            coefficient=coeff
        ))
        
    years = sorted(set(r.year for r in records), reverse=True)
    return templates.TemplateResponse("attendance.html", {
        "request": request,
        "records": records,
        "employees": employees,
        "available_years": years,
        "current_year": datetime.now().year,
        "current_month": datetime.now().month,
    })

@app.post("/attendance", response_class=HTMLResponse)
async def web_attendance_post(
    request: Request,
    employee_id: int = Form(...),
    year: int = Form(...),
    month: int = Form(...),
    days_worked: float = Form(...),
    db: Session = Depends(get_db),
    response: Response = None
):
    existing = db.query(Attendance).filter_by(employee_id=employee_id, year=year, month=month).first()
    if existing:
        existing.working_days = int(days_worked)
    else:
        db.add(Attendance(employee_id=employee_id, year=year, month=month, working_days=int(days_worked)))
    db.commit()
    response.headers["X-Toast-Success"] = "Запись в табель добавлена"
    return RedirectResponse(url="/attendance", status_code=303)

@app.get("/reports", response_class=HTMLResponse)
def web_reports(request: Request, db: Session = Depends(get_db)):
    reports_db = db.query(MonthlyReport).options(joinedload(MonthlyReport.entries)).order_by(MonthlyReport.id.desc()).all()
    employees = db.query(User).order_by(User.full_name).all()
    topics = db.query(ResearchTopic).all()
    
    # Адаптация под шаблон
    reports = []
    for r in reports_db:
        head = db.query(User).filter_by(id=r.lab_head_id).first()
        reports.append(SimpleNamespace(
            id=r.id,
            head_of_lab=head.full_name if head else "Не назначен",
            month=r.month,
            year=r.year,
            total_fund=None, # Заглушка, можно рассчитать позже
            status=r.status,
            entries=r.entries
        ))
        
    return templates.TemplateResponse("reports.html", {
        "request": request,
        "reports": reports,
        "employees": employees,
        "topics": topics,
        "current_year": datetime.now().year,
        "current_month": datetime.now().month,
    })
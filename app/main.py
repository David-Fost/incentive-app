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
import secrets  # ← Для генерации CSRF-токенов

from .calculations import calculate_nir_score_raw, normalize_by_working_days, apply_additional_cap, calculate_final_total
from .database import get_db
from .models import (
    User, ResearchTopic, MonthlyReport, ReportEntry, 
    Attendance, WorkingDaysCalendar, ServiceActivity,
    Publication, PublicationPlan
)
from .schemas import (
    UserCreate, UserResponse, Token,
    TopicCreate, TopicResponse,
    MonthlyReportCreate, MonthlyReportResponse, ReportEntryCreate, ReportEntryResponse,
    AttendanceCreate, AttendanceResponse,
    WorkingDaysCreate, WorkingDaysResponse,
    PublicationResponse, PublicationPlanCreate, PublicationPlanResponse
)
from .auth import (
    get_password_hash, verify_password, create_access_token, get_current_user,
    ACCESS_TOKEN_EXPIRE_MINUTES
)

# ================= ИНИЦИАЛИЗАЦИЯ =================
app = FastAPI(title="Система расчёта СН", version="0.6.1")
templates = Jinja2Templates(directory="app/templates")
# app.mount("/static", StaticFiles(directory="app/static"), name="static")


# ================= КОНТЕКСТ ДЛЯ ШАБЛОНОВ =================
def get_base_context(request: Request, db: Session = None) -> dict:
    """
    Базовый контекст для всех шаблонов.
    Передаёт: current_user, csrf_token, current_date, active_page
    """
    # Получаем текущего пользователя (из зависимости или request.state)
    current_user = getattr(request.state, "user", None)
    
    # Определяем активную страницу для подсветки навигации
    path = request.url.path.strip('/').split('?')[0]
    active_page = path if path else 'home'
    
    # Форматируем дату на русском
    current_date = datetime.now().strftime("%A, %d %B %Y")
    
    return {
        "request": request,
        "current_user": current_user,
        "csrf_token": secrets.token_urlsafe(32),
        "current_date": current_date,
        "active_page": active_page,
    }


# ================= AUTH & API =================
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


# ================= ПУБЛИКАЦИИ — ЭНДПОИНТЫ =================
@app.get("/api/employees/{employee_id}/publications", response_model=List[PublicationResponse])
def get_employee_publications_json(
    employee_id: int,
    year: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    query = db.query(Publication).join(Publication.authors_list).filter(User.id == employee_id)
    if year:
        query = query.filter(Publication.year == year)
    return query.all()

@app.get("/api/employees/{employee_id}/publications/planned", response_model=List[PublicationPlanResponse])
def get_planned_publications(
    employee_id: int,
    year: int = Query(...),
    month: int = Query(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    plans = db.query(PublicationPlan).join(Publication).join(User).filter(
        PublicationPlan.employee_id == employee_id,
        PublicationPlan.year == year,
        PublicationPlan.month == month
    ).all()
    return plans

@app.post("/api/publication-plans/", response_model=PublicationPlanResponse, status_code=201)
def create_publication_plan(
    plan: PublicationPlanCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    if current_user.role != "admin":
        employee = db.query(User).filter(User.id == plan.employee_id).first()
        if employee and employee.department != current_user.department:
            raise HTTPException(403, "Нет доступа к этому сотруднику")
    exists = db.query(PublicationPlan).filter(
        PublicationPlan.publication_id == plan.publication_id,
        PublicationPlan.employee_id == plan.employee_id,
        PublicationPlan.year == plan.year,
        PublicationPlan.month == plan.month
    ).first()
    if exists:
        raise HTTPException(400, "Эта публикация уже в плане за этот месяц")
    new_plan = PublicationPlan(**plan.model_dump())
    db.add(new_plan)
    db.commit()
    db.refresh(new_plan)
    return new_plan

@app.patch("/api/publication-plans/{plan_id}/toggle-paid")
def toggle_plan_paid(
    plan_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    plan = db.query(PublicationPlan).filter(PublicationPlan.id == plan_id).first()
    if not plan:
        raise HTTPException(404, "План не найден")
    if current_user.role != "admin":
        employee = db.query(User).filter(User.id == plan.employee_id).first()
        if employee and employee.department != current_user.department:
            raise HTTPException(403, "Нет доступа")
    plan.is_paid = not plan.is_paid
    db.commit()
    db.refresh(plan)
    return plan

@app.get("/web/employees/{employee_id}/publications", response_class=HTMLResponse)
def get_employee_publications_html(
    employee_id: int,
    year: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    request: Request = None
):
    query = db.query(Publication).join(Publication.authors_list).filter(User.id == employee_id)
    if year:
        query = query.filter(Publication.year == year)
    publications = query.all()
    return templates.TemplateResponse("partials/pub_list.html", {
        **get_base_context(request, db),
        "publications": publications,
        "current_year": year or datetime.now().year,
        "current_month": datetime.now().month,
    })


# ================= WEB UI =================
@app.get("/", response_class=HTMLResponse)
def web_index(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse("index.html", {
        **get_base_context(request, db),
        "employees_count": db.query(User).count(),
        "topics_count": db.query(ResearchTopic).count(),
        "reports_count": db.query(MonthlyReport).count(),
        "attendance_count": db.query(Attendance).count(),
        "recent_reports": db.query(MonthlyReport).order_by(MonthlyReport.id.desc()).limit(5).all(),
    })

@app.get("/topics", response_class=HTMLResponse)
def web_topics(request: Request, db: Session = Depends(get_db)):
    topics = db.query(ResearchTopic).order_by(ResearchTopic.id.desc()).all()
    users = db.query(User).order_by(User.full_name).all()
    return templates.TemplateResponse("topics.html", {
        **get_base_context(request, db),
        "topics": topics,
        "users": users,
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
    executor_ids: List[int] = Form(default_factory=list),
    db: Session = Depends(get_db),
    response: Response = None,
    current_user: User = Depends(get_current_user)  # ← Защита формы
):
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
    db.flush()
    if executor_ids:
        executors = db.query(User).filter(User.id.in_(executor_ids)).all()
        new_topic.executors = executors
    db.commit()
    response.headers["X-Toast-Success"] = "Тема успешно добавлена"
    return RedirectResponse(url="/topics", status_code=303)

@app.get("/calendar", response_class=HTMLResponse)
def web_calendar(request: Request, db: Session = Depends(get_db)):
    norms = db.query(WorkingDaysCalendar).order_by(WorkingDaysCalendar.year.desc(), WorkingDaysCalendar.month.desc()).all()
    return templates.TemplateResponse("calendar.html", {
        **get_base_context(request, db),
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
    response: Response = None,
    current_user: User = Depends(get_current_user)  # ← Защита формы
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
        **get_base_context(request, db),
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
    response: Response = None,
    current_user: User = Depends(get_current_user)  # ← Защита формы
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
    reports = []
    for r in reports_db:
        head = db.query(User).filter_by(id=r.lab_head_id).first()
        reports.append(SimpleNamespace(
            id=r.id,
            head_of_lab=head.full_name if head else "Не назначен",
            month=r.month,
            year=r.year,
            total_fund=None,
            status=r.status,
            entries=r.entries
        ))
    return templates.TemplateResponse("reports.html", {
        **get_base_context(request, db),
        "reports": reports,
        "employees": employees,
        "topics": topics,
        "current_year": datetime.now().year,
        "current_month": datetime.now().month,
    })

@app.delete("/calendar/{norm_id}", status_code=204)
def delete_working_days(
    norm_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Удаление нормы рабочих дней (для HTMX)"""
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Только администратор")
    
    norm = db.query(WorkingDaysCalendar).filter(WorkingDaysCalendar.id == norm_id).first()
    if not norm:
        raise HTTPException(status_code=404, detail="Норма не найдена")
    
    db.delete(norm)
    db.commit()
    return Response(status_code=204)
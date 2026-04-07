from sqlalchemy import Column, Integer, String, Boolean, DateTime, Date, ForeignKey, DECIMAL, UniqueConstraint, Enum, Table
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import enum

from .database import Base

# 🔹 ТАБЛИЦА СВЯЗИ MANY-TO-MANY (обязательно вне классов!)
topic_executors = Table(
    "topic_executors", Base.metadata,
    Column("topic_id", Integer, ForeignKey("research_topics.id", ondelete="CASCADE")),
    Column("user_id",  Integer, ForeignKey("users.id", ondelete="CASCADE"))
)


class ReportStatus(str, enum.Enum):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    APPROVED = "approved"
    REJECTED = "rejected"


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, index=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    full_name = Column(String(100))
    department = Column(String(100))
    role = Column(String(20), default="head")  # head | admin | employee
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # 🔹 Обратные связи
    attendance_records = relationship("Attendance", back_populates="employee", cascade="all, delete-orphan")
    service_activities = relationship("ServiceActivity", back_populates="employee", cascade="all, delete-orphan")
    assigned_topics = relationship("ResearchTopic", secondary=topic_executors, back_populates="executors")  # 🔹 ДОБАВЛЕНО


class ResearchTopic(Base):
    __tablename__ = "research_topics"
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(255), nullable=False)
    code = Column(String(50))
    period_year = Column(Integer, nullable=False)
    period_month = Column(Integer, nullable=False)
    department = Column(String(100), nullable=False)
    is_active = Column(Boolean, default=True)
    
    # 🔹 НОВЫЕ ПОЛЯ
    head_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    responsible_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    date_start = Column(Date, nullable=True)   # 🔹 ЗАМЕНЕНО String(10) → Date
    date_end   = Column(Date, nullable=True)   # 🔹 ЗАМЕНЕНО String(10) → Date
    
    # 🔹 СВЯЗЬ MANY-TO-MANY (таблица вынесена на уровень модуля)
    executors = relationship("User", secondary=topic_executors, back_populates="assigned_topics")


class MonthlyReport(Base):
    """Заголовок ежемесячного отчёта лаборатории"""
    __tablename__ = "monthly_reports"
    id = Column(Integer, primary_key=True, index=True)
    lab_head_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    department = Column(String(200), nullable=False)
    year = Column(Integer, nullable=False)
    month = Column(Integer, nullable=False)
    status = Column(String(20), default=ReportStatus.DRAFT)
    submitted_at = Column(DateTime(timezone=True))
    approved_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    entries = relationship("ReportEntry", back_populates="report", cascade="all, delete-orphan")
    __table_args__ = (UniqueConstraint('lab_head_id', 'year', 'month', name='_lab_head_period_uc'),)


class ReportEntry(Base):
    """Строка отчёта: конкретная работа сотрудника по теме"""
    __tablename__ = "report_entries"
    id = Column(Integer, primary_key=True, index=True)
    report_id = Column(Integer, ForeignKey("monthly_reports.id"), nullable=False)
    topic_id = Column(Integer, ForeignKey("research_topics.id"), nullable=False)
    employee_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    work_title = Column(String(300))
    doi_or_link = Column(String(300))
    publications_count = Column(Integer, default=0)
    points_earned = Column(DECIMAL(8, 4), default=0.0)
    notes = Column(String(500))

    report = relationship("MonthlyReport", back_populates="entries")
    topic = relationship("ResearchTopic")
    employee = relationship("User")


class EmployeeMonthlyScore(Base):
    """Агрегированные итоги за месяц"""
    __tablename__ = "employee_monthly_scores"
    id = Column(Integer, primary_key=True, index=True)
    employee_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    year = Column(Integer, nullable=False)
    month = Column(Integer, nullable=False)

    topic_points = Column(DECIMAL(8, 4), default=0.0)
    additional_points = Column(DECIMAL(8, 4), default=0.0)
    total_points = Column(DECIMAL(8, 4), default=0.0)
    status = Column(String(20), default="calculated")

    __table_args__ = (UniqueConstraint('employee_id', 'year', 'month', name='_emp_score_period_uc'),)


# =============================================================================
# 🔹 НОВЫЕ МОДЕЛИ для реального расчёта (Шаг 32)
# =============================================================================

class WorkingDaysCalendar(Base):
    __tablename__ = "working_days_calendar"
    id = Column(Integer, primary_key=True, index=True)
    year = Column(Integer, nullable=False)
    month = Column(Integer, nullable=False)
    total_days = Column(Integer, nullable=False)
    __table_args__ = (UniqueConstraint('year', 'month', name='_calendar_year_month_uc'),)


class Attendance(Base):
    __tablename__ = "attendance"
    id = Column(Integer, primary_key=True, index=True)
    employee_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    year = Column(Integer, nullable=False)
    month = Column(Integer, nullable=False)
    working_days = Column(Integer, nullable=False)
    employee = relationship("User", back_populates="attendance_records")
    __table_args__ = (UniqueConstraint('employee_id', 'year', 'month', name='_attendance_emp_period_uc'),)


class ServiceActivity(Base):
    __tablename__ = "service_activities"
    id = Column(Integer, primary_key=True, index=True)
    employee_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    year = Column(Integer, nullable=False)
    month = Column(Integer, nullable=False)
    criterion_name = Column(String(200), nullable=False)
    criterion_weight = Column(DECIMAL(6, 4), nullable=False)
    quantity = Column(DECIMAL(6, 2), default=1)
    notes = Column(String(500))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    employee = relationship("User", back_populates="service_activities")
from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, DECIMAL, UniqueConstraint
from sqlalchemy.sql import func
from .database import Base

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, index=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    full_name = Column(String(100))
    department = Column(String(100))
    role = Column(String(20), default="head")  # head | admin
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class ResearchTopic(Base):
    __tablename__ = "research_topics"
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(255), nullable=False)
    code = Column(String(50))
    period_year = Column(Integer, nullable=False)
    period_month = Column(Integer, nullable=False)
    department = Column(String(100), nullable=False)  # К какому подразделению относится
    is_active = Column(Boolean, default=True)


class MonthlyReport(Base):
    __tablename__ = "monthly_reports"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    topic_id = Column(Integer, ForeignKey("research_topics.id"), nullable=False)
    period_year = Column(Integer, nullable=False)
    period_month = Column(Integer, nullable=False)
    
    # Поля для ввода данных руководителем
    publications_count = Column(Integer, default=0)
    doi_list = Column(String(500))  # Список DOI через запятую
    additional_notes = Column(String(500))
    
    # Расчётные баллы (заполняются автоматически)
    nir_score = Column(DECIMAL(8, 4), default=0)
    additional_score = Column(DECIMAL(8, 4), default=0)
    total_score = Column(DECIMAL(8, 4), default=0)
    
    # Статус отчёта
    status = Column(String(20), default="draft")  # draft | finalized | approved
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    
    __table_args__ = (
        UniqueConstraint('user_id', 'topic_id', 'period_year', 'period_month', name='_user_topic_period_uc'),
    )
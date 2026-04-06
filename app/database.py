from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# Параметры из docker-compose.yml
DATABASE_URL = "postgresql+psycopg2://devuser:devpassword123@localhost:5432/incentive_db"

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Зависимость для FastAPI (понадобится позже)
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
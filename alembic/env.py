from logging.config import fileConfig

from sqlalchemy import create_engine
from alembic import context
import sys
from pathlib import Path

# Добавляем путь к проекту, чтобы Alembic видел папку app
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Импортируем базу и модели
from app.database import Base, DATABASE_URL
import app.models  # ← КЛЮЧЕВАЯ СТРОКА: регистрирует класс User в Base.metadata

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Подключаем метаданные моделей к Alembic
target_metadata = Base.metadata

def run_migrations_offline() -> None:
    url = DATABASE_URL
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()

def run_migrations_online() -> None:
    connectable = create_engine(DATABASE_URL)

    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata
        )
        with context.begin_transaction():
            context.run_migrations()

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
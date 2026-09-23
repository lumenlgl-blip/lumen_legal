from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.pool import NullPool
from dotenv import load_dotenv
import os

load_dotenv()

SQLALCHEMY_DATABASE_URL = os.getenv("DATABASE_URL")

if not SQLALCHEMY_DATABASE_URL:
    raise RuntimeError("❌ DATABASE_URL no está definida en el archivo .env")

# --- Configuración según el tipo de motor ---
if SQLALCHEMY_DATABASE_URL.startswith("sqlite"):
    # SQLite (desarrollo local rápido)
    engine = create_engine(
        SQLALCHEMY_DATABASE_URL,
        connect_args={"check_same_thread": False},
    )
else:
    # PostgreSQL / Supabase
    # NullPool: obligatorio con el pooler de Supabase (puerto 6543)
    engine = create_engine(
        SQLALCHEMY_DATABASE_URL,
        poolclass=NullPool,
        client_encoding="utf8",
        connect_args={"connect_timeout": 10, "sslmode": "require"},
    )

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
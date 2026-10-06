# app/database.py
"""
Configuración central de la base de datos.

Características:
  - Soporta SQLite (desarrollo) y PostgreSQL/Supabase (producción).
  - Migrado a psycopg3 (evita bloqueos de Smart App Control en Windows).
  - Detecta automáticamente si se usa el pooler de Supabase (puerto 6543)
    para usar NullPool; en caso contrario usa QueuePool con pool_pre_ping.
  - Añade statement_timeout y application_name para trazabilidad.
  - Reintenta la conexión al inicio (útil en despliegues en la nube).
"""

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.pool import NullPool
from sqlalchemy.exc import OperationalError, DBAPIError
from dotenv import load_dotenv
import os
import time
import logging

load_dotenv()

logger = logging.getLogger(__name__)

# ============================================================
# 1. LEER Y NORMALIZAR LA URL DE CONEXIÓN
# ============================================================
SQLALCHEMY_DATABASE_URL = os.getenv("DATABASE_URL")

if not SQLALCHEMY_DATABASE_URL:
    raise RuntimeError(
        "❌ DATABASE_URL no está definida. "
        "Configúrala en el archivo .env (local) o en Render (producción)."
    )

# Normalizar URLs de PostgreSQL para usar el driver psycopg3.
# Render a veces entrega "postgres://" o "postgresql://" — ambos hay que
# convertirlos a "postgresql+psycopg://" para que SQLAlchemy use psycopg3.
if SQLALCHEMY_DATABASE_URL.startswith("postgres://"):
    SQLALCHEMY_DATABASE_URL = SQLALCHEMY_DATABASE_URL.replace(
        "postgres://", "postgresql+psycopg://", 1
    )
elif SQLALCHEMY_DATABASE_URL.startswith("postgresql://"):
    SQLALCHEMY_DATABASE_URL = SQLALCHEMY_DATABASE_URL.replace(
        "postgresql://", "postgresql+psycopg://", 1
    )
elif SQLALCHEMY_DATABASE_URL.startswith("postgresql+psycopg2://"):
    # Si por accidente alguien dejó el prefijo antiguo, lo corregimos.
    SQLALCHEMY_DATABASE_URL = SQLALCHEMY_DATABASE_URL.replace(
        "postgresql+psycopg2://", "postgresql+psycopg://", 1
    )

# Detectar si es SQLite o PostgreSQL
IS_SQLITE = SQLALCHEMY_DATABASE_URL.startswith("sqlite")
IS_POSTGRES = SQLALCHEMY_DATABASE_URL.startswith("postgresql")

# Detectar si estamos usando el pooler de Supabase (puerto 6543) o
# una conexión directa (puerto 5432). El pooler NO admite connection pooling
# local, por eso se usa NullPool.
IS_SUPABASE_POOLER = IS_POSTGRES and (
    ":6543" in SQLALCHEMY_DATABASE_URL
    or "pooler.supabase.com" in SQLALCHEMY_DATABASE_URL
)

# ============================================================
# 2. CONFIGURACIÓN DEL ENGINE SEGÚN EL MOTOR
# ============================================================
if IS_SQLITE:
    # ── SQLite (desarrollo local) ──
    engine = create_engine(
        SQLALCHEMY_DATABASE_URL,
        connect_args={"check_same_thread": False},
        echo=False,
    )
    logger.info("🗄️  Base de datos: SQLite local")

else:
    # ── PostgreSQL / Supabase (producción) ──
    connect_args = {
        "connect_timeout": 10,
        "sslmode": "require",
        "application_name": "lumen_legal",
        # Corta queries que duren más de 30 segundos (evita bloqueos)
        "options": "-c statement_timeout=30000",
        # 🔥 CRÍTICO: desactiva prepared statements de psycopg3.
        # PgBouncer (pooler de Supabase) no los soporta y causa
        # DuplicatePreparedStatement / "prepared statement does not exist".
        "prepare_threshold": None,
    }

    if IS_SUPABASE_POOLER:
        # NullPool es obligatorio con el pooler de Supabase.
        # Supabase (PgBouncer) gestiona el pooling del lado del servidor.
        engine = create_engine(
            SQLALCHEMY_DATABASE_URL,
            poolclass=NullPool,
            client_encoding="utf8",
            connect_args=connect_args,
            echo=False,
        )
        logger.info("🗄️  Base de datos: PostgreSQL (Supabase Pooler · NullPool)")
    else:
        # Conexión directa → QueuePool con verificación previa.
        engine = create_engine(
            SQLALCHEMY_DATABASE_URL,
            pool_size=5,
            max_overflow=10,
            pool_pre_ping=True,
            pool_recycle=1800,  # recicla conexiones cada 30 min
            client_encoding="utf8",
            connect_args=connect_args,
            echo=False,
        )
        logger.info("🗄️  Base de datos: PostgreSQL (directa · QueuePool)")

# ============================================================
# 3. SESSION FACTORY
# ============================================================
SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
    expire_on_commit=False,  # evita expirar objetos tras commit (más rápido)
)

# ============================================================
# 4. BASE DECLARATIVA (para los modelos)
# ============================================================
Base = declarative_base()


# ============================================================
# 5. DEPENDENCIA DE FASTAPI
# ============================================================
def get_db():
    """Dependencia FastAPI que entrega una sesión por request y la cierra al final."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ============================================================
# 6. HEALTH CHECK Y REINTENTOS
# ============================================================
def verify_connection(retries: int = 3, delay: int = 2) -> bool:
    """
    Verifica que la conexión a la base de datos funcione.
    Reintenta varias veces al inicio (útil en Render donde la red tarda
    unos segundos en estar lista).

    Retorna True si la conexión es exitosa, False en caso contrario.
    """
    for intento in range(1, retries + 1):
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            logger.info("✅ Conexión a la base de datos verificada")
            return True
        except (OperationalError, DBAPIError) as e:
            logger.warning(
                f"⚠️ Intento {intento}/{retries} de conexión falló: {str(e)[:150]}"
            )
            if intento < retries:
                time.sleep(delay)
    logger.error("❌ No se pudo conectar a la base de datos tras varios intentos")
    return False

# app/routers/backup.py
# Backup de la base de datos (PostgreSQL/Supabase) como SQL dump en Python puro.
# Los archivos ahora viven en Cloudflare R2; se incluye un manifest con sus keys.

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session
from sqlalchemy import inspect, text
from app.database import get_db, engine
from app.models import core  # noqa: F401  (registra todos los modelos en Base.metadata)
from app.storage import s3_client, R2_BUCKET
from datetime import datetime
import io
import zipfile
import os

router = APIRouter(prefix="/backup", tags=["Backup"])


# ============================================================
# PÁGINA HTML
# ============================================================
@router.get("/", response_class=HTMLResponse)
async def backup_page(request: Request):
    with open("app/templates/backup.html", "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())


# ============================================================
# GENERADOR DE SQL DUMP (Python puro, sin pg_dump)
# ============================================================
def _escape_value(value) -> str:
    """Convierte un valor de Python a literal SQL seguro."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, datetime):
        return f"'{value.isoformat()}'"
    if isinstance(value, bytes):
        # bytea en PostgreSQL
        return "'\\x" + value.hex() + "'"
    # string u otros → escapar comillas simples
    s = str(value).replace("'", "''")
    return f"'{s}'"


def _dump_table(db: Session, table_name: str) -> str:
    """Genera los INSERTs de una tabla."""
    inspector = inspect(engine)
    columns = [col["name"] for col in inspector.get_columns(table_name)]

    rows = db.execute(text(f'SELECT * FROM "{table_name}"')).fetchall()
    if not rows:
        return f"-- (tabla {table_name} vacía)\n"

    lines = [f"-- Datos de {table_name} ({len(rows)} filas)"]
    col_list = ", ".join(f'"{c}"' for c in columns)

    for row in rows:
        values = ", ".join(_escape_value(v) for v in row)
        lines.append(f'INSERT INTO "{table_name}" ({col_list}) VALUES ({values});')

    return "\n".join(lines) + "\n"


def generate_sql_dump(db: Session) -> str:
    """Genera un dump SQL completo: DDL (schema) + DML (datos)."""
    inspector = inspect(engine)
    table_names = inspector.get_table_names()

    parts = [
        "-- ==================================================",
        f"-- Backup Lumen Legal — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "-- Base de datos: Supabase (PostgreSQL)",
        "-- ==================================================",
        "",
        "SET client_encoding = 'UTF8';",
        "SET standard_conforming_strings = on;",
        "",
    ]

    # 1. DDL — estructura de las tablas
    parts.append("-- ==================== ESTRUCTURA ====================")
    parts.append("")

    for table in table_names:
        cols = inspector.get_columns(table)
        col_defs = []
        for col in cols:
            col_type = str(col["type"])
            col_def = f'"{col["name"]}" {col_type}'
            if not col.get("nullable", True):
                col_def += " NOT NULL"
            col_defs.append(col_def)
        parts.append(f'CREATE TABLE IF NOT EXISTS "{table}" (')
        parts.append("  " + ",\n  ".join(col_defs))
        parts.append(");")
        parts.append("")

    # 2. DML — datos de cada tabla
    parts.append("-- ==================== DATOS ====================")
    parts.append("")

    for table in table_names:
        parts.append(_dump_table(db, table))
        parts.append("")

    # 3. Secuencias (para que los IDs sigan funcionando tras restaurar)
    parts.append("-- ==================== SECUENCIAS ====================")
    parts.append("")
    for table in table_names:
        try:
            seq_sql = f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), COALESCE(MAX(id), 1)) FROM \"{table}\";"
            db.execute(text(seq_sql))
            parts.append(seq_sql)
        except Exception:
            # Algunas tablas no tienen 'id' serial; se ignora
            pass

    return "\n".join(parts)


# ============================================================
# MANIFEST DE ARCHIVOS EN R2
# ============================================================
def generate_r2_manifest() -> str:
    """Lista todas las keys guardadas en R2 para incluir en el backup."""
    lines = [
        "# Manifest de archivos en Cloudflare R2",
        f"# Generado: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"# Bucket: {R2_BUCKET}",
        "#",
        "# Formato: <key> | <tamaño en bytes> | <última modificación>",
        "",
    ]
    try:
        paginator = s3_client.get_paginator("list_objects_v2")
        total = 0
        for page in paginator.paginate(Bucket=R2_BUCKET):
            for obj in page.get("Contents", []):
                lines.append(
                    f"{obj['Key']} | {obj['Size']} | "
                    f"{obj['LastModified'].isoformat()}"
                )
                total += 1
        lines.append("")
        lines.append(f"# Total: {total} archivos")
    except Exception as e:
        lines.append(f"# ⚠️ Error al listar R2: {e}")

    return "\n".join(lines)


# ============================================================
# ENDPOINT: DESCARGA DEL BACKUP
# ============================================================
@router.get("/download")
async def download_backup(db: Session = Depends(get_db)):
    """
    Descarga un ZIP con:
      - database.sql    → volcado completo de PostgreSQL
      - r2_manifest.txt → lista de archivos alojados en Cloudflare R2
    """
    # 1. Generar el SQL dump
    sql_dump = generate_sql_dump(db)

    # 2. Generar el manifest de R2
    manifest = generate_r2_manifest()

    # 3. Empaquetar en ZIP
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("database.sql", sql_dump)
        zf.writestr("r2_manifest.txt", manifest)
        zf.writestr(
            "README.txt",
            "Backup Lumen Legal\n"
            "==================\n\n"
            "1. database.sql    → Restaurar con: psql <URL_SUPABASE> < database.sql\n"
            "2. r2_manifest.txt → Lista de archivos en Cloudflare R2.\n"
            "                     Los archivos binarios NO se incluyen aquí,\n"
            "                     porque viven en R2 (no en el servidor).\n"
        )

    buffer.seek(0)
    filename = f"backup_lumen_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"

    return Response(
        content=buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
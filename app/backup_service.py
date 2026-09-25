# app/backup_service.py
"""
Servicio de respaldo y restauración para Lumen Legal.
Adaptado del sistema Transfer.

Genera un ZIP con:
  - metadata.json      → info del respaldo
  - database/*.json    → todas las tablas en JSON
  - storage/**         → todos los archivos de R2 (clientes, expedientes, etc.)
"""
import os
import json
import uuid
import hashlib
import zipfile
import logging
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session
from sqlalchemy import inspect, DateTime, Boolean

from app.models import core
from app.storage import s3_client, R2_BUCKET, get_bytes, list_objects, object_exists
from app.config import settings

logger = logging.getLogger(__name__)

BACKUPS_DIR = "storage/backups"
os.makedirs(BACKUPS_DIR, exist_ok=True)
os.makedirs(os.path.join(BACKUPS_DIR, "_tmp"), exist_ok=True)


# ------------------------------------------------------------
# ORDEN DE TABLAS (respeta FKs)
# ------------------------------------------------------------
TABLES_INSERT_ORDER = [
    "firms",
    "users",
    "clients",
    "client_documents",
    "contracts",
    "court_cases",
    "payments",
    "actuaciones",
    "agenda_events",
    "activity_logs",
    "backups",
]

TABLES_DELETE_ORDER = list(reversed(TABLES_INSERT_ORDER))

TABLE_TO_MODEL = {
    "firms": core.Firm,
    "users": core.User,
    "clients": core.Client,
    "client_documents": core.ClientDocument,
    "contracts": core.Contract,
    "court_cases": core.CourtCase,
    "payments": core.Payment,
    "actuaciones": core.Actuacion,
    "agenda_events": core.AgendaEvent,
    "activity_logs": core.ActivityLog,
    "backups": core.Backup,
}

# Prefijos de R2 que se respaldan (excluye temp/ y backups/)
R2_BACKUP_PREFIXES = [
    "clientes/",
    "expedientes/",
    "pagos/",
    "contratos/",
]


# ============================================================
# UTILIDADES
# ============================================================
def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def _serialize_row(row) -> dict:
    """Convierte una fila SQLAlchemy a dict JSON-safe."""
    d = {}
    for col in row.__table__.columns:
        val = getattr(row, col.name)
        if isinstance(val, datetime):
            d[col.name] = val.isoformat()
        elif isinstance(val, (bytes, bytearray)):
            d[col.name] = val.decode("utf-8", errors="replace")
        else:
            d[col.name] = val
    return d


def _parse_row(row: dict, column_types: dict) -> dict:
    """Convierte strings ISO a datetime/boolean para SQLAlchemy."""
    parsed = {}
    for k, v in row.items():
        col_type = column_types.get(k)
        if v is None:
            parsed[k] = None
            continue

        if isinstance(col_type, DateTime) and isinstance(v, str):
            try:
                if v.endswith("Z"):
                    v = v[:-1]
                parsed[k] = datetime.fromisoformat(v)
            except (ValueError, TypeError):
                parsed[k] = None
        elif isinstance(col_type, Boolean) and isinstance(v, int):
            parsed[k] = bool(v)
        else:
            parsed[k] = v
    return parsed


# ============================================================
# GENERAR BACKUP
# ============================================================
def generar_backup(db: Session, user_id: Optional[int] = None,
                   notes: Optional[str] = None,
                   progress_cb=None) -> dict:
    """Exporta BD (JSON) + archivos de R2 en un ZIP. Sube a R2."""
    now = datetime.utcnow()
    timestamp = now.strftime("%Y%m%d_%H%M%S")
    filename = f"backup_lumen_{timestamp}.zip"
    stored_name = f"{uuid.uuid4().hex}.zip"
    out_path = os.path.join(BACKUPS_DIR, stored_name)

    # 1. Exportar tablas
    inspector = inspect(db.get_bind())
    existing_tables = set(inspector.get_table_names())

    tables_data = {}
    total_records = 0
    total_tables = len([t for t in TABLES_INSERT_ORDER if t in existing_tables])
    current_table = 0

    for table_name in TABLES_INSERT_ORDER:
        if table_name not in existing_tables:
            continue

        current_table += 1
        if progress_cb:
            progress_cb(f"Exportando tabla: {table_name}", current_table, total_tables)

        model_class = TABLE_TO_MODEL.get(table_name)
        if model_class is None:
            continue

        rows = db.query(model_class).all()
        serialized = [_serialize_row(r) for r in rows]
        tables_data[table_name] = serialized
        total_records += len(serialized)

    # 2. Listar archivos de R2 (solo los prefijos permitidos)
    r2_files = []
    for prefix in R2_BACKUP_PREFIXES:
        for obj in list_objects(prefix):
            # Skip dotfiles / marcadores
            if obj["key"].endswith("/.used"):
                continue
            r2_files.append(obj["key"])

    total_files = len(r2_files)
    logger.info(f"📦 Backup: {len(tables_data)} tablas, {total_files} archivos en R2")

    # 3. Crear el ZIP
    metadata = {
        "version": "1.0",
        "app": "lumen-legal",
        "created_at": now.isoformat() + "Z",
        "created_by_user_id": user_id,
        "notes": notes or "",
        "num_records": total_records,
        "num_files": total_files,
        "tables": list(tables_data.keys()),
    }

    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        # metadata
        zf.writestr("metadata.json", json.dumps(metadata, ensure_ascii=False, indent=2))

        # database
        for table_name, rows in tables_data.items():
            zf.writestr(
                f"database/{table_name}.json",
                json.dumps(rows, ensure_ascii=False, indent=2, default=str),
            )

        # storage — descarga cada archivo de R2 y lo mete al ZIP
        for idx, key in enumerate(r2_files, 1):
            if progress_cb and idx % 20 == 0:
                progress_cb(f"Empaquetando archivos: {idx}/{total_files}", idx, total_files)
            try:
                data = get_bytes(key)
                if data is None:
                    continue
                zf.writestr(f"storage/{key}", data)
            except Exception as e:
                logger.warning(f"No se pudo incluir {key}: {e}")

    # 4. Hash y tamaño
    size = os.path.getsize(out_path)
    sha = _sha256_file(out_path)

    # 5. Subir a R2
    r2_subido = False
    try:
        with open(out_path, "rb") as f:
            s3_client.put_object(Bucket=R2_BUCKET, Key=f"backups/{stored_name}", Body=f.read())
        r2_subido = True
        logger.info(f"⬆️  Backup subido a R2: backups/{stored_name} ({size} bytes)")
    except Exception as e:
        logger.warning(f"No se pudo subir backup a R2: {e}")

    return {
        "filename": filename,
        "stored_name": stored_name,
        "path": out_path,
        "size": size,
        "sha256": sha,
        "num_records": total_records,
        "num_files": total_files,
        "tablas": list(tables_data.keys()),
        "r2_subido": r2_subido,
    }


# ============================================================
# LISTAR / BORRAR
# ============================================================
def listar_backups(db: Session):
    return (
        db.query(core.Backup)
        .order_by(core.Backup.created_at.desc())
        .all()
    )


def borrar_backup(db: Session, backup_id: int) -> bool:
    b = db.query(core.Backup).get(backup_id)
    if not b:
        return False

    # 1. Borrar de R2
    try:
        s3_client.delete_object(Bucket=R2_BUCKET, Key=f"backups/{b.stored_name}")
    except Exception as e:
        logger.warning(f"No se pudo borrar de R2 {b.stored_name}: {e}")

    # 2. Borrar de disco
    path = os.path.join(BACKUPS_DIR, b.stored_name)
    if os.path.exists(path):
        try:
            os.remove(path)
        except OSError as e:
            logger.warning(f"No se pudo borrar {path}: {e}")

    db.delete(b)
    db.commit()
    return True


# ============================================================
# RESOLVER PATH (disco → R2 fallback)
# ============================================================
def _obtener_zip_path(stored_name: str) -> str:
    local_path = os.path.join(BACKUPS_DIR, stored_name)
    if os.path.exists(local_path):
        return local_path

    # Intentar desde R2
    data = get_bytes(f"backups/{stored_name}")
    if data is None:
        raise FileNotFoundError(f"Respaldo no encontrado: {stored_name}")

    os.makedirs(BACKUPS_DIR, exist_ok=True)
    with open(local_path, "wb") as f:
        f.write(data)
    logger.info(f"⬇️  Respaldo descargado de R2: {stored_name}")
    return local_path


# ============================================================
# INSPECCIONAR
# ============================================================
def inspeccionar_backup(zip_path: str) -> dict:
    if not os.path.isabs(zip_path) and not os.path.exists(zip_path):
        zip_path = _obtener_zip_path(os.path.basename(zip_path))

    if not os.path.exists(zip_path):
        raise FileNotFoundError("El archivo ZIP no existe")
    if not zipfile.is_zipfile(zip_path):
        raise ValueError("El archivo no es un ZIP válido")

    resultado = {
        "metadata": {},
        "tablas": {},
        "total_records": 0,
        "total_files": 0,
        "tamaño_total": os.path.getsize(zip_path),
        "tamaño_storage": 0,
        "archivos_incluidos": [],
    }

    with zipfile.ZipFile(zip_path, "r") as zf:
        try:
            metadata_raw = zf.read("metadata.json").decode("utf-8")
            resultado["metadata"] = json.loads(metadata_raw)
        except KeyError:
            raise ValueError("El ZIP no contiene metadata.json. No es un respaldo válido.")

        for name in zf.namelist():
            if name.startswith("database/") and name.endswith(".json"):
                table_name = name[len("database/"):-len(".json")]
                try:
                    rows = json.loads(zf.read(name).decode("utf-8"))
                    if isinstance(rows, list):
                        resultado["tablas"][table_name] = len(rows)
                        resultado["total_records"] += len(rows)
                    else:
                        resultado["tablas"][table_name] = 0
                except Exception:
                    resultado["tablas"][table_name] = -1

            elif name.startswith("storage/") and not name.endswith("/"):
                resultado["total_files"] += 1
                try:
                    info = zf.getinfo(name)
                    resultado["tamaño_storage"] += info.file_size
                except Exception:
                    pass
                if len(resultado["archivos_incluidos"]) < 10:
                    resultado["archivos_incluidos"].append(name[len("storage/"):])

    return resultado


# ============================================================
# RESTAURAR SELECTIVO
# ============================================================
def restaurar_backup_selectivo(
    db: Session,
    zip_path: str,
    tablas_seleccionadas: list,
    modo: str = "merge",
    restaurar_archivos: bool = True,
    progress_cb=None,
) -> dict:
    if not os.path.isabs(zip_path) and not os.path.exists(zip_path):
        zip_path = _obtener_zip_path(os.path.basename(zip_path))

    if not os.path.exists(zip_path):
        raise FileNotFoundError("El archivo ZIP no existe")
    if not zipfile.is_zipfile(zip_path):
        raise ValueError("El archivo no es un ZIP válido")
    if modo not in ("replace", "merge"):
        raise ValueError("Modo inválido. Usa 'replace' o 'merge'.")

    tablas_seleccionadas = [t.strip() for t in (tablas_seleccionadas or []) if t.strip()]
    if not tablas_seleccionadas:
        raise ValueError("Debes seleccionar al menos una tabla")

    resultado = {
        "modo": modo,
        "tablas_procesadas": [],
        "tablas_saltadas": [],
        "tablas_error": [],
        "num_records_insertados": 0,
        "num_records_saltados": 0,
        "num_records_borrados": 0,
        "num_files": 0,
    }

    with zipfile.ZipFile(zip_path, "r") as zf:
        try:
            metadata = json.loads(zf.read("metadata.json").decode("utf-8"))
        except KeyError:
            raise ValueError("El ZIP no contiene metadata.json")

        all_names = zf.namelist()
        inspector = inspect(db.get_bind())
        existing_tables = set(inspector.get_table_names())

        # PASO 1: Borrar (modo replace)
        if modo == "replace":
            for table_name in TABLES_DELETE_ORDER:
                if table_name not in tablas_seleccionadas or table_name not in existing_tables:
                    continue
                model_class = TABLE_TO_MODEL.get(table_name)
                if model_class is None:
                    continue
                try:
                    count = db.query(model_class).delete(synchronize_session=False)
                    db.commit()
                    resultado["num_records_borrados"] += count or 0
                except Exception as e:
                    db.rollback()
                    logger.warning(f"No se pudo borrar {table_name}: {e}")

        # PASO 2: Insertar
        tablas_ordenadas = [t for t in TABLES_INSERT_ORDER if t in tablas_seleccionadas]
        tablas_ordenadas += [t for t in tablas_seleccionadas if t not in TABLES_INSERT_ORDER]

        total_tablas = len(tablas_ordenadas)

        for idx, table_name in enumerate(tablas_ordenadas, 1):
            if progress_cb:
                progress_cb(f"Restaurando tabla: {table_name}", idx, total_tablas)

            path_in_zip = f"database/{table_name}.json"
            if path_in_zip not in all_names or table_name not in existing_tables:
                resultado["tablas_saltadas"].append(table_name)
                continue

            model_class = TABLE_TO_MODEL.get(table_name)
            if model_class is None:
                resultado["tablas_saltadas"].append(table_name)
                continue

            try:
                rows = json.loads(zf.read(path_in_zip).decode("utf-8"))
            except Exception as e:
                logger.error(f"Error leyendo {path_in_zip}: {e}")
                resultado["tablas_error"].append(table_name)
                continue

            column_types = {c.name: c.type for c in model_class.__table__.columns}
            insertados = 0
            saltados = 0

            for row_data in rows:
                try:
                    if modo == "merge" and "id" in row_data and row_data["id"] is not None:
                        if db.query(model_class).get(row_data["id"]):
                            saltados += 1
                            continue

                    parsed = _parse_row(row_data, column_types)
                    db.add(model_class(**parsed))
                    insertados += 1
                except Exception as e:
                    logger.warning(f"Error insertando en {table_name}: {e}")
                    saltados += 1

            try:
                db.commit()
            except Exception as e:
                db.rollback()
                logger.error(f"Error al guardar {table_name}: {e}")
                resultado["tablas_error"].append(table_name)
                continue

            resultado["tablas_procesadas"].append(table_name)
            resultado["num_records_insertados"] += insertados
            resultado["num_records_saltados"] += saltados

        # PASO 3: Restaurar archivos (siempre que existan en el ZIP)
        if restaurar_archivos:
            if progress_cb:
                progress_cb("Restaurando archivos adjuntos…", 0, 1)

            archivos_en_zip = [
                n for n in all_names
                if n.startswith("storage/") and not n.endswith("/")
            ]
            files_restored = 0
            total_files = len(archivos_en_zip)

            for idx, name in enumerate(archivos_en_zip, 1):
                if progress_cb and idx % 20 == 0:
                    progress_cb(f"Restaurando archivos: {idx}/{total_files}", idx, total_files)

                rel = name[len("storage/"):]
                if not rel:
                    continue

                # Excluir temp/ y backups/
                if rel.startswith(("temp/", "backups/")):
                    continue

                try:
                    data = zf.read(name)
                    s3_client.put_object(Bucket=R2_BUCKET, Key=rel, Body=data)
                    files_restored += 1
                except Exception as e:
                    logger.warning(f"No se pudo restaurar {name}: {e}")

            resultado["num_files"] = files_restored

    resultado["metadata"] = metadata
    return resultado
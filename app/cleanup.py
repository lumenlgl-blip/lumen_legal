# app/cleanup.py
# Limpieza automática robusta de:
#   1. Temporales locales (temp/, tmp/, storage/temp/)
#   2. Temporales en R2 (prefijo temp/)
#   3. Archivos huérfanos en R2 (sin referencia en BD)
#   4. Bitácora antigua (ActivityLog > 180 días)
#   5. Backups ZIP huérfanos (sin registro en BD)
#
# Se ejecuta en background desde main.py. Sin cron, sin costo.

import os
import time
import logging
from pathlib import Path
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

# ============================================================
# CONFIGURACIÓN DE RETENCIÓN
# ============================================================
# Directorios temporales locales a limpiar (relativos al CWD)
TEMP_DIRS = [
    Path("temp"),
    Path("tmp"),
    Path("storage/temp"),  # ← el que usa LibreOffice en preview
    Path("storage/tmp"),
]

# Archivos locales más antiguos que esto se borran
LOCAL_MAX_AGE_HOURS = 2

# Objetos en R2 bajo "temp/" más antiguos que esto se borran
R2_TEMP_MAX_AGE_HOURS = 24

# Bitácora: cuántos días conservar
ACTIVITY_LOG_KEEP_DAYS = 180

# Acciones críticas que NUNCA se borran de la bitácora
ACCIONES_CRITICAS = ("delete", "cleanup", "error_ia", "reprint", "backup")

# Ficheros protegidos: NO borrar aunque estén viejos
PROTECTED_NAMES = {".gitkeep", ".gitignore", ".keep", "README.md"}


# ============================================================
# UTILIDADES
# ============================================================
def _safe_unlink(path: Path) -> bool:
    """Borra un archivo capturando cualquier error. Retorna True si tuvo éxito."""
    try:
        if path.is_file() and path.name not in PROTECTED_NAMES:
            path.unlink()
            return True
    except (PermissionError, OSError) as e:
        logger.warning(f"No se pudo borrar {path}: {e}")
    return False


def _safe_rmdir(path: Path) -> bool:
    """Intenta borrar un directorio vacío. Retorna True si tuvo éxito."""
    try:
        if path.is_dir():
            path.rmdir()  # solo funciona si está vacío
            return True
    except OSError:
        pass  # no está vacío o está en uso, se ignora
    return False


# ============================================================
# 1. LIMPIEZA DE ARCHIVOS LOCALES
# ============================================================
def cleanup_temp_files() -> dict:
    """
    Elimina archivos y carpetas temporales locales viejos.
    Protege archivos en uso (los que fallan al borrar se reintentan después).
    """
    cutoff = time.time() - (LOCAL_MAX_AGE_HOURS * 3600)
    borrados = 0
    fallidos = 0
    carpetas_borradas = 0

    for temp_dir in TEMP_DIRS:
        if not temp_dir.exists():
            continue

        try:
            # Recorrer de abajo hacia arriba para poder borrar directorios vacíos
            for item in sorted(temp_dir.rglob("*"), reverse=True):
                if not item.exists():
                    continue

                if item.is_file():
                    if item.stat().st_mtime < cutoff:
                        if _safe_unlink(item):
                            borrados += 1
                        else:
                            fallidos += 1

                elif item.is_dir() and item != temp_dir:
                    # Intentar borrar la carpeta si quedó vacía
                    if _safe_rmdir(item):
                        carpetas_borradas += 1

        except Exception as e:
            logger.warning(f"Error escaneando {temp_dir}: {e}")

    return {
        "archivos_borrados": borrados,
        "carpetas_borradas": carpetas_borradas,
        "archivos_en_uso": fallidos,
    }


# ============================================================
# 2. LIMPIEZA DE TEMPORALES EN R2 (prefijo "temp/")
# ============================================================
def cleanup_r2_temp() -> int:
    """
    Elimina objetos bajo 'temp/' en R2 con más de R2_TEMP_MAX_AGE_HOURS.
    Usa borrado por lotes (hasta 1000 por request, límite de S3 API).
    """
    try:
        from app.storage import s3_client, R2_BUCKET
    except Exception as e:
        logger.error(f"No se pudo importar storage: {e}")
        return 0

    cutoff = datetime.now(timezone.utc) - timedelta(hours=R2_TEMP_MAX_AGE_HOURS)
    borrados = 0

    try:
        paginator = s3_client.get_paginator("list_objects_v2")
        a_borrar = []

        for page in paginator.paginate(Bucket=R2_BUCKET, Prefix="temp/"):
            for obj in page.get("Contents", []):
                if obj["LastModified"] < cutoff:
                    a_borrar.append({"Key": obj["Key"]})

        # Borrado por lotes de 1000
        for i in range(0, len(a_borrar), 1000):
            batch = a_borrar[i : i + 1000]
            try:
                s3_client.delete_objects(
                    Bucket=R2_BUCKET,
                    Delete={"Objects": batch, "Quiet": True},
                )
                borrados += len(batch)
            except Exception as e:
                logger.warning(f"Error borrando lote en R2: {e}")

    except Exception as e:
        logger.error(f"Error listando R2 temp/: {e}")

    return borrados


# ============================================================
# 3. LIMPIEZA DE HUÉRFANOS EN R2
# ============================================================
def cleanup_r2_orphans() -> dict:
    """
    Busca archivos en R2 cuyas keys ya no están referenciadas en la BD
    y los borra. Revisa los prefijos: clientes/, expedientes/, formatos_demanda/.

    SOLO borra archivos con más de 24h de antigüedad para evitar race conditions
    (un archivo recién subido podría no estar aún en la BD).
    """
    try:
        from app.storage import s3_client, R2_BUCKET
        from app.database import SessionLocal
        from app.models.core import (
            ClientDocument,
            Actuacion,
            FormatoDemanda,
            CourtCase,
            Payment,
        )
    except Exception as e:
        logger.error(f"No se pudo importar modelos/storage: {e}")
        return {"revisados": 0, "huerfanos": 0, "borrados": 0, "errores": 0}

    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    db = SessionLocal()

    try:
        # ── Recolectar todas las keys registradas en la BD ──
        keys_en_db = set()

        # Documentos de clientes
        for (key,) in db.query(ClientDocument.file_url).all():
            if key:
                keys_en_db.add(key)

        # Actuaciones
        for (key,) in db.query(Actuacion.pdf_url).all():
            if key:
                keys_en_db.add(key)

        # Formatos de demanda
        for (key,) in db.query(FormatoDemanda.archivo_key).all():
            if key:
                keys_en_db.add(key)

        # Acuses de expedientes
        for (key,) in db.query(CourtCase.acuse_pdf_url).all():
            if key:
                keys_en_db.add(key)

        # Comprobantes de pagos
        for (key,) in db.query(Payment.receipt_pdf_url).all():
            if key:
                keys_en_db.add(key)

        # ── Escanear R2 y encontrar huérfanos ──
        prefijos = ["clientes/", "expedientes/", "formatos_demanda/"]
        paginator = s3_client.get_paginator("list_objects_v2")

        total_revisados = 0
        huerfanos = 0
        borrados = 0
        errores = 0
        a_borrar = []

        for prefijo in prefijos:
            try:
                for page in paginator.paginate(Bucket=R2_BUCKET, Prefix=prefijo):
                    for obj in page.get("Contents", []):
                        total_revisados += 1
                        key = obj["Key"]

                        # Solo procesar si tiene > 24h (evita race conditions)
                        if obj["LastModified"] >= cutoff:
                            continue

                        if key not in keys_en_db:
                            huerfanos += 1
                            a_borrar.append({"Key": key})
            except Exception as e:
                logger.warning(f"Error escaneando prefijo {prefijo}: {e}")

        # ── Borrar huérfanos por lotes ──
        for i in range(0, len(a_borrar), 1000):
            batch = a_borrar[i : i + 1000]
            try:
                s3_client.delete_objects(
                    Bucket=R2_BUCKET,
                    Delete={"Objects": batch, "Quiet": True},
                )
                borrados += len(batch)
            except Exception as e:
                logger.warning(f"Error borrando huérfanos: {e}")
                errores += len(batch)

        return {
            "revisados": total_revisados,
            "huerfanos": huerfanos,
            "borrados": borrados,
            "errores": errores,
        }

    except Exception as e:
        logger.error(f"Error en cleanup_r2_orphans: {e}")
        return {"revisados": 0, "huerfanos": 0, "borrados": 0, "errores": 0}
    finally:
        db.close()


# ============================================================
# 4. LIMPIEZA DE BITÁCORA ANTIGUA
# ============================================================
def cleanup_activity_logs() -> int:
    """
    Elimina logs de actividad con más de ACTIVITY_LOG_KEEP_DAYS.
    NUNCA borra acciones críticas (delete, cleanup, error_ia, reprint, backup).
    """
    try:
        from app.database import SessionLocal
        from app.models.core import ActivityLog
    except Exception as e:
        logger.error(f"No se pudo importar ActivityLog: {e}")
        return 0

    db = SessionLocal()
    try:
        umbral = datetime.utcnow() - timedelta(days=ACTIVITY_LOG_KEEP_DAYS)

        borrados = (
            db.query(ActivityLog)
            .filter(
                ActivityLog.created_at < umbral,
                ~ActivityLog.action.in_(ACCIONES_CRITICAS),
            )
            .delete(synchronize_session=False)
        )
        db.commit()

        if borrados:
            logger.info(f"Bitácora: {borrados} logs antiguos eliminados")

        return borrados

    except Exception as e:
        db.rollback()
        logger.error(f"Error limpiando bitácora: {e}")
        return 0
    finally:
        db.close()


# ============================================================
# 5. LIMPIEZA DE BACKUPS HUÉRFANOS EN DISCO
# ============================================================
def cleanup_orphan_backups() -> int:
    """
    Borra archivos ZIP de backup en disco que ya no tienen registro en la BD.
    Caso típico: si el borrado previo falló y quedó el archivo huérfano.
    """
    try:
        from app.database import SessionLocal
        from app.models.core import Backup
        import app.backup_service as bs
    except Exception as e:
        logger.error(f"No se pudo importar backup_service: {e}")
        return 0

    try:
        backup_dir = Path(bs.BACKUPS_DIR)
        if not backup_dir.exists():
            return 0

        db = SessionLocal()
        try:
            stored_names_en_db = {
                row[0] for row in db.query(Backup.stored_name).all() if row[0]
            }

            borrados = 0
            for zip_file in backup_dir.glob("*.zip"):
                if zip_file.name not in stored_names_en_db:
                    if _safe_unlink(zip_file):
                        borrados += 1
                        logger.info(f"Backup huérfano borrado: {zip_file.name}")

            return borrados
        finally:
            db.close()

    except Exception as e:
        logger.error(f"Error en cleanup_orphan_backups: {e}")
        return 0


# ============================================================
# ORQUESTADOR PRINCIPAL
# ============================================================
def cleanup_all() -> dict:
    """
    Ejecuta toda la limpieza. Devuelve un resumen completo con estadísticas.
    Cada sección es independiente: si una falla, las demás siguen.
    """
    inicio = time.time()
    resumen = {
        "temp_local": {},
        "r2_temp": 0,
        "r2_orphans": {},
        "activity_logs": 0,
        "orphan_backups": 0,
        "duracion_seg": 0,
    }

    # 1) Temporales locales
    try:
        resumen["temp_local"] = cleanup_temp_files()
    except Exception as e:
        logger.error(f"Fallo temp_local: {e}")

    # 2) Temporales en R2
    try:
        resumen["r2_temp"] = cleanup_r2_temp()
    except Exception as e:
        logger.error(f"Fallo r2_temp: {e}")

    # 3) Huérfanos en R2 (más costoso, se ejecuta siempre)
    try:
        resumen["r2_orphans"] = cleanup_r2_orphans()
    except Exception as e:
        logger.error(f"Fallo r2_orphans: {e}")

    # 4) Bitácora antigua
    try:
        resumen["activity_logs"] = cleanup_activity_logs()
    except Exception as e:
        logger.error(f"Fallo activity_logs: {e}")

    # 5) Backups huérfanos
    try:
        resumen["orphan_backups"] = cleanup_orphan_backups()
    except Exception as e:
        logger.error(f"Fallo orphan_backups: {e}")

    resumen["duracion_seg"] = round(time.time() - inicio, 2)

    # Log resumen
    tl = resumen["temp_local"]
    ro = resumen["r2_orphans"]
    print(
        f"🧹 Limpieza [{resumen['duracion_seg']}s]: "
        f"{tl.get('archivos_borrados', 0)} locales | "
        f"{resumen['r2_temp']} R2 temp | "
        f"{ro.get('borrados', 0)} huérfanos R2 | "
        f"{resumen['activity_logs']} logs | "
        f"{resumen['orphan_backups']} backups huérfanos"
    )

    return resumen


# ============================================================
# MODO MANUAL (opcional, para endpoint admin)
# ============================================================
def cleanup_manual() -> dict:
    """Alias para llamar desde un endpoint HTTP admin manualmente."""
    return cleanup_all()

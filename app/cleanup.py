# app/cleanup.py
# Limpieza automática de temporales locales Y de R2.
# Se ejecuta como tarea en background desde main.py (sin cron, sin pagar nada).

import time
import shutil
from pathlib import Path
from datetime import datetime, timezone, timedelta

# ============================================================
# LIMPIEZA DE ARCHIVOS LOCALES (temp/, tmp/)
# ============================================================
TEMP_DIRS = [Path("temp"), Path("tmp")]
MAX_AGE_HOURS = 1  # Archivos locales con más de 1 hora se borran


def cleanup_temp_files() -> int:
    """Elimina archivos temporales locales viejos. Retorna cuántos borró."""
    cutoff = time.time() - (MAX_AGE_HOURS * 3600)
    removed = 0

    for temp_dir in TEMP_DIRS:
        if not temp_dir.exists():
            continue
        for f in temp_dir.rglob("*"):
            if f.is_file() and f.stat().st_mtime < cutoff:
                try:
                    f.unlink()
                    removed += 1
                except Exception as e:
                    print(f"⚠️ No se pudo borrar {f}: {e}")

    return removed


# ============================================================
# LIMPIEZA DE TEMPORALES EN R2 (prefijo "temp/")
# ============================================================
R2_TEMP_MAX_AGE_HOURS = 24  # Archivos temp/ en R2 con más de 24h se borran


def cleanup_r2_temp() -> int:
    """Elimina objetos en R2 bajo temp/ con más de 24h. Retorna cuántos borró."""
    try:
        from app.storage import s3_client, R2_BUCKET
    except Exception as e:
        print(f"⚠️ No se pudo importar storage: {e}")
        return 0

    cutoff = datetime.now(timezone.utc) - timedelta(hours=R2_TEMP_MAX_AGE_HOURS)
    removed = 0

    try:
        paginator = s3_client.get_paginator("list_objects_v2")
        to_delete = []

        for page in paginator.paginate(Bucket=R2_BUCKET, Prefix="temp/"):
            for obj in page.get("Contents", []):
                if obj["LastModified"] < cutoff:
                    to_delete.append({"Key": obj["Key"]})

        # Borrar en lotes de 1000 (límite de S3 API)
        for i in range(0, len(to_delete), 1000):
            batch = to_delete[i : i + 1000]
            s3_client.delete_objects(
                Bucket=R2_BUCKET,
                Delete={"Objects": batch, "Quiet": True},
            )
            removed += len(batch)

    except Exception as e:
        print(f"⚠️ Error limpiando R2: {e}")

    return removed


# ============================================================
# LIMPIEZA COMPLETA
# ============================================================
def cleanup_all() -> dict:
    """Ejecuta toda la limpieza. Retorna un resumen."""
    local = cleanup_temp_files()
    r2 = cleanup_r2_temp()
    resumen = {"archivos_locales": local, "objetos_r2": r2, "total": local + r2}
    print(f"🧹 Limpieza: {local} locales, {r2} en R2")
    return resumen
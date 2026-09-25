# app/routers/backup.py
import os
import uuid
import json
import logging
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Request
from fastapi.responses import HTMLResponse, FileResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import core
from app.routers.auth import get_current_user, verify_password
from app import backup_service as bs

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/backup", tags=["Backup"])


def _require_admin(request: Request, db: Session, password: str = None):
    user = get_current_user(request, db)
    if not user:
        raise HTTPException(401, "No autenticado")
    if user.role != "admin":
        raise HTTPException(403, "Solo administradores")
    if password is not None and not verify_password(password, user.hashed_password):
        raise HTTPException(401, "Contraseña incorrecta")
    return user


# ============================================================
# UI
# ============================================================
@router.get("/", response_class=HTMLResponse)
async def backup_page(request: Request):
    with open("app/templates/backup.html", "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())


# ============================================================
# CREAR
# ============================================================
@router.post("/create")
async def backup_create(
    request: Request,
    password: str = Form(...),
    notes: str = Form(""),
    db: Session = Depends(get_db),
):
    user = _require_admin(request, db, password)

    try:
        result = bs.generar_backup(db=db, user_id=user.id, notes=notes.strip() or None)
    except Exception as e:
        logger.exception("Error generando respaldo")
        raise HTTPException(500, f"Error generando respaldo: {e}")

    b = core.Backup(
        filename=result["filename"],
        stored_name=result["stored_name"],
        size=result["size"],
        sha256=result["sha256"],
        num_records=result["num_records"],
        num_files=result["num_files"],
        created_by=user.id,
        notes=notes.strip() or None,
    )
    db.add(b)
    db.commit()
    db.refresh(b)

    return {
        "ok": True,
        "id": b.id,
        "filename": b.filename,
        "size": b.size,
        "num_records": b.num_records,
        "num_files": b.num_files,
        "created_at": (b.created_at.isoformat() + "Z") if b.created_at else None,
    }


# ============================================================
# LISTAR
# ============================================================
@router.post("/list")
async def backup_list(
    request: Request,
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    _require_admin(request, db, password)

    backups = bs.listar_backups(db)
    result = []
    for b in backups:
        local_path = os.path.join(bs.BACKUPS_DIR, b.stored_name)
        exists = os.path.exists(local_path) or bs.object_exists(f"backups/{b.stored_name}")
        result.append({
            "id": b.id,
            "filename": b.filename,
            "size": b.size,
            "sha256": b.sha256,
            "num_records": b.num_records,
            "num_files": b.num_files,
            "created_by": b.created_by,
            "created_at": (b.created_at.isoformat() + "Z") if b.created_at else None,
            "notes": b.notes,
            "exists": exists,
        })
    return result


# ============================================================
# DESCARGAR
# ============================================================
@router.get("/download/{backup_id}")
async def backup_download(
    backup_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    # Autenticación por cookie/sesión
    _require_admin(request, db)

    b = db.query(core.Backup).get(backup_id)
    if not b:
        raise HTTPException(404, "Respaldo no encontrado")

    try:
        path = bs._obtener_zip_path(b.stored_name)
    except FileNotFoundError:
        raise HTTPException(410, "El archivo del respaldo ya no existe")

    return FileResponse(path, filename=b.filename, media_type="application/zip")


# ============================================================
# ELIMINAR
# ============================================================
@router.delete("/{backup_id}")
async def backup_delete(
    backup_id: int,
    request: Request,
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    _require_admin(request, db, password)

    b = db.query(core.Backup).get(backup_id)
    if not b:
        raise HTTPException(404, "Respaldo no encontrado")

    bs.borrar_backup(db, backup_id)
    return {"ok": True}


# ============================================================
# INSPECCIONAR
# ============================================================
@router.post("/inspect")
async def backup_inspect(
    request: Request,
    file: UploadFile = File(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    _require_admin(request, db, password)

    if not file.filename.lower().endswith(".zip"):
        raise HTTPException(400, "El archivo debe ser un ZIP")

    tmp_dir = os.path.join(bs.BACKUPS_DIR, "_tmp")
    os.makedirs(tmp_dir, exist_ok=True)
    tmp_path = os.path.join(tmp_dir, f"inspect_{uuid.uuid4().hex}.zip")

    try:
        with open(tmp_path, "wb") as f:
            while chunk := await file.read(1024 * 1024):
                f.write(chunk)

        resultado = bs.inspeccionar_backup(zip_path=tmp_path)

        actuales = {
            "firms": db.query(core.Firm).count(),
            "users": db.query(core.User).count(),
            "clients": db.query(core.Client).count(),
            "client_documents": db.query(core.ClientDocument).count(),
            "contracts": db.query(core.Contract).count(),
            "court_cases": db.query(core.CourtCase).count(),
            "payments": db.query(core.Payment).count(),
            "actuaciones": db.query(core.Actuacion).count(),
            "agenda_events": db.query(core.AgendaEvent).count(),
            "activity_logs": db.query(core.ActivityLog).count(),
            "backups": db.query(core.Backup).count(),
        }

        return {
            "ok": True,
            "metadata": resultado["metadata"],
            "tablas": resultado["tablas"],
            "total_records": resultado["total_records"],
            "total_files": resultado["total_files"],
            "tamaño_total": resultado["tamaño_total"],
            "tamaño_storage": resultado["tamaño_storage"],
            "archivos_incluidos": resultado["archivos_incluidos"],
            "actuales": actuales,
        }
    except ValueError as e:
        raise HTTPException(400, f"Respaldo inválido: {e}")
    except Exception as e:
        logger.exception("Error inspeccionando respaldo")
        raise HTTPException(500, f"Error: {e}")
    finally:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass


# ============================================================
# RESTAURAR SELECTIVO
# ============================================================
@router.post("/restore-selective")
async def backup_restore_selective(
    request: Request,
    file: UploadFile = File(...),
    password: str = Form(...),
    confirm: str = Form(...),
    tablas: str = Form(...),
    modo: str = Form("merge"),
    restaurar_archivos: str = Form("true"),
    db: Session = Depends(get_db),
):
    _require_admin(request, db, password)

    if confirm.strip().upper() != "CONFIRMAR":
        raise HTTPException(400, "Debes escribir CONFIRMAR para proceder")

    if not file.filename.lower().endswith(".zip"):
        raise HTTPException(400, "El archivo debe ser un ZIP")

    try:
        tablas_list = json.loads(tablas)
        if not isinstance(tablas_list, list):
            raise ValueError
    except Exception:
        raise HTTPException(400, "Formato de 'tablas' inválido")

    if not tablas_list:
        raise HTTPException(400, "Selecciona al menos una tabla")
    if modo not in ("replace", "merge"):
        raise HTTPException(400, "Modo inválido")

    restaurar_arch = str(restaurar_archivos).lower() in ("true", "1", "yes", "on")

    tmp_dir = os.path.join(bs.BACKUPS_DIR, "_tmp")
    os.makedirs(tmp_dir, exist_ok=True)
    tmp_path = os.path.join(tmp_dir, f"restore_{uuid.uuid4().hex}.zip")

    try:
        with open(tmp_path, "wb") as f:
            while chunk := await file.read(1024 * 1024):
                f.write(chunk)

        result = bs.restaurar_backup_selectivo(
            db=db,
            zip_path=tmp_path,
            tablas_seleccionadas=tablas_list,
            modo=modo,
            restaurar_archivos=restaurar_arch,
        )
    except ValueError as e:
        raise HTTPException(400, f"Respaldo inválido: {e}")
    except Exception as e:
        logger.exception("Error restaurando")
        raise HTTPException(500, f"Error: {e}")
    finally:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass

    return {
        "ok": True,
        "modo": result["modo"],
        "tablas_procesadas": result["tablas_procesadas"],
        "tablas_saltadas": result["tablas_saltadas"],
        "tablas_error": result["tablas_error"],
        "num_records_insertados": result["num_records_insertados"],
        "num_records_saltados": result["num_records_saltados"],
        "num_records_borrados": result["num_records_borrados"],
        "num_files": result["num_files"],
        "metadata": result.get("metadata", {}),
    }
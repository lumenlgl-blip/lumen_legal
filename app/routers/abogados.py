# app/routers/abogados.py
from fastapi import APIRouter, Depends, HTTPException, Request, Form, UploadFile, File
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from sqlalchemy import or_
import uuid
import os

from app.database import get_db
from app.models.core import Abogado, User, ActivityLog
from app.routers.auth import get_current_user
from app.storage import upload_fileobj, delete_file, s3_client, R2_BUCKET

router = APIRouter(prefix="/abogados", tags=["Abogados"])


# ============================================================
# HELPERS
# ============================================================
def _require_admin(request: Request, db: Session):
    user = get_current_user(request, db)
    if not user:
        raise HTTPException(401, "No autenticado")
    if user.role != "admin":
        raise HTTPException(403, "Solo administradores")
    return user


# ============================================================
# UI: LISTAR ABOGADOS
# ============================================================
@router.get("/", response_class=HTMLResponse)
async def show_abogados_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        from fastapi.responses import RedirectResponse

        return RedirectResponse("/auth/login", status_code=302)
    if user.role != "admin":
        return HTMLResponse("<h1>Acceso denegado</h1>", status_code=403)

    with open("app/templates/list_abogados.html", "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())


# ============================================================
# UI: REGISTRAR ABOGADO
# ============================================================
@router.get("/register", response_class=HTMLResponse)
async def show_register_abogado(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        from fastapi.responses import RedirectResponse

        return RedirectResponse("/auth/login", status_code=302)
    if user.role != "admin":
        return HTMLResponse("<h1>Acceso denegado</h1>", status_code=403)

    with open("app/templates/register_abogado.html", "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())


# ============================================================
# API: LISTAR (JSON)
# ============================================================
@router.get("/list")
async def list_abogados(request: Request, db: Session = Depends(get_db)):
    """Lista todos los abogados del despacho. Disponible para cualquier usuario autenticado
    (necesario para el select de asignación en contratos)."""
    user = get_current_user(request, db)
    if not user:
        raise HTTPException(401, "No autenticado")

    q = request.query_params.get("q", "").strip()
    query = db.query(Abogado).filter(Abogado.firm_id == user.firm_id)

    if q:
        term = f"%{q}%"
        query = query.filter(
            or_(
                Abogado.nombre_completo.ilike(term),
                Abogado.cedula_profesional.ilike(term),
                Abogado.especialidad.ilike(term),
                Abogado.email.ilike(term),
            )
        )

    abogados = query.order_by(Abogado.nombre_completo).all()

    return [
        {
            "id": a.id,
            "nombre_completo": a.nombre_completo,
            "cedula_profesional": a.cedula_profesional,
            "domicilio_profesional": a.domicilio_profesional or "",
            "especialidad": a.especialidad or "",
            "telefono": a.telefono or "",
            "email": a.email or "",
            "user_id": a.user_id,
            "tiene_acceso": a.user_id is not None,
            "is_active": bool(a.is_active),
            "created_at": a.created_at.strftime("%d/%m/%Y") if a.created_at else "",
        }
        for a in abogados
    ]


# ============================================================
# API: CREAR ABOGADO
# ============================================================
@router.post("/create")
async def create_abogado(
    request: Request,
    nombre_completo: str = Form(...),
    cedula_profesional: str = Form(...),
    domicilio_profesional: str = Form(...),
    especialidad: str = Form(None),
    telefono: str = Form(None),
    email: str = Form(None),
    db: Session = Depends(get_db),
):
    user = _require_admin(request, db)

    nombre_completo = nombre_completo.strip()
    cedula_profesional = cedula_profesional.strip()
    domicilio_profesional = domicilio_profesional.strip()
    especialidad = (especialidad or "").strip() or None
    telefono = (telefono or "").strip() or None
    email = (email or "").strip().lower() or None

    if not nombre_completo or not cedula_profesional or not domicilio_profesional:
        raise HTTPException(400, "Nombre, cédula y domicilio son obligatorios")

    # Verificar cédula duplicada en el despacho
    existente = (
        db.query(Abogado)
        .filter(
            Abogado.firm_id == user.firm_id,
            Abogado.cedula_profesional == cedula_profesional,
        )
        .first()
    )
    if existente:
        raise HTTPException(400, "Ya existe un abogado con esa cédula profesional")

    nuevo = Abogado(
        firm_id=user.firm_id,
        nombre_completo=nombre_completo,
        cedula_profesional=cedula_profesional,
        domicilio_profesional=domicilio_profesional,
        especialidad=especialidad,
        telefono=telefono,
        email=email,
    )
    db.add(nuevo)
    db.flush()

    db.add(
        ActivityLog(
            firm_id=user.firm_id,
            user_id=user.id,
            action="create",
            entity="Abogado",
            entity_id=nuevo.id,
            description=f"Registró al abogado {nombre_completo} (cédula {cedula_profesional})",
        )
    )
    db.commit()
    db.refresh(nuevo)

    return {
        "message": "Abogado registrado exitosamente",
        "id": nuevo.id,
        "nombre_completo": nuevo.nombre_completo,
    }


# ============================================================
# API: ACTUALIZAR ABOGADO
# ============================================================
@router.put("/update/{abogado_id}")
async def update_abogado(
    abogado_id: int,
    request: Request,
    nombre_completo: str = Form(...),
    cedula_profesional: str = Form(...),
    domicilio_profesional: str = Form(...),
    especialidad: str = Form(None),
    telefono: str = Form(None),
    email: str = Form(None),
    db: Session = Depends(get_db),
):
    user = _require_admin(request, db)

    abogado = (
        db.query(Abogado)
        .filter(Abogado.id == abogado_id, Abogado.firm_id == user.firm_id)
        .first()
    )
    if not abogado:
        raise HTTPException(404, "Abogado no encontrado")

    nombre_completo = nombre_completo.strip()
    cedula_profesional = cedula_profesional.strip()
    domicilio_profesional = domicilio_profesional.strip()
    especialidad = (especialidad or "").strip() or None
    telefono = (telefono or "").strip() or None
    email = (email or "").strip().lower() or None

    if not nombre_completo or not cedula_profesional or not domicilio_profesional:
        raise HTTPException(400, "Nombre, cédula y domicilio son obligatorios")

    # Verificar cédula duplicada (excluyendo el propio)
    dup = (
        db.query(Abogado)
        .filter(
            Abogado.firm_id == user.firm_id,
            Abogado.cedula_profesional == cedula_profesional,
            Abogado.id != abogado_id,
        )
        .first()
    )
    if dup:
        raise HTTPException(400, "Ya existe otro abogado con esa cédula")

    abogado.nombre_completo = nombre_completo
    abogado.cedula_profesional = cedula_profesional
    abogado.domicilio_profesional = domicilio_profesional
    abogado.especialidad = especialidad
    abogado.telefono = telefono
    abogado.email = email

    db.add(
        ActivityLog(
            firm_id=user.firm_id,
            user_id=user.id,
            action="update",
            entity="Abogado",
            entity_id=abogado.id,
            description=f"Actualizó al abogado {nombre_completo}",
        )
    )
    db.commit()
    db.refresh(abogado)

    return {"message": "Abogado actualizado", "id": abogado.id}


# ============================================================
# API: ACTIVAR / DESACTIVAR ABOGADO
# ============================================================
@router.post("/toggle-status/{abogado_id}")
async def toggle_abogado_status(
    abogado_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    user = _require_admin(request, db)

    abogado = (
        db.query(Abogado)
        .filter(Abogado.id == abogado_id, Abogado.firm_id == user.firm_id)
        .first()
    )
    if not abogado:
        raise HTTPException(404, "Abogado no encontrado")

    abogado.is_active = not abogado.is_active
    estado = "activado" if abogado.is_active else "desactivado"

    db.add(
        ActivityLog(
            firm_id=user.firm_id,
            user_id=user.id,
            action="update",
            entity="Abogado",
            entity_id=abogado.id,
            description=f"{estado.capitalize()} al abogado {abogado.nombre_completo}",
        )
    )
    db.commit()

    return {
        "message": f"Abogado {estado}",
        "is_active": bool(abogado.is_active),
    }


# ============================================================
# API: ELIMINAR ABOGADO
# ============================================================
@router.delete("/delete/{abogado_id}")
async def delete_abogado(
    abogado_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    user = _require_admin(request, db)

    abogado = (
        db.query(Abogado)
        .filter(Abogado.id == abogado_id, Abogado.firm_id == user.firm_id)
        .first()
    )
    if not abogado:
        raise HTTPException(404, "Abogado no encontrado")

    # Verificar si tiene contratos asignados
    from app.models.core import Contract

    contratos_asignados = (
        db.query(Contract).filter(Contract.assigned_lawyer_id == abogado_id).count()
    )
    if contratos_asignados > 0:
        raise HTTPException(
            400,
            f"No se puede eliminar: tiene {contratos_asignados} contrato(s) asignado(s). "
            f"Reasígnalos o desactívalo en su lugar.",
        )

    nombre = abogado.nombre_completo
    db.delete(abogado)

    db.add(
        ActivityLog(
            firm_id=user.firm_id,
            user_id=user.id,
            action="delete",
            entity="Abogado",
            entity_id=abogado_id,
            description=f"Eliminó al abogado {nombre}",
        )
    )
    db.commit()

    return {"message": "Abogado eliminado"}


# ============================================================
# API: VINCULAR / DESVINCULAR USUARIO
# ============================================================
@router.post("/link-user/{abogado_id}")
async def link_user(
    abogado_id: int,
    request: Request,
    user_id: int = Form(None),
    db: Session = Depends(get_db),
):
    """
    Vincula (o desvincula) un usuario del sistema a un abogado.
    - user_id=None o "" → desvincula
    - user_id=N → vincula al usuario N (debe existir, mismo despacho, no estar ya vinculado)
    """
    user = _require_admin(request, db)

    abogado = (
        db.query(Abogado)
        .filter(Abogado.id == abogado_id, Abogado.firm_id == user.firm_id)
        .first()
    )
    if not abogado:
        raise HTTPException(404, "Abogado no encontrado")

    # Desvincular
    if user_id is None or user_id == "" or user_id == 0:
        abogado.user_id = None
        db.add(
            ActivityLog(
                firm_id=user.firm_id,
                user_id=user.id,
                action="update",
                entity="Abogado",
                entity_id=abogado.id,
                description=f"Desvinculó usuario del abogado {abogado.nombre_completo}",
            )
        )
        db.commit()
        return {"message": "Usuario desvinculado", "user_id": None}

    # Vincular
    target_user = (
        db.query(User).filter(User.id == user_id, User.firm_id == user.firm_id).first()
    )
    if not target_user:
        raise HTTPException(404, "Usuario no encontrado")

    # Verificar que ese usuario no esté vinculado a otro abogado
    ya_vinculado = (
        db.query(Abogado)
        .filter(Abogado.user_id == user_id, Abogado.id != abogado_id)
        .first()
    )
    if ya_vinculado:
        raise HTTPException(
            400,
            f"El usuario ya está vinculado al abogado {ya_vinculado.nombre_completo}",
        )

    abogado.user_id = user_id

    db.add(
        ActivityLog(
            firm_id=user.firm_id,
            user_id=user.id,
            action="update",
            entity="Abogado",
            entity_id=abogado.id,
            description=f"Vínculó usuario {target_user.full_name} al abogado {abogado.nombre_completo}",
        )
    )
    db.commit()

    return {"message": "Usuario vinculado", "user_id": user_id}


# ============================================================
# API: LISTAR USUARIOS DISPONIBLES PARA VINCULAR
# ============================================================
@router.get("/available-users")
async def list_available_users(request: Request, db: Session = Depends(get_db)):
    """Usuarios del despacho que NO están vinculados a ningún abogado todavía."""
    user = _require_admin(request, db)

    # IDs ya vinculados
    vinculados = (
        db.query(Abogado.user_id)
        .filter(Abogado.firm_id == user.firm_id, Abogado.user_id.isnot(None))
        .all()
    )
    ids_vinculados = {row[0] for row in vinculados}

    usuarios = (
        db.query(User)
        .filter(User.firm_id == user.firm_id, User.is_active == True)
        .order_by(User.full_name)
        .all()
    )

    return [
        {
            "id": u.id,
            "full_name": u.full_name,
            "email": u.email,
            "role": u.role,
            "ya_vinculado": u.id in ids_vinculados,
        }
        for u in usuarios
    ]

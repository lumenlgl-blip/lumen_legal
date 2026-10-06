# app/routers/users.py
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.core import Abogado
from app.routers.auth import get_current_user

router = APIRouter(prefix="/users", tags=["Users"])


@router.get("/lawyers")
async def list_lawyers(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        raise HTTPException(401, "No autenticado")

    abogados = (
        db.query(Abogado)
        .filter(Abogado.firm_id == user.firm_id, Abogado.is_active == True)
        .order_by(Abogado.nombre_completo)
        .all()
    )

    return [
        {
            "id": a.id,
            "nombre": a.nombre_completo,
            "cedula": a.cedula_profesional,
            "especialidad": a.especialidad or "",
        }
        for a in abogados
    ]

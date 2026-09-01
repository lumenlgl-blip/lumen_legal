from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.core import ActivityLog, User
from datetime import datetime
from passlib.context import CryptContext

router = APIRouter(prefix="/activity", tags=["Activity"])

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

@router.get("/", response_class=HTMLResponse)
async def show_activity_log(request: Request):
    with open("app/templates/activity_log.html", "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())

@router.get("/logs")
async def get_logs(db: Session = Depends(get_db)):
    try:
        logs = db.query(ActivityLog).order_by(ActivityLog.created_at.desc()).limit(100).all()
        
        logs_data = []
        for log in logs:
            user_name = "Sistema"
            if log.user_id:
                user = db.query(User).filter(User.id == log.user_id).first()
                if user:
                    user_name = user.full_name
            
            logs_data.append({
                "id": log.id,
                "user": user_name,
                "action": log.action,
                "entity": log.entity,
                "description": log.description or "",
                "date": log.created_at.strftime("%d/%m/%Y %H:%M") if log.created_at else "N/A"
            })
        
        return logs_data
    except Exception as e:
        return [{
            "id": 0,
            "user": "Sistema",
            "action": "error",
            "entity": "Error",
            "description": str(e),
            "date": "Ahora"
        }]

# --- Limpiar bitácora (solo admin con contraseña) ---
@router.post("/clear")
async def clear_activity_log(
    request: Request,
    password: str = Form(...),
    db: Session = Depends(get_db)
):
    # Verificar autenticación
    from app.routers.auth import get_current_user, verify_password
    
    user = get_current_user(request, db)
    if not user:
        return {"success": False, "message": "No autenticado"}
    
    if user.role != "admin":
        return {"success": False, "message": "Solo administradores pueden limpiar la bitácora"}
    
    # Verificar contraseña
    if not verify_password(password, user.hashed_password):
        return {"success": False, "message": "Contraseña incorrecta"}
    
    # Contar registros antes de eliminar
    total_logs = db.query(ActivityLog).count()
    
    # Eliminar todos los registros
    db.query(ActivityLog).delete()
    db.commit()
    
    # Registrar en la bitácora quién hizo la limpieza
    new_log = ActivityLog(
        firm_id=user.firm_id,
        user_id=user.id,
        action="delete",
        entity="Bitácora",
        entity_id=None,
        description=f"{user.full_name} eliminó {total_logs} registros de la bitácora"
    )
    db.add(new_log)
    db.commit()
    
    return {
        "success": True,
        "message": f"Bitácora limpiada correctamente. Se eliminaron {total_logs} registros.",
        "eliminados": total_logs,
        "limpiado_por": user.full_name
    }
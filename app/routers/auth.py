from fastapi import APIRouter, Depends, HTTPException, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from sqlalchemy import or_
from passlib.context import CryptContext
from jose import jwt, JWTError
import os
from datetime import datetime, timedelta
from app.database import get_db
from app.models.core import User, Firm

router = APIRouter(prefix="/auth", tags=["Authentication"])

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

SECRET_KEY = os.getenv("JWT_SECRET", "MI_CLAVE_SUPER_SECRETA_CAMBIAR_EN_PRODUCCION_123456789")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 10

def get_password_hash(password):
    if len(password.encode('utf-8')) > 72:
        password = password[:72]
    return pwd_context.hash(password)

def verify_password(plain_password, hashed_password):
    if len(plain_password.encode('utf-8')) > 72:
        plain_password = plain_password[:72]
    return pwd_context.verify(plain_password, hashed_password)

def create_access_token(data: dict, expires_delta: timedelta = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def get_current_user(request: Request, db: Session = None):
    token = request.cookies.get("access_token")
    if not token:
        return None
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = int(payload.get("sub"))
        if user_id is None:
            return None
        if db is None:
            from app.database import SessionLocal
            db = SessionLocal()
            try:
                return db.query(User).filter(User.id == user_id).first()
            finally:
                db.close()
        else:
            return db.query(User).filter(User.id == user_id).first()
    except JWTError:
        return None

# --- LOGIN ---
@router.get("/login", response_class=HTMLResponse)
async def login_form(request: Request):
    user = get_current_user(request)
    if user:
        return RedirectResponse("/", status_code=302)
    try:
        with open("app/templates/login.html", "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except FileNotFoundError:
        return HTMLResponse("<h1>Login no disponible</h1>")

@router.post("/login")
async def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db)
):
    user = db.query(User).filter(
        (User.email == email) | (User.full_name == email),
        User.is_active == True
    ).first()
    if not user or not verify_password(password, user.hashed_password):
        with open("app/templates/login.html", "r", encoding="utf-8") as f:
            html = f.read()
        html = html.replace("<!-- ERROR_MESSAGE -->", '<div class="alert alert-danger">⚠️ Credenciales incorrectas</div>')
        return HTMLResponse(content=html, status_code=401)
    
    # Registrar en bitácora
    from app.models.core import ActivityLog
    log = ActivityLog(
        firm_id=user.firm_id,
        user_id=user.id,
        action="login",
        entity="Usuario",
        entity_id=user.id,
        description=f"{user.full_name} inició sesión"
    )
    db.add(log)
    db.commit()
    
    access_token = create_access_token(data={"sub": str(user.id), "role": user.role, "firm_id": user.firm_id})
    
    response = RedirectResponse("/", status_code=302)
    response.set_cookie(
        key="access_token",
        value=access_token,
        httponly=True,
        secure=False,
        samesite="lax",
        max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        path="/"
    )
    return response

@router.get("/logout")
async def logout():
    response = RedirectResponse("/auth/login", status_code=302)
    response.delete_cookie("access_token")
    return response

# --- REGISTRO ---
@router.get("/register", response_class=HTMLResponse)
async def register_form(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/auth/login", status_code=302)
    if user.role != "admin":
        return HTMLResponse("<h1>Acceso denegado</h1>", status_code=403)
    try:
        with open("app/templates/register_user.html", "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except FileNotFoundError:
        return HTMLResponse("<h1>Template no encontrado</h1>")

@router.post("/register")
async def register_user(
    request: Request,
    full_name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    role: str = Form("abogado"),
    permissions: str = Form(""),
    db: Session = Depends(get_db)
):
    current_user = get_current_user(request)
    if not current_user:
        raise HTTPException(401, "No autenticado")
    if current_user.role != "admin":
        raise HTTPException(403, "Solo administradores pueden registrar usuarios")
    
    existing = db.query(User).filter(User.email == email).first()
    if existing:
        raise HTTPException(400, "El email ya está registrado")
    
    new_user = User(
        firm_id=current_user.firm_id,
        full_name=full_name,
        email=email,
        hashed_password=get_password_hash(password),
        role=role,
        is_active=True,
        must_change_password=True,
        permissions=permissions
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    
    return {"message": "Usuario registrado exitosamente", "user_id": new_user.id, "email": new_user.email, "role": new_user.role}

# --- LISTAR USUARIOS ---
@router.get("/users", response_class=HTMLResponse)
async def list_users(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/auth/login", status_code=302)
    if user.role != "admin":
        return HTMLResponse("<h1>Acceso denegado</h1>", status_code=403)
    
    users = db.query(User).filter(User.firm_id == user.firm_id).all()
    
    rows_html = ""
    for u in users:
        status = "✅ Activo" if u.is_active else "❌ Inactivo"
        role_badge = "bg-danger" if u.role == "admin" else "bg-success" if u.role == "abogado" else "bg-info"
        
        permissions_html = ""
        if u.role == "admin" or u.permissions == "all":
            permissions_html = '<span class="badge bg-dark permission-badge">Todos</span>'
        elif u.permissions:
            perm_labels = {
                "clients": "👤 Clientes",
                "contracts": "📜 Contratos",
                "cases": "📁 Expedientes",
                "payments": "💰 Pagos",
                "actuaciones": "📄 Actuaciones",
                "consult": "🔍 Consultas",
                "agenda": "📅 Agenda",
                "dashboard": "📊 Dashboard",
                "audit": "🔎 Auditoría"
            }
            for perm in u.permissions.split(","):
                perm = perm.strip()
                if perm in perm_labels:
                    permissions_html += f'<span class="badge bg-primary permission-badge me-1">{perm_labels[perm]}</span>'
        else:
            permissions_html = '<span class="text-muted">Sin permisos</span>'
        
        rows_html += f"""
            <tr>
                <td>{u.id}</td>
                <td><strong>{u.full_name}</strong></td>
                <td>{u.email}</td>
                <td><span class="badge {role_badge}">{u.role.upper()}</span></td>
                <td>{status}</td>
                <td>{permissions_html}</td>
                <td>
                    <button class="btn btn-sm btn-warning reset-password" data-id="{u.id}" data-name="{u.full_name}">
                        🔑 Restablecer
                    </button>
                    <button class="btn btn-sm btn-info edit-permissions" data-id="{u.id}" data-name="{u.full_name}" data-permissions="{u.permissions or ''}">
                        ⚙️ Permisos
                    </button>
                </td>
            </tr>
        """
    
    with open("app/templates/list_users.html", "r", encoding="utf-8") as f:
        html = f.read()
    html = html.replace("{{ rows }}", rows_html)
    
    return HTMLResponse(content=html)

# --- RESTABLECER CONTRASEÑA ---
@router.post("/reset-password/{user_id}")
async def reset_password(
    request: Request,
    user_id: int,
    new_password: str = Form(...),
    db: Session = Depends(get_db)
):
    current_user = get_current_user(request)
    if not current_user or current_user.role != "admin":
        raise HTTPException(403, "Solo administradores pueden restablecer contraseñas")
    
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(404, "Usuario no encontrado")
    
    user.hashed_password = get_password_hash(new_password)
    user.must_change_password = True
    db.commit()
    
    return {"message": f"Contraseña restablecida para {user.full_name}"}

# --- ACTUALIZAR PERMISOS ---
@router.post("/update-permissions/{user_id}")
async def update_permissions(
    request: Request,
    user_id: int,
    permissions: str = Form(""),
    db: Session = Depends(get_db)
):
    current_user = get_current_user(request)
    if not current_user or current_user.role != "admin":
        raise HTTPException(403, "Solo administradores pueden actualizar permisos")
    
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(404, "Usuario no encontrado")
    
    user.permissions = permissions
    db.commit()
    
    return {"message": f"Permisos actualizados para {user.full_name}"}

# --- VERIFICAR CAMBIO DE CONTRASEÑA ---
@router.get("/check-password-change")
async def check_password_change(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return {"must_change": False}
    return {"must_change": user.must_change_password}

# --- CAMBIAR CONTRASEÑA (primer acceso) ---
@router.post("/change-password")
async def change_password(
    request: Request,
    new_password: str = Form(...),
    db: Session = Depends(get_db)
):
    user = get_current_user(request, db)
    if not user:
        raise HTTPException(401, "No autenticado")
    
    if len(new_password) < 8:
        raise HTTPException(400, "La contraseña debe tener al menos 8 caracteres")
    
    user.hashed_password = get_password_hash(new_password)
    user.must_change_password = False
    db.commit()
    
    return {"message": "Contraseña actualizada correctamente"}

# --- CAMBIAR CONTRASEÑA (desde perfil) ---
@router.post("/change-password-auth")
async def change_password_auth(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    db: Session = Depends(get_db)
):
    user = get_current_user(request, db)
    if not user:
        raise HTTPException(401, "No autenticado")
    
    if not verify_password(current_password, user.hashed_password):
        raise HTTPException(400, "La contraseña actual es incorrecta")
    
    if len(new_password) < 8:
        raise HTTPException(400, "La nueva contraseña debe tener al menos 8 caracteres")
    
    if verify_password(new_password, user.hashed_password):
        raise HTTPException(400, "La nueva contraseña no puede ser igual a la anterior")
    
    user.hashed_password = get_password_hash(new_password)
    user.must_change_password = False
    db.commit()
    
    return {"message": "Contraseña actualizada correctamente"}

# --- PÁGINA DE CAMBIO DE CONTRASEÑA ---
@router.get("/change-password-page", response_class=HTMLResponse)
async def change_password_page(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/auth/login", status_code=302)
    if not user.must_change_password:
        return RedirectResponse("/", status_code=302)
    
    with open("app/templates/change_password.html", "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())

# --- VERIFICAR SESIÓN ---
@router.get("/check-session")
async def check_session(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return {"authenticated": False}
    return {"authenticated": True, "user_id": user.id, "full_name": user.full_name, "role": user.role}

# --- REFRESCAR PERMISOS ---
@router.post("/refresh-permissions")
async def refresh_permissions(
    request: Request,
    db: Session = Depends(get_db)
):
    user = get_current_user(request, db)
    if not user:
        raise HTTPException(401, "No autenticado")
    
    access_token = create_access_token(
        data={"sub": str(user.id), "role": user.role, "firm_id": user.firm_id}
    )
    
    response = RedirectResponse("/", status_code=302)
    response.set_cookie(
        key="access_token",
        value=access_token,
        httponly=True,
        secure=False,
        max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        path="/"
    )
    return response

# --- Página de cambio de contraseña desde login ---
@router.get("/change-password-login", response_class=HTMLResponse)
async def change_password_login_form(request: Request):
    with open("app/templates/change_password_login.html", "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())

# --- Procesar cambio de contraseña desde login ---
@router.post("/change-password-login")
async def change_password_login(
    email: str = Form(...),
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    db: Session = Depends(get_db)
):
    # Buscar usuario
    user = db.query(User).filter(User.email == email, User.is_active == True).first()
    
    if not user or not verify_password(current_password, user.hashed_password):
        raise HTTPException(400, "Email o contraseña actual incorrecta")
    
    if len(new_password) < 8:
        raise HTTPException(400, "La nueva contraseña debe tener al menos 8 caracteres")
    
    if new_password != confirm_password:
        raise HTTPException(400, "Las contraseñas no coinciden")
    
    if verify_password(new_password, user.hashed_password):
        raise HTTPException(400, "La nueva contraseña no puede ser igual a la anterior")
    
    user.hashed_password = get_password_hash(new_password)
    user.must_change_password = False
    db.commit()
    
    return RedirectResponse("/auth/login", status_code=302)
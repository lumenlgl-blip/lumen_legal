from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware
import os
from dotenv import load_dotenv
from app.database import engine, Base
from app.models import core

# Cargar variables de entorno
load_dotenv()

# Inicializar la app
app = FastAPI(title="Lumen Legal", version="1.0.0")

# Configurar rutas de templates y archivos estáticos
templates = Jinja2Templates(directory="app/templates")
app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

# --- Crear tablas al iniciar ---
Base.metadata.create_all(bind=engine)

# --- MIDDLEWARE DE AUTENTICACIÓN ---
class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        public_paths = [
            "/auth/login",
            "/auth/logout",
            "/auth/check-session",
            "/static",
            "/uploads",
            "/docs",
            "/openapi.json",
            "/redoc"
        ]
        
        # Verificar rutas públicas
        for path in public_paths:
            if request.url.path.startswith(path):
                return await call_next(request)
        
        # Verificar token de sesión
        token = request.cookies.get("access_token")
        if not token:
            return RedirectResponse("/auth/login", status_code=302)
        
        return await call_next(request)

app.add_middleware(AuthMiddleware)

# --- IMPORTAR ROUTERS ---
from app.routers import health, clients, cases, contracts, payments, actuaciones, auth

# Registrar routers
app.include_router(health.router, prefix="/api")
app.include_router(clients.router, prefix="/api")
app.include_router(cases.router, prefix="/api")
app.include_router(contracts.router, prefix="/api")
app.include_router(payments.router, prefix="/api")
app.include_router(actuaciones.router, prefix="/api")
app.include_router(auth.router)  # Rutas de autenticación (sin /api)

# --- FUNCIÓN PARA OBTENER USUARIO ACTUAL ---
from app.routers.auth import get_current_user

# --- RUTA RAÍZ ---
@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    user = get_current_user(request)
    
    # Si no está autenticado, redirigir a login
    if not user:
        return RedirectResponse("/auth/login", status_code=302)
    
    # Obtener usuario actualizado de la BD (por si los permisos cambiaron)
    from app.database import SessionLocal
    from app.models.core import User as UserModel
    db = SessionLocal()
    try:
        user = db.query(UserModel).filter(UserModel.id == user.id).first()
    finally:
        db.close()
    
    if not user:
        return RedirectResponse("/auth/login", status_code=302)
    
    # Si debe cambiar contraseña, redirigir a cambio
    if user.must_change_password:
        return RedirectResponse("/auth/change-password-page", status_code=302)
    
    # Determinar permisos
    user_permissions = []
    if user.role == "admin" or user.permissions == "all":
        user_permissions = ["clients", "contracts", "cases", "payments", "actuaciones", "consult", "admin"]
    else:
        user_permissions = user.permissions.split(",") if user.permissions else []
    
    return templates.TemplateResponse("index.html", {
        "request": request,
        "user": user,
        "permissions": user_permissions
    })
    
# --- RUTA DE PERFIL ---
@app.get("/profile", response_class=HTMLResponse)
async def profile(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/auth/login", status_code=302)
    
    return templates.TemplateResponse("profile.html", {"request": request, "user": user})

# --- RUTA DE USUARIOS (solo admin) ---
@app.get("/users", response_class=HTMLResponse)
async def list_users(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/auth/login", status_code=302)
    if user.role != "admin":
        return HTMLResponse("<h1>Acceso denegado</h1><p>Solo administradores pueden ver esta sección.</p>", status_code=403)
    
    # Redirigir a la ruta de auth
    return RedirectResponse("/auth/users", status_code=302)

@app.get("/change-password", response_class=HTMLResponse)
async def change_password_redirect(request: Request):
    return RedirectResponse("/auth/change-password-page", status_code=302)
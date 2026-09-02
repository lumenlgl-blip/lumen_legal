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

# Crear carpetas necesarias para producción
REQUIRED_DIRS = [
    "uploads",
    "uploads/client_docs",
    "uploads/case_docs",
    "uploads/actuaciones",
    "uploads/payment_docs"
]

for directory in REQUIRED_DIRS:
    os.makedirs(directory, exist_ok=True)

# Configurar rutas de templates y archivos estáticos
templates = Jinja2Templates(directory="app/templates")
app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

# --- Crear tablas al iniciar ---
Base.metadata.create_all(bind=engine)

# --- MIDDLEWARE DE AUTENTICACIÓN ---
class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Rutas públicas (no requieren autenticación)
        public_paths = [
            "/auth/login",
            "/auth/logout",
            "/auth/check-session",
            "/auth/change-password-page",
            "/auth/change-password-login",
            "/setup-admin",
            "/static",
            "/uploads",
            "/docs",
            "/openapi.json",
            "/redoc",
            "/loading",
        ]
        
        # Verificar si la ruta es pública
        for path in public_paths:
            if request.url.path.startswith(path):
                return await call_next(request)
        
        # Verificar autenticación
        token = request.cookies.get("access_token")
        if not token:
            return RedirectResponse("/auth/login", status_code=302)
        
        return await call_next(request)

app.add_middleware(AuthMiddleware)


# --- IMPORTAR ROUTERS ---
# Import
from app.routers import health, clients, cases, contracts, payments, actuaciones, auth, agenda, dashboard, audit, activity, backup




# Registrar routers
app.include_router(health.router, prefix="/api")
app.include_router(clients.router, prefix="/api")
app.include_router(cases.router, prefix="/api")
app.include_router(contracts.router, prefix="/api")
app.include_router(payments.router, prefix="/api")
app.include_router(actuaciones.router, prefix="/api")
app.include_router(agenda.router, prefix="/api")
app.include_router(dashboard.router, prefix="/api")
app.include_router(audit.router, prefix="/api")
app.include_router(activity.router, prefix="/api")
app.include_router(backup.router, prefix="/api")
app.include_router(auth.router)

# --- FUNCIÓN PARA OBTENER USUARIO ACTUAL ---
from app.routers.auth import get_current_user

# --- RUTA RAÍZ ---
@app.get("/", response_class=HTMLResponse)
@app.head("/", response_class=HTMLResponse)
async def root(request: Request):
    user = get_current_user(request)
    
    if not user:
        return RedirectResponse("/loading", status_code=302)
    
    # Obtener usuario actualizado de la BD
    from app.database import SessionLocal
    from app.models.core import User as UserModel
    db = SessionLocal()
    try:
        user = db.query(UserModel).filter(UserModel.id == user.id).first()
    finally:
        db.close()
    
    if not user:
        return RedirectResponse("/auth/login", status_code=302)
    
    if user.must_change_password:
        return RedirectResponse("/auth/change-password-page", status_code=302)
    
    # Determinar permisos
    user_permissions = []
    if user.role == "admin" or user.permissions == "all":
        user_permissions = ["clients", "contracts", "cases", "payments", "actuaciones", "consult", "agenda", "dashboard", "audit", "admin"]
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
async def list_users_redirect(request: Request):
    return RedirectResponse("/auth/users", status_code=302)

# --- RUTA DE CAMBIO DE CONTRASEÑA ---
@app.get("/change-password", response_class=HTMLResponse)
async def change_password_redirect(request: Request):
    return RedirectResponse("/auth/change-password-page", status_code=302)


# --- RUTA DE LOADING ---
@app.get("/loading", response_class=HTMLResponse)
async def loading_page(request: Request):
    """Página de inicio con loading para Render"""
    with open("app/templates/loading.html", "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())
    
    
    # --- Health check para monitores ---
@app.get("/ping")
@app.head("/ping")
async def ping():
    return {"status": "ok"}


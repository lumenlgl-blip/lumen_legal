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
    "uploads/constancias",
    "uploads/receipts",
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
        public_paths = [
            "/auth/login",
            "/auth/logout",
            "/auth/check-session",
            "/auth/change-password-page",
            "/setup-admin",
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
app.include_router(auth.router)

# --- FUNCIÓN PARA OBTENER USUARIO ACTUAL ---
from app.routers.auth import get_current_user

# --- RUTA RAÍZ ---
@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    user = get_current_user(request)
    
    if not user:
        return RedirectResponse("/auth/login", status_code=302)
    
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
async def list_users_redirect(request: Request):
    return RedirectResponse("/auth/users", status_code=302)

# --- RUTA DE CAMBIO DE CONTRASEÑA ---
@app.get("/change-password", response_class=HTMLResponse)
async def change_password_redirect(request: Request):
    return RedirectResponse("/auth/change-password-page", status_code=302)


# --- ENDPOINT TEMPORAL PARA CREAR ADMIN (BORRAR DESPUÉS) ---
@app.get("/setup-admin")
async def setup_admin():
    from app.database import SessionLocal
    from app.models.core import Firm, User
    from passlib.context import CryptContext
    
    pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
    db = SessionLocal()
    
    # Verificar si ya existe
    existing = db.query(User).filter(User.email == "admin@lumenlegal.com").first()
    if existing:
        db.close()
        return HTMLResponse("<h1>Admin ya existe</h1>")
    
    firm = db.query(Firm).first()
    if not firm:
        firm = Firm(name="Lumen Legal")
        db.add(firm)
        db.commit()
        db.refresh(firm)
    
    admin = User(
        firm_id=firm.id,
        full_name="Administrador",
        email="admin@lumenlegal.com",
        hashed_password=pwd_context.hash("admin123"),
        role="admin",
        is_active=True,
        must_change_password=False,
        permissions="all"
    )
    db.add(admin)
    db.commit()
    db.close()
    
    return HTMLResponse("""
    <h1>✅ Admin creado exitosamente</h1>
    <p>Email: admin@lumenlegal.com</p>
    <p>Contraseña: admin123</p>
    <p><strong>IMPORTANTE: Borra este endpoint después de usarlo.</strong></p>
    <a href="/auth/login">Ir al login</a>
    """)
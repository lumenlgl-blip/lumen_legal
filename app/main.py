from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi.middleware.cors import CORSMiddleware
import os
from dotenv import load_dotenv
from app.database import engine, Base
from app.models import core

# Cargar variables de entorno
load_dotenv()

# Inicializar la app
app = FastAPI(title="Lumen Legal", version="1.0.0")

# 🔥 CORS — permite subidas desde el móvil vía QR
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================================
# CARPETAS NECESARIAS
# ============================================================
# Solo usamos temp/ para archivos efímeros (PDFs generados en memoria).
# Los archivos persistentes van a Cloudflare R2 (ver app/storage.py).
REQUIRED_DIRS = ["temp"]
for directory in REQUIRED_DIRS:
    os.makedirs(directory, exist_ok=True)

# ============================================================
# TEMPLATES Y ESTÁTICOS
# ============================================================
templates = Jinja2Templates(directory="app/templates")
app.mount("/static", StaticFiles(directory="app/static"), name="static")
# ❌ Ya NO montamos /uploads — los archivos viven en R2

# ============================================================
# CREAR TABLAS AL INICIAR (Supabase)
# ============================================================
Base.metadata.create_all(bind=engine)


# ============================================================
# STARTUP: limpieza + auto-backup
# ============================================================
import asyncio

@app.on_event("startup")
async def startup_tasks():
    """
    Arranca las tareas automáticas del sistema:
      1. Limpieza de temporales locales (inmediata + cada 6h)
      2. Auto-backup (cada N días, con rotación)
    """
    from app.cleanup import cleanup_all
    from app.config import settings

    # ------------------------------------------------------------
    # 1. LIMPIEZA DE TEMPORALES
    # ------------------------------------------------------------
    cleanup_all()  # limpieza inmediata al arrancar

    async def periodic_cleanup():
        while True:
            await asyncio.sleep(6 * 3600)  # 6 horas
            try:
                cleanup_all()
            except Exception as e:
                print(f"⚠️ Error en limpieza programada: {e}")

    asyncio.create_task(periodic_cleanup())
    print("🧹 Limpieza automática activada (cada 6 horas)")

    # ------------------------------------------------------------
    # 2. AUTO-BACKUP
    # ------------------------------------------------------------
    if settings.BACKUP_AUTO_ENABLED:
        from app.database import SessionLocal
        from app.models import core
        from app import backup_service as bs

        async def periodic_backup():
            # Esperar 1 min al arranque para no bloquear startup
            await asyncio.sleep(60)

            while True:
                try:
                    db = SessionLocal()
                    try:
                        admin = (
                            db.query(core.User)
                            .filter(core.User.role == "admin")
                            .order_by(core.User.id.asc())
                            .first()
                        )
                        admin_id = admin.id if admin else None

                        print("🔄 Iniciando respaldo automático…")
                        result = bs.generar_backup(
                            db=db,
                            user_id=admin_id,
                            notes="📅 Respaldo automático",
                        )

                        b = core.Backup(
                            filename=result["filename"],
                            stored_name=result["stored_name"],
                            size=result["size"],
                            sha256=result["sha256"],
                            num_records=result["num_records"],
                            num_files=result["num_files"],
                            created_by=admin_id,
                            notes="📅 Respaldo automático",
                        )
                        db.add(b)
                        db.commit()
                        print(
                            f"✅ Backup automático: {b.filename} "
                            f"({b.num_records} registros, {b.num_files} archivos)"
                        )

                        # Rotación: mantener solo los últimos N automáticos
                        keep = settings.BACKUP_AUTO_KEEP
                        if keep > 0:
                            autos = (
                                db.query(core.Backup)
                                .filter(core.Backup.notes == "📅 Respaldo automático")
                                .order_by(core.Backup.created_at.desc())
                                .all()
                            )
                            for old in autos[keep:]:
                                print(f"🗑️  Eliminando backup viejo: {old.filename}")
                                bs.borrar_backup(db, old.id)

                    finally:
                        db.close()

                except Exception as e:
                    print(f"⚠️ Error en backup automático: {e}")

                # Esperar N días hasta el siguiente
                await asyncio.sleep(settings.BACKUP_AUTO_EVERY_DAYS * 24 * 3600)

        asyncio.create_task(periodic_backup())
        print(
            f"📅 Auto-backup activado cada {settings.BACKUP_AUTO_EVERY_DAYS} días "
            f"(retiene los últimos {settings.BACKUP_AUTO_KEEP})"
        )
    else:
        print("⏭️  Auto-backup desactivado (BACKUP_AUTO_ENABLED=false)")


# ============================================================
# MIDDLEWARE DE AUTENTICACIÓN
# ============================================================
class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        public_paths = [
            "/auth/login",
            "/auth/logout",
            "/auth/check-session",
            "/auth/change-password-page",
            "/auth/change-password-login",
            "/setup-admin",
            "/static",
            "/docs",
            "/openapi.json",
            "/redoc",
            "/loading",
            "/ping",
            "/api/clients/upload-mobile",
            "/api/clients/check-upload",
            "/api/clients/upload-mobile-page",
            "/api/clients/check-qr-status",
        ]

        for path in public_paths:
            if request.url.path.startswith(path):
                return await call_next(request)

        token = request.cookies.get("access_token")
        if not token:
            return RedirectResponse("/auth/login", status_code=302)

        return await call_next(request)

app.add_middleware(AuthMiddleware)


# ============================================================
# ROUTERS
# ============================================================
from app.routers import (
    health, clients, cases, contracts, payments,
    actuaciones, auth, agenda, dashboard, audit, activity, backup
)

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


# ============================================================
# FUNCIÓN PARA OBTENER USUARIO ACTUAL
# ============================================================
from app.routers.auth import get_current_user


# ============================================================
# RUTA RAÍZ
# ============================================================
@app.get("/", response_class=HTMLResponse)
@app.head("/", response_class=HTMLResponse)
async def root(request: Request):
    user = get_current_user(request)

    if not user:
        return RedirectResponse("/loading", status_code=302)

    # Refrescar usuario desde la BD
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
    if user.role == "admin" or user.permissions == "all":
        user_permissions = [
            "clients", "contracts", "cases", "payments",
            "actuaciones", "consult", "agenda", "dashboard",
            "audit", "admin"
        ]
    else:
        user_permissions = user.permissions.split(",") if user.permissions else []

    return templates.TemplateResponse("index.html", {
        "request": request,
        "user": user,
        "permissions": user_permissions
    })


# ============================================================
# RUTA DE PERFIL
# ============================================================
@app.get("/profile", response_class=HTMLResponse)
async def profile(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/auth/login", status_code=302)
    return templates.TemplateResponse("profile.html", {"request": request, "user": user})


# ============================================================
# REDIRECTS
# ============================================================
@app.get("/users", response_class=HTMLResponse)
async def list_users_redirect(request: Request):
    return RedirectResponse("/auth/users", status_code=302)


@app.get("/change-password", response_class=HTMLResponse)
async def change_password_redirect(request: Request):
    return RedirectResponse("/auth/change-password-page", status_code=302)


# ============================================================
# LOADING PAGE
# ============================================================
@app.get("/loading", response_class=HTMLResponse)
async def loading_page(request: Request):
    """Pantalla de carga inicial para Render."""
    with open("app/templates/loading.html", "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())


# ============================================================
# HEALTH CHECK
# ============================================================
@app.get("/ping")
@app.head("/ping")
async def ping():
    return {"status": "ok"}
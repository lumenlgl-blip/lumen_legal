from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
import os
from dotenv import load_dotenv

# Cargar variables de entorno
load_dotenv()

# Inicializar la app
app = FastAPI(title="Lumen Legal", version="1.0.0")

# Configurar rutas de templates y archivos estáticos (aunque estén vacíos, los dejamos listos)
templates = Jinja2Templates(directory="app/templates")
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# --- Importar routers después de crear la app ---
from app.routers import health
app.include_router(health.router, prefix="/api")

# --- Crear tablas al iniciar (solo para desarrollo) ---
from app.database import engine, Base
from app.models import core  # Importamos para que registre las tablas

Base.metadata.create_all(bind=engine)

# --- Ruta raíz (página de bienvenida) ---
@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    # Renderizamos un HTML básico para comprobar que el frontend sirve
    return templates.TemplateResponse("index.html", {"request": request})
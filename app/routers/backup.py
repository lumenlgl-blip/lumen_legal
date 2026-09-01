from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session
from app.database import get_db
import os
import shutil
from datetime import datetime
import zipfile
import io

router = APIRouter(prefix="/backup", tags=["Backup"])

@router.get("/", response_class=HTMLResponse)
async def backup_page(request: Request):
    with open("app/templates/backup.html", "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())

@router.get("/download")
async def download_backup():
    """Crea y descarga un ZIP con la BD y archivos"""
    backup_buffer = io.BytesIO()
    
    with zipfile.ZipFile(backup_buffer, 'w', zipfile.ZIP_DEFLATED) as zipf:
        # Base de datos
        db_path = "lumen_legal.db"
        if os.path.exists(db_path):
            zipf.write(db_path, "lumen_legal.db")
        
        # Archivos subidos
        for root, dirs, files in os.walk("uploads"):
            for file in files:
                file_path = os.path.join(root, file)
                arcname = os.path.relpath(file_path, os.getcwd())
                zipf.write(file_path, arcname)
    
    backup_buffer.seek(0)
    filename = f"backup_lumen_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
    
    return Response(
        content=backup_buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )
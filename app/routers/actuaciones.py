from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import HTMLResponse, Response, FileResponse
from sqlalchemy.orm import Session
from sqlalchemy import or_, desc
from app.database import get_db
from app.models.core import Client, Contract, CourtCase, Actuacion
import os, uuid
from datetime import datetime
import aiofiles

router = APIRouter(prefix="/actuaciones", tags=["Actuaciones"])

UPLOAD_DIR = "uploads/actuaciones"
os.makedirs(UPLOAD_DIR, exist_ok=True)

TIPOS_ACTUACION = [
    "demanda", "acuerdo", "emplazamiento", "audiencia",
    "diligencia_emplazamiento", "diligencia_notificacion", "sentencia",
    "oficio", "exhorto", "promocion", "escrito", "otro"
]

TIPO_ACTUACION_MAP = {
    "demanda": "Demanda Inicial",
    "acuerdo": "Acuerdo",
    "emplazamiento": "Emplazamiento",
    "audiencia": "Audiencia",
    "diligencia_emplazamiento": "Diligencia de Emplazamiento",
    "diligencia_notificacion": "Diligencia de Notificación",
    "sentencia": "Sentencia",
    "oficio": "Oficio",
    "exhorto": "Exhorto",
    "promocion": "Promoción",
    "escrito": "Escrito",
    "otro": "Otro"
}

def get_tipo_label(tipo):
    return TIPO_ACTUACION_MAP.get(tipo, tipo)

def normalize_pdf_url(url):
    """Normaliza la URL del PDF para que sea absoluta desde /uploads/actuaciones/"""
    if not url:
        return None
    if url.startswith("/uploads/actuaciones/"):
        return url
    if url.startswith("/") and not url.startswith("/uploads/"):
        return "/uploads/actuaciones" + url
    return "/uploads/actuaciones/" + url.replace("\\", "/")

async def save_upload_file(upload_file: UploadFile, folder: str, filename: str) -> str:
    full_folder = os.path.join(UPLOAD_DIR, folder)
    os.makedirs(full_folder, exist_ok=True)
    file_path = os.path.join(full_folder, filename)
    async with aiofiles.open(file_path, "wb") as out_file:
        content = await upload_file.read()
        await out_file.write(content)
    return "/" + file_path.replace("\\", "/")

@router.get("/register", response_class=HTMLResponse)
async def show_actuacion_form():
    try:
        with open("app/templates/register_actuacion.html", "r", encoding="utf-8") as f:
            html_content = f.read()
        return HTMLResponse(content=html_content)
    except FileNotFoundError:
        return HTMLResponse(content="<h1>Error: Template no encontrado</h1>")

@router.post("/search-client")
@router.post("/search-client")
async def search_client_with_cases(
    search_term: str = Form(...),
    db: Session = Depends(get_db)
):
    # Primero buscar por número de expediente del tribunal
    court_cases = db.query(CourtCase).filter(
        CourtCase.num_exp_tribunal.ilike(f"%{search_term}%")
    ).all()
    
    # Buscar clientes por otros campos
    clients = db.query(Client).filter(
        or_(
            Client.name.ilike(f"%{search_term}%"),
            Client.paterno.ilike(f"%{search_term}%"),
            Client.materno.ilike(f"%{search_term}%"),
            Client.curp.ilike(f"%{search_term}%"),
            Client.phone.ilike(f"%{search_term}%"),
            Client.folio_registro.ilike(f"%{search_term}%"),
            Client.expediente_interno == (int(search_term) if search_term.isdigit() else -1)
        )
    ).all()
    
    # Crear set de clientes ya encontrados
    found_client_ids = set()
    result = []
    
    # Agregar clientes encontrados por expediente del tribunal
    for case in court_cases:
        contract = db.query(Contract).filter(Contract.id == case.contract_id).first()
        if contract and contract.client_id not in found_client_ids:
            found_client_ids.add(contract.client_id)
            client = db.query(Client).filter(Client.id == contract.client_id).first()
            if client:
                cases_data = get_cases_for_client(client.id, db)
                if cases_data:
                    result.append({
                        "id": client.id,
                        "nombre_completo": f"{client.name} {client.paterno} {client.materno or ''}",
                        "curp": client.curp,
                        "telefono": client.phone,
                        "expediente_interno": client.expediente_interno,
                        "folio_registro": client.folio_registro,
                        "casos": cases_data
                    })
    
    # Agregar clientes encontrados por búsqueda normal
    for c in clients:
        if c.id not in found_client_ids:
            found_client_ids.add(c.id)
            cases_data = get_cases_for_client(c.id, db)
            if cases_data:
                result.append({
                    "id": c.id,
                    "nombre_completo": f"{c.name} {c.paterno} {c.materno or ''}",
                    "curp": c.curp,
                    "telefono": c.phone,
                    "expediente_interno": c.expediente_interno,
                    "folio_registro": c.folio_registro,
                    "casos": cases_data
                })
    
    if not result:
        raise HTTPException(404, "No se encontraron clientes con expedientes")
    
    return result


def get_cases_for_client(client_id: int, db: Session):
    """Obtiene los casos del cliente"""
    contracts = db.query(Contract).filter(Contract.client_id == client_id).all()
    cases_data = []
    for contract in contracts:
        court_case = db.query(CourtCase).filter(CourtCase.contract_id == contract.id).first()
        if court_case:
            cases_data.append({
                "id": court_case.id,
                "expediente_tribunal": court_case.num_exp_tribunal,
                "tribunal": court_case.tribunal,
                "status": court_case.status
            })
    return cases_data
@router.get("/case/{court_case_id}")
async def get_case_actuaciones(court_case_id: int, db: Session = Depends(get_db)):
    court_case = db.query(CourtCase).filter(CourtCase.id == court_case_id).first()
    if not court_case:
        raise HTTPException(404, "Expediente no encontrado")

    actuaciones = db.query(Actuacion).filter(
        Actuacion.court_case_id == court_case_id
    ).order_by(Actuacion.fecha_actuacion.desc()).all()

    actuaciones_data = []
    for act in actuaciones:
        pdf_url = normalize_pdf_url(act.pdf_url)
        actuaciones_data.append({
            "id": act.id,
            "tipo": act.tipo,
            "tipo_label": get_tipo_label(act.tipo),
            "fecha": act.fecha_actuacion.strftime("%d/%m/%Y"),
            "descripcion": act.descripcion or "",
            "pdf_url": pdf_url,
            "subido_por": act.uploaded_by,
            "subido_en": act.uploaded_at.strftime("%d/%m/%Y %H:%M")
        })

    return {
        "id": court_case.id,
        "num_exp_tribunal": court_case.num_exp_tribunal,
        "tribunal": court_case.tribunal,
        "status": court_case.status,
        "actuaciones": actuaciones_data
    }

@router.post("/register/{court_case_id}")
async def register_actuacion(
    court_case_id: int,
    tipo: str = Form(...),
    fecha_actuacion: str = Form(...),
    descripcion: str = Form(None),
    pdf_file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    court_case = db.query(CourtCase).filter(CourtCase.id == court_case_id).first()
    if not court_case:
        raise HTTPException(404, "Expediente no encontrado")

    if tipo not in TIPOS_ACTUACION:
        raise HTTPException(400, f"Tipo de actuación no válido")

    filename = f"actuacion_{court_case_id}_{uuid.uuid4().hex[:8]}.pdf"
    pdf_path = await save_upload_file(pdf_file, str(court_case_id), filename)

    new_actuacion = Actuacion(
        court_case_id=court_case_id,
        firm_id=1,
        tipo=tipo,
        fecha_actuacion=datetime.strptime(fecha_actuacion, "%Y-%m-%d").date(),
        descripcion=descripcion,
        pdf_url=pdf_path,
        uploaded_by=1,
        uploaded_at=datetime.utcnow()
    )
    db.add(new_actuacion)
    db.commit()
    db.refresh(new_actuacion)

    return {
        "message": "Actuación registrada exitosamente",
        "id": new_actuacion.id,
        "tipo": new_actuacion.tipo,
        "fecha": new_actuacion.fecha_actuacion.strftime("%d/%m/%Y"),
        "pdf_url": normalize_pdf_url(new_actuacion.pdf_url)
    }

@router.delete("/delete/{actuacion_id}")
async def delete_actuacion(actuacion_id: int, db: Session = Depends(get_db)):
    actuacion = db.query(Actuacion).filter(Actuacion.id == actuacion_id).first()
    if not actuacion:
        raise HTTPException(404, "Actuación no encontrada")

    # Eliminar archivo físico
    if actuacion.pdf_url:
        file_path = actuacion.pdf_url
        if file_path.startswith("/"):
            file_path = file_path[1:]
        file_path = file_path.replace("/", os.sep)
        if os.path.exists(file_path):
            os.remove(file_path)

    db.delete(actuacion)
    db.commit()

    return {"message": "Actuación eliminada correctamente"}

@router.get("/preview/{actuacion_id}")
async def preview_actuacion(actuacion_id: int, db: Session = Depends(get_db)):
    actuacion = db.query(Actuacion).filter(Actuacion.id == actuacion_id).first()
    if not actuacion:
        raise HTTPException(404, "Actuación no encontrada")

    if actuacion.pdf_url.startswith("/"):
        file_path = actuacion.pdf_url[1:]
        file_path = file_path.replace("/", os.sep)
    else:
        file_path = actuacion.pdf_url

    if not os.path.exists(file_path):
        raise HTTPException(404, "Archivo PDF no encontrado")

    return FileResponse(file_path, media_type="application/pdf")
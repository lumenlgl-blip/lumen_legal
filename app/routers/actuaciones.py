from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Request
from app.routers.auth import get_current_user
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from sqlalchemy import or_
from app.database import get_db
from app.models.core import Client, Contract, CourtCase, Actuacion
from app.storage import upload_fileobj, delete_file, get_file_url
import uuid
from datetime import datetime

router = APIRouter(prefix="/actuaciones", tags=["Actuaciones"])

# ❌ UPLOAD_DIR y save_upload_file eliminados: todo va a R2


# ============================================================
# CATÁLOGOS
# ============================================================
TIPOS_ACTUACION = [
    "demanda", "acuerdo", "emplazamiento", "audiencia",
    "diligencia_emplazamiento", "diligencia_notificacion", "sentencia",
    "oficio", "exhorto", "promocion", "escrito", "otro",
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
    "otro": "Otro",
}


def get_tipo_label(tipo):
    return TIPO_ACTUACION_MAP.get(tipo, tipo)


# ============================================================
# FORMULARIO
# ============================================================
@router.get("/register", response_class=HTMLResponse)
async def show_actuacion_form():
    try:
        with open("app/templates/register_actuacion.html", "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except FileNotFoundError:
        return HTMLResponse(content="<h1>Error: Template no encontrado</h1>")


# ============================================================
# BÚSQUEDA DE CLIENTES CON EXPEDIENTES
# ============================================================
@router.post("/search-client")
async def search_client_with_cases(
    search_term: str = Form(...), db: Session = Depends(get_db)
):
    # Buscar por número de expediente del tribunal
    court_cases = (
        db.query(CourtCase)
        .filter(CourtCase.num_exp_tribunal.ilike(f"%{search_term}%"))
        .all()
    )

    # Buscar clientes por otros campos
    clients = (
        db.query(Client)
        .filter(
            or_(
                Client.name.ilike(f"%{search_term}%"),
                Client.paterno.ilike(f"%{search_term}%"),
                Client.materno.ilike(f"%{search_term}%"),
                Client.curp.ilike(f"%{search_term}%"),
                Client.phone.ilike(f"%{search_term}%"),
                Client.folio_registro.ilike(f"%{search_term}%"),
                Client.expediente_interno
                == (int(search_term) if search_term.isdigit() else -1),
            )
        )
        .all()
    )

    found_client_ids = set()
    result = []

    # Clientes encontrados por expediente del tribunal
    for case in court_cases:
        contract = db.query(Contract).filter(Contract.id == case.contract_id).first()
        if contract and contract.client_id not in found_client_ids:
            found_client_ids.add(contract.client_id)
            client = db.query(Client).filter(Client.id == contract.client_id).first()
            if client:
                cases_data = get_cases_for_client(client.id, db)
                if cases_data:
                    result.append(
                        {
                            "id": client.id,
                            "nombre_completo": f"{client.name} {client.paterno} {client.materno or ''}",
                            "curp": client.curp,
                            "telefono": client.phone,
                            "expediente_interno": client.expediente_interno,
                            "folio_registro": client.folio_registro,
                            "casos": cases_data,
                        }
                    )

    # Clientes encontrados por búsqueda normal
    for c in clients:
        if c.id not in found_client_ids:
            found_client_ids.add(c.id)
            cases_data = get_cases_for_client(c.id, db)
            if cases_data:
                result.append(
                    {
                        "id": c.id,
                        "nombre_completo": f"{c.name} {c.paterno} {c.materno or ''}",
                        "curp": c.curp,
                        "telefono": c.phone,
                        "expediente_interno": c.expediente_interno,
                        "folio_registro": c.folio_registro,
                        "casos": cases_data,
                    }
                )

    if not result:
        raise HTTPException(404, "No se encontraron clientes con expedientes")

    return result


def get_cases_for_client(client_id: int, db: Session):
    contracts = db.query(Contract).filter(Contract.client_id == client_id).all()
    cases_data = []
    for contract in contracts:
        court_case = (
            db.query(CourtCase).filter(CourtCase.contract_id == contract.id).first()
        )
        if court_case:
            cases_data.append(
                {
                    "id": court_case.id,
                    "expediente_tribunal": court_case.num_exp_tribunal,
                    "tribunal": court_case.tribunal,
                    "status": court_case.status,
                }
            )
    return cases_data


# ============================================================
# LISTAR ACTUACIONES DE UN EXPEDIENTE
# ============================================================
@router.get("/case/{court_case_id}")
async def get_case_actuaciones(court_case_id: int, db: Session = Depends(get_db)):
    court_case = db.query(CourtCase).filter(CourtCase.id == court_case_id).first()
    if not court_case:
        raise HTTPException(404, "Expediente no encontrado")

    # Orden cronológico ascendente: la primera actuación arriba,
    # las más recientes abajo. Desempate por id (la más antigua fue creada primero).
    actuaciones = (
        db.query(Actuacion)
        .filter(Actuacion.court_case_id == court_case_id)
        .order_by(Actuacion.fecha_actuacion.asc(), Actuacion.id.asc())
        .all()
    )

    actuaciones_data = []
    for act in actuaciones:
        # URL prefirmada de R2 (válida 1 hora)
        pdf_url = get_file_url(act.pdf_url, expires_in=3600) if act.pdf_url else None
        actuaciones_data.append(
            {
                "id": act.id,
                "tipo": act.tipo,
                "tipo_label": get_tipo_label(act.tipo),
                "fecha": act.fecha_actuacion.strftime("%d/%m/%Y"),
                "descripcion": act.descripcion or "",
                "pdf_url": pdf_url,
                "subido_por": act.uploaded_by,
                "subido_en": act.uploaded_at.strftime("%d/%m/%Y %H:%M"),
            }
        )

    return {
        "id": court_case.id,
        "num_exp_tribunal": court_case.num_exp_tribunal,
        "tribunal": court_case.tribunal,
        "secretaria": getattr(court_case, "secretaria", None),
        "folio_tribunal": getattr(court_case, "folio_tribunal", None),
        "fecha_presentacion": (
            court_case.fecha_presentacion.strftime("%d/%m/%Y")
            if getattr(court_case, "fecha_presentacion", None) else None
        ),
        "status": court_case.status,
        "actor_nombre": getattr(court_case, "actor_nombre", None),
        "actor2_nombre": getattr(court_case, "actor2_nombre", None),
        "demandado_nombre": getattr(court_case, "demandado_nombre", None),
        "actuaciones": actuaciones_data,
    }


# ============================================================
# REGISTRAR ACTUACIÓN
# ============================================================
@router.post("/register/{court_case_id}")
async def register_actuacion(
    request: Request,
    court_case_id: int,
    tipo: str = Form(...),
    fecha_actuacion: str = Form(...),
    descripcion: str = Form(None),
    pdf_file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    court_case = db.query(CourtCase).filter(CourtCase.id == court_case_id).first()
    if not court_case:
        raise HTTPException(404, "Expediente no encontrado")

    if tipo not in TIPOS_ACTUACION:
        raise HTTPException(400, "Tipo de actuación no válido")

    # ------------------------------------------------------------
    # SUBIR A R2
    # ------------------------------------------------------------
    key = f"expedientes/{court_case_id}/actuacion_{uuid.uuid4().hex[:8]}.pdf"
    folder = key.rsplit("/", 1)[0]
    filename = key.rsplit("/", 1)[-1]
    await pdf_file.seek(0)
    upload_fileobj(pdf_file.file, folder, filename)

    current_user = get_current_user(request, db)
    if not current_user:
        raise HTTPException(401, "No autenticado")

    new_actuacion = Actuacion(
        court_case_id=court_case_id,
        firm_id=current_user.firm_id,
        tipo=tipo,
        fecha_actuacion=datetime.strptime(fecha_actuacion, "%Y-%m-%d").date(),
        descripcion=descripcion,
        pdf_url=key,
        uploaded_by=current_user.id,
        uploaded_at=datetime.utcnow(),
    )
    db.add(new_actuacion)
    db.commit()
    db.refresh(new_actuacion)

    return {
        "message": "Actuación registrada exitosamente",
        "id": new_actuacion.id,
        "tipo": new_actuacion.tipo,
        "fecha": new_actuacion.fecha_actuacion.strftime("%d/%m/%Y"),
        "pdf_url": get_file_url(new_actuacion.pdf_url, expires_in=3600),
    }


# ============================================================
# ELIMINAR ACTUACIÓN
# ============================================================
@router.delete("/delete/{actuacion_id}")
async def delete_actuacion(actuacion_id: int, db: Session = Depends(get_db)):
    actuacion = db.query(Actuacion).filter(Actuacion.id == actuacion_id).first()
    if not actuacion:
        raise HTTPException(404, "Actuación no encontrada")

    # Borrar archivo de R2
    if actuacion.pdf_url:
        delete_file(actuacion.pdf_url)

    db.delete(actuacion)
    db.commit()

    return {"message": "Actuación eliminada correctamente"}


# ============================================================
# PREVISUALIZAR ACTUACIÓN (redirige a URL prefirmada de R2)
# ============================================================
@router.get("/preview/{actuacion_id}")
async def preview_actuacion(actuacion_id: int, db: Session = Depends(get_db)):
    actuacion = db.query(Actuacion).filter(Actuacion.id == actuacion_id).first()
    if not actuacion:
        raise HTTPException(404, "Actuación no encontrada")

    if not actuacion.pdf_url:
        raise HTTPException(404, "Archivo PDF no asociado")

    url = get_file_url(actuacion.pdf_url, expires_in=3600)
    return RedirectResponse(url)
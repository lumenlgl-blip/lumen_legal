from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session
from sqlalchemy import or_
from app.database import get_db
from app.models.core import Client, Contract, CourtCase, Actuacion, User
import os, uuid
from datetime import datetime
import aiofiles


router = APIRouter(prefix="/cases", tags=["Cases"])

UPLOAD_DIR = "uploads/case_docs"
os.makedirs(UPLOAD_DIR, exist_ok=True)

async def save_upload_file(upload_file: UploadFile, subfolder: str, filename: str) -> str:
    folder = os.path.join(UPLOAD_DIR, subfolder)
    os.makedirs(folder, exist_ok=True)
    file_path = os.path.join(folder, filename)
    async with aiofiles.open(file_path, "wb") as out_file:
        content = await upload_file.read()
        await out_file.write(content)
    return file_path

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
                "status": court_case.status,
                "fecha_presentacion": court_case.fecha_presentacion.strftime("%d/%m/%Y"),
                "contract_id": contract.id
            })
    return cases_data

# --- Ruta para mostrar el formulario de relación ---
@router.get("/relate", response_class=HTMLResponse)
async def show_relate_form():
    with open("app/templates/relate_case.html", "r", encoding="utf-8") as f:
        html_content = f.read()
    return HTMLResponse(content=html_content)

# --- Ruta para buscar clientes ---
@router.post("/search-client")
async def search_client(
    search_term: str = Form(...),
    db: Session = Depends(get_db)
):
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
    
    if not clients:
        raise HTTPException(404, "No se encontraron clientes")
    
    result = []
    for c in clients:
        # Verificar si tiene contratos de tipo juicio o contestacion SIN expediente
        has_pending_contract = db.query(Contract).filter(
            Contract.client_id == c.id,
            Contract.service_type.in_(["juicio", "contestacion"]),
            ~Contract.court_case.has()
        ).first() is not None
        
        if has_pending_contract:
            result.append({
                "id": c.id,
                "nombre_completo": f"{c.name} {c.paterno} {c.materno or ''}",
                "curp": c.curp,
                "telefono": c.phone,
                "expediente_interno": c.expediente_interno,
                "folio_registro": c.folio_registro
            })
    
    if not result:
        raise HTTPException(404, "No se encontraron clientes con contrataciones pendientes de relacionar")
    
    return result

# --- Ruta para obtener un cliente específico ---
@router.get("/client/{client_id}")
async def get_client(client_id: int, db: Session = Depends(get_db)):
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(404, "Cliente no encontrado")
    
    # Solo contratos de tipo juicio o contestacion SIN expediente relacionado
    contracts = db.query(Contract).filter(
        Contract.client_id == client_id,
        Contract.service_type.in_(["juicio", "contestacion"]),
        ~Contract.court_case.has()
    ).all()
    
    if not contracts:
        raise HTTPException(404, "Este cliente no tiene contrataciones de juicio pendientes de relacionar")
    
    contracts_data = []
    for contract in contracts:
        service_type_map = {
            "juicio": "Inicio de Juicio",
            "contestacion": "Contestación de Demanda"
        }
        service_label = service_type_map.get(contract.service_type, contract.service_type)
        
        tipo_juicio_display = contract.tipo_juicio or ""
        if contract.tipo_juicio_otro:
            tipo_juicio_display = contract.tipo_juicio_otro
        
        contracts_data.append({
            "id": contract.id,
            "servicio": service_label,
            "tipo_juicio": tipo_juicio_display,
            "detalle": contract.specific_detail or "",
            "costo_total": float(contract.total_cost),
            "estatus": contract.status
        })
    
    return {
        "id": client.id,
        "nombre_completo": f"{client.name} {client.paterno} {client.materno or ''}",
        "curp": client.curp,
        "telefono": client.phone,
        "expediente_interno": client.expediente_interno,
        "folio_registro": client.folio_registro,
        "domicilio": client.address,
        "ocupacion": client.occupation,
        "contratos": contracts_data
    }
# --- Ruta para relacionar expediente ---
@router.post("/relate/{client_id}")
async def relate_case(
    client_id: int,
    tribunal: str = Form(...),
    secretaria: str = Form(...),
    num_exp_tribunal: str = Form(...),
    folio_tribunal: str = Form(...),
    fecha_presentacion: str = Form(...),
    tipo_juicio: str = Form(None),
    demandado_nombre: str = Form(None),
    actor_nombre: str = Form(None),
    acuse_file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(404, "Cliente no encontrado")
    
    # Verificar que exista un contrato de tipo juicio o contestacion pendiente
    contract = db.query(Contract).filter(
        Contract.client_id == client_id,
        Contract.service_type.in_(["juicio", "contestacion"]),
        ~Contract.court_case.has()
    ).first()
    
    if not contract:
        raise HTTPException(400, "Este cliente no tiene contrataciones de juicio pendientes de relacionar")
    
    existing_case = db.query(CourtCase).filter(CourtCase.contract_id == contract.id).first()
    if existing_case:
        raise HTTPException(400, "Este cliente ya tiene un expediente relacionado")
    
    acuse_filename = f"acuse_{client.folio_registro}_{uuid.uuid4().hex[:8]}.pdf"
    acuse_path = await save_upload_file(acuse_file, f"acuses/{client.folio_registro}", acuse_filename)
    
    new_case = CourtCase(
        contract_id=contract.id,
        firm_id=1,
        tribunal=tribunal,
        secretaria=secretaria,
        num_exp_tribunal=num_exp_tribunal,
        folio_tribunal=folio_tribunal,
        fecha_presentacion=datetime.strptime(fecha_presentacion, "%Y-%m-%d").date(),
        acuse_pdf_url=acuse_path,
        demandado_nombre=demandado_nombre,
        actor_nombre=actor_nombre,
        status="iniciado"
    )
    db.add(new_case)
    db.commit()
    db.refresh(new_case)
    
    # Registrar en bitácora
    from app.models.core import ActivityLog
    log = ActivityLog(
        firm_id=1,
        user_id=1,
        action="create",
        entity="Expediente",
        entity_id=new_case.id,
        description=f"Relacionó expediente {num_exp_tribunal}"
    )
    db.add(log)
    db.commit()
    
    return {
        "message": "Expediente relacionado exitosamente",
        "cliente": f"{client.name} {client.paterno}",
        "expediente_tribunal": num_exp_tribunal,
        "folio": folio_tribunal
    }

# ================================================================
# RUTAS PARA ACTUALIZAR ESTATUS
# ================================================================

@router.get("/status", response_class=HTMLResponse)
async def show_status_form():
    try:
        with open("app/templates/update_status.html", "r", encoding="utf-8") as f:
            html_content = f.read()
        return HTMLResponse(content=html_content)
    except FileNotFoundError:
        return HTMLResponse(content="<h1>Error: Template no encontrado</h1>")

@router.post("/search-client-status")
async def search_client_for_status(
    search_term: str = Form(...),
    db: Session = Depends(get_db)
):
    court_cases_by_exp = db.query(CourtCase).filter(
        CourtCase.num_exp_tribunal.ilike(f"%{search_term}%")
    ).all()
    
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
    
    found_client_ids = set()
    result = []
    
    for court_case in court_cases_by_exp:
        contract = db.query(Contract).filter(Contract.id == court_case.contract_id).first()
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

@router.put("/status/{court_case_id}")
async def update_case_status(
    court_case_id: int,
    new_status: str = Form(...),
    db: Session = Depends(get_db)
):
    court_case = db.query(CourtCase).filter(CourtCase.id == court_case_id).first()
    if not court_case:
        raise HTTPException(404, "Expediente no encontrado")
    
    valid_statuses = ["iniciado", "terminado", "inactivo", "desistido", "caducado", "revocado", "cliente_no_contesta", "otro"]
    if new_status not in valid_statuses:
        raise HTTPException(400, f"Estatus no válido. Opciones: {', '.join(valid_statuses)}")
    
    court_case.status = new_status
    db.commit()
    db.refresh(court_case)
    
    return {
        "message": "Estatus actualizado exitosamente",
        "id": court_case.id,
        "nuevo_estatus": court_case.status,
        "expediente": court_case.num_exp_tribunal
    }

# ================================================================
# RUTAS PARA CONSULTA DE EXPEDIENTE
# ================================================================

@router.get("/consult", response_class=HTMLResponse)
async def show_consult_case():
    try:
        with open("app/templates/consult_case.html", "r", encoding="utf-8") as f:
            html_content = f.read()
        return HTMLResponse(content=html_content)
    except FileNotFoundError:
        return HTMLResponse(content="<h1>Error: Template no encontrado</h1>")

@router.post("/search-client-consult")
async def search_client_for_consult(
    search_term: str = Form(...),
    db: Session = Depends(get_db)
):
    court_cases_by_exp = db.query(CourtCase).filter(
        CourtCase.num_exp_tribunal.ilike(f"%{search_term}%")
    ).all()
    
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
    
    found_client_ids = set()
    result = []
    
    for court_case in court_cases_by_exp:
        contract = db.query(Contract).filter(Contract.id == court_case.contract_id).first()
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

@router.get("/consult-detail/{court_case_id}")
async def get_case_full_detail(court_case_id: int, db: Session = Depends(get_db)):
    court_case = db.query(CourtCase).filter(CourtCase.id == court_case_id).first()
    if not court_case:
        raise HTTPException(404, "Expediente no encontrado")
    
    contract = db.query(Contract).filter(Contract.id == court_case.contract_id).first()
    client = db.query(Client).filter(Client.id == contract.client_id).first()
    
    actuaciones = db.query(Actuacion).filter(
        Actuacion.court_case_id == court_case_id
    ).order_by(Actuacion.fecha_actuacion).all()
    
    actuaciones_data = []
    for act in actuaciones:
        actuaciones_data.append({
            "id": act.id,
            "tipo": act.tipo,
            "fecha": act.fecha_actuacion.strftime("%d/%m/%Y"),
            "descripcion": act.descripcion or "",
            "pdf_url": act.pdf_url,
            "subido_en": act.uploaded_at.strftime("%d/%m/%Y %H:%M")
        })
    
    return {
        "cliente": {
            "id": client.id,
            "nombre_completo": f"{client.name} {client.paterno} {client.materno or ''}",
            "curp": client.curp,
            "telefono": client.phone,
            "email": client.email or "N/A",
            "domicilio": client.address,
            "ocupacion": client.occupation,
            "expediente_interno": client.expediente_interno,
            "folio_registro": client.folio_registro
        },
        "contrato": {
            "id": contract.id,
            "service_type": contract.service_type,
            "specific_detail": contract.specific_detail or "",
            "tipo_juicio": contract.tipo_juicio or "",
            "tipo_juicio_otro": contract.tipo_juicio_otro or "",
            "total_cost": float(contract.total_cost),
            "status": contract.status
        },
        "expediente": {
            "id": court_case.id,
            "tribunal": court_case.tribunal,
            "secretaria": court_case.secretaria,
            "num_exp_tribunal": court_case.num_exp_tribunal,
            "folio_tribunal": court_case.folio_tribunal,
            "fecha_presentacion": court_case.fecha_presentacion.strftime("%d/%m/%Y"),
            "demandado_nombre": court_case.demandado_nombre or "N/A",
            "actor_nombre": court_case.actor_nombre or "N/A",
            "status": court_case.status,
            "acuse_pdf_url": court_case.acuse_pdf_url
        },
        "actuaciones": actuaciones_data
    }

@router.post("/verify-password")
async def verify_user_password(
    request: Request,
    password: str = Form(...),
    db: Session = Depends(get_db)
):
    from app.routers.auth import get_current_user, verify_password
    
    user = get_current_user(request, db)
    if not user:
        raise HTTPException(401, "No autenticado")
    
    if not verify_password(password, user.hashed_password):
        raise HTTPException(401, "Contraseña incorrecta")
    
    return {"valid": True}

# --- Ruta para generar PDF unificado del expediente completo ---
@router.get("/full-case-pdf/{court_case_id}")
async def get_full_case_pdf(court_case_id: int, db: Session = Depends(get_db)):
    """Genera un PDF único con todos los documentos del expediente"""
    from jinja2 import Environment, FileSystemLoader
    from weasyprint import HTML
    import base64
    from datetime import datetime
    
    court_case = db.query(CourtCase).filter(CourtCase.id == court_case_id).first()
    if not court_case:
        raise HTTPException(404, "Expediente no encontrado")
    
    contract = db.query(Contract).filter(Contract.id == court_case.contract_id).first()
    client = db.query(Client).filter(Client.id == contract.client_id).first()
    
    actuaciones = db.query(Actuacion).filter(
        Actuacion.court_case_id == court_case_id
    ).order_by(Actuacion.fecha_actuacion.asc()).all()
    
    # Ruta del logo
    logo_path = os.path.join(os.getcwd(), "app", "static", "img", "logo.jpeg")
    
    logo_base64 = ""
    if os.path.exists(logo_path):
        with open(logo_path, "rb") as f:
            logo_base64 = base64.b64encode(f.read()).decode('utf-8')
    
    # Cargar plantilla
    env = Environment(loader=FileSystemLoader("app/templates"))
    template = env.get_template("pdf/expediente_completo.html")
    
    actuaciones_data = []
    for act in actuaciones:
        actuaciones_data.append({
            "tipo": act.tipo.upper(),
            "fecha": act.fecha_actuacion.strftime('%d/%m/%Y'),
            "descripcion": act.descripcion or ""
        })
    
    html_content = template.render(
        logo_base64=logo_base64,
        num_expediente=court_case.num_exp_tribunal,
        folio=court_case.folio_tribunal,
        tribunal=court_case.tribunal,
        secretaria=court_case.secretaria,
        fecha_presentacion=court_case.fecha_presentacion.strftime('%d/%m/%Y'),
        estatus=court_case.status.upper(),
        actor=court_case.actor_nombre or 'N/A',
        demandado=court_case.demandado_nombre or 'N/A',
        nombre_completo=f"{client.name} {client.paterno} {client.materno or ''}",
        curp=client.curp,
        telefono=client.phone,
        domicilio=client.address,
        total_actuaciones=len(actuaciones),
        actuaciones=actuaciones_data,
        anio=datetime.now().strftime('%Y')
    )
    
    pdf_bytes = HTML(string=html_content).write_pdf()
    
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f"inline; filename=expediente_completo_{court_case.num_exp_tribunal}.pdf"}
    )
    
    
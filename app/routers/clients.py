from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session
from sqlalchemy import or_, func
from app.database import get_db
from app.models.core import Client, ClientDocument, Contract, Payment, CourtCase
import uuid, os
from datetime import datetime
import aiofiles

router = APIRouter(prefix="/clients", tags=["Clients"])

UPLOAD_DIR = "uploads/client_docs"
os.makedirs(UPLOAD_DIR, exist_ok=True)



def generate_folio():
    return f"CL-{uuid.uuid4().hex[:8].upper()}"

def get_next_internal_expediente(firm_id: int, db: Session):
    max_exp = db.query(Client.expediente_interno).filter(Client.firm_id == firm_id).order_by(Client.expediente_interno.desc()).first()
    return (max_exp[0] + 1) if max_exp else 1

async def save_upload_file(upload_file: UploadFile, subfolder: str, filename: str) -> str:
    folder = os.path.join(UPLOAD_DIR, subfolder)
    os.makedirs(folder, exist_ok=True)
    file_path = os.path.join(folder, filename)
    async with aiofiles.open(file_path, "wb") as out_file:
        content = await upload_file.read()
        await out_file.write(content)
    # Devolver ruta pública absoluta
    return "/" + file_path.replace("\\", "/")

def generate_client_pdf(client_data):
    from jinja2 import Environment, FileSystemLoader
    from weasyprint import HTML
    import base64
    import os
    from datetime import datetime
    
    # Cargar plantilla
    env = Environment(loader=FileSystemLoader("app/templates"))
    template = env.get_template("pdf/constancia_cliente.html")
    
    # Ruta del logo
    logo_path = os.path.join(os.getcwd(), "app", "static", "img", "logo.jpeg")
    
    logo_base64 = ""
    if os.path.exists(logo_path):
        with open(logo_path, "rb") as f:
            logo_base64 = base64.b64encode(f.read()).decode('utf-8')
    
    anio = client_data.created_at.strftime('%Y') if client_data.created_at else datetime.now().strftime('%Y')
    
    html_content = template.render(
        nombre_completo=f"{client_data.name} {client_data.paterno} {client_data.materno or ''}",
        curp=client_data.curp,
        telefono=client_data.phone,
        email=client_data.email or "",
        domicilio=client_data.address,
        ocupacion=client_data.occupation,
        folio=client_data.folio_registro,
        expediente_interno=client_data.expediente_interno,
        anio=anio,
        fecha_registro=client_data.created_at.strftime('%d/%m/%Y %H:%M') if client_data.created_at else datetime.now().strftime('%d/%m/%Y %H:%M'),
        logo_base64=logo_base64
    )
    
    pdf_bytes = HTML(string=html_content).write_pdf()
    return pdf_bytes

@router.get("/register", response_class=HTMLResponse)
async def show_register_form():
    with open("app/templates/register_client.html", "r", encoding="utf-8") as f:
        html_content = f.read()
    return HTMLResponse(content=html_content)

@router.post("/register")
async def register_client(
    name: str = Form(...),
    paterno: str = Form(...),
    materno: str = Form(None),
    curp: str = Form(...),
    phone: str = Form(...),
    email: str = Form(None),
    address: str = Form(...),
    occupation: str = Form(...),
    curp_file: UploadFile = File(...),
    ine_file: UploadFile = File(...),
    domicilio_file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    # Validar CURP único
    existing = db.query(Client).filter(Client.curp == curp).first()
    if existing:
        anio = existing.created_at.strftime('%Y')
        return {
            "success": False,
            "message": "Cliente ya registrado con ese CURP",
            "existing_client": {
                "folio_registro": existing.folio_registro,
                "expediente_interno": f"{existing.expediente_interno}/{anio}",
                "nombre_completo": f"{existing.name} {existing.paterno} {existing.materno}"
            }
        }
    
    firm_id = 1
    
    folio = generate_folio()
    expediente = get_next_internal_expediente(firm_id, db)
    
    new_client = Client(
        firm_id=firm_id,
        folio_registro=folio,
        expediente_interno=expediente,
        name=name,
        paterno=paterno,
        materno=materno if materno else "",
        curp=curp,
        phone=phone,
        email=email,
        address=address,
        occupation=occupation
    )
    db.add(new_client)
    db.commit()
    db.refresh(new_client)
    
    # Registrar en bitácora
    from app.models.core import ActivityLog
    log = ActivityLog(
        firm_id=firm_id,
        user_id=1,
        action="create",
        entity="Cliente",
        entity_id=new_client.id,
        description=f"Registró al cliente {new_client.name} {new_client.paterno}"
    )
    db.add(log)
    
    # Guardar documentos
    docs = [
        ("CURP", curp_file),
        ("INE", ine_file),
        ("DOMICILIO", domicilio_file)
    ]
    for doc_type, file in docs:
        filename = f"{folio}_{doc_type}.pdf"
        file_path = await save_upload_file(file, folio, filename)
        db_doc = ClientDocument(client_id=new_client.id, doc_type=doc_type, file_url=file_path)
        db.add(db_doc)
    
    db.commit()
    db.refresh(new_client)
    
    # Generar constancia PDF (solo se envía al navegador, no se guarda)
    pdf_bytes = generate_client_pdf(new_client)
    
    anio = new_client.created_at.strftime('%Y')
    
    # Devolver PDF directamente
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=constancia_{folio}.pdf"}
    )



# ================================================================
# RUTAS PARA CONSULTA DE CLIENTE (FEATURE 11)
# ================================================================

@router.get("/consult", response_class=HTMLResponse)
async def show_consult_client():
    try:
        with open("app/templates/consult_client.html", "r", encoding="utf-8") as f:
            html_content = f.read()
        return HTMLResponse(content=html_content)
    except FileNotFoundError:
        return HTMLResponse(content="<h1>Error: Template no encontrado</h1>")

@router.post("/search-client-consult")
async def search_client_for_consult(
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
        contracts = db.query(Contract).filter(Contract.client_id == c.id).all()
        total_debt = 0
        total_contracts = len(contracts)

        for contract in contracts:
            if contract.status == "pendiente":
                total_pagado = db.query(func.sum(Payment.amount)).filter(
                    Payment.contract_id == contract.id
                ).scalar() or 0
                total_pagado = float(total_pagado)
                total_debt += float(contract.total_cost) - total_pagado

        has_case = db.query(CourtCase).join(Contract).filter(Contract.client_id == c.id).first() is not None

        result.append({
            "id": c.id,
            "nombre_completo": f"{c.name} {c.paterno} {c.materno or ''}",
            "curp": c.curp,
            "telefono": c.phone,
            "email": c.email or "N/A",
            "expediente_interno": c.expediente_interno,
            "folio_registro": c.folio_registro,
            "total_contratos": total_contracts,
            "deuda_total": total_debt,
            "has_case": has_case
        })

    return result

@router.get("/consult-detail/{client_id}")
async def get_client_full_detail(client_id: int, db: Session = Depends(get_db)):
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(404, "Cliente no encontrado")

    documents = db.query(ClientDocument).filter(ClientDocument.client_id == client_id).all()
    docs_data = []
    for doc in documents:
        url = doc.file_url
        if url and not url.startswith("/"):
            url = "/" + url.replace("\\", "/")
        docs_data.append({
            "id": doc.id,
            "tipo": doc.doc_type,
            "url": url,
            "fecha": doc.uploaded_at.strftime("%d/%m/%Y %H:%M")
        })

    contracts = db.query(Contract).filter(Contract.client_id == client_id).all()
    contracts_data = []
    for contract in contracts:
        payments = db.query(Payment).filter(Payment.contract_id == contract.id).all()
        total_pagado = sum(p.amount for p in payments) if payments else 0
        total_pagado = float(total_pagado)

        court_case = db.query(CourtCase).filter(CourtCase.contract_id == contract.id).first()
        case_data = None
        if court_case:
            case_data = {
                "id": court_case.id,
                "num_exp_tribunal": court_case.num_exp_tribunal,
                "tribunal": court_case.tribunal,
                "status": court_case.status
            }

        last_payment = None
        if payments:
            last_payment = {
                "monto": float(payments[-1].amount),
                "fecha": payments[-1].payment_date.strftime("%d/%m/%Y")
            }

        contracts_data.append({
            "id": contract.id,
            "service_type": contract.service_type,
            "specific_detail": contract.specific_detail or "",
            "tipo_juicio": contract.tipo_juicio or "",
            "tipo_juicio_otro": contract.tipo_juicio_otro or "",
            "total_cost": float(contract.total_cost),
            "pagado": total_pagado,
            "saldo": float(contract.total_cost) - total_pagado,
            "status": contract.status,
            "created_at": contract.created_at.strftime("%d/%m/%Y"),
            "court_case": case_data,
            "last_payment": last_payment,
            "total_payments": len(payments)
        })

    return {
        "id": client.id,
        "nombre_completo": f"{client.name} {client.paterno} {client.materno or ''}",
        "curp": client.curp,
        "telefono": client.phone,
        "email": client.email or "N/A",
        "domicilio": client.address,
        "ocupacion": client.occupation,
        "expediente_interno": client.expediente_interno,
        "folio_registro": client.folio_registro,
        "created_at": client.created_at.strftime("%d/%m/%Y %H:%M"),
        "documentos": docs_data,
        "contratos": contracts_data
    }

@router.put("/update/{client_id}")
async def update_client(
    client_id: int,
    name: str = Form(...),
    paterno: str = Form(...),
    materno: str = Form(...),
    phone: str = Form(...),
    email: str = Form(None),
    address: str = Form(...),
    occupation: str = Form(...),
    db: Session = Depends(get_db)
):
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(404, "Cliente no encontrado")

    client.name = name
    client.paterno = paterno
    client.materno = materno
    client.phone = phone
    client.email = email
    client.address = address
    client.occupation = occupation

    db.commit()
    db.refresh(client)

    return {
        "message": "Cliente actualizado exitosamente",
        "id": client.id,
        "nombre": f"{client.name} {client.paterno} {client.materno}"
    }
    
@router.post("/replace-doc/{doc_id}")
async def replace_client_document(
    doc_id: int,
    pdf_file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    doc = db.query(ClientDocument).filter(ClientDocument.id == doc_id).first()
    if not doc:
        raise HTTPException(404, "Documento no encontrado")
    
    # Guardar nuevo archivo
    filename = f"{doc.doc_type}_{uuid.uuid4().hex[:8]}.pdf"
    file_path = await save_upload_file(pdf_file, doc.client.folio_registro, filename)
    
    # Eliminar archivo anterior
    if doc.file_url:
        old_path = doc.file_url
        if old_path.startswith("/"):
            old_path = old_path[1:]
        old_path = old_path.replace("/", os.sep)
        if os.path.exists(old_path):
            os.remove(old_path)
    
    doc.file_url = file_path
    db.commit()
    
    return {"message": "Documento reemplazado correctamente"}


# --- Eliminar cliente completo (solo admin) ---
@router.delete("/delete/{client_id}")
async def delete_client(
    client_id: int,
    request: Request,
    password: str = Form(...),
    db: Session = Depends(get_db)
):
    from app.routers.auth import get_current_user, verify_password
    from app.models.core import ActivityLog, CourtCase, Payment, Actuacion, Contract, ClientDocument
    
    # Verificar autenticación
    user = get_current_user(request, db)
    if not user:
        return {"success": False, "message": "No autenticado"}
    
    if user.role != "admin":
        return {"success": False, "message": "Solo administradores pueden eliminar clientes"}
    
    # Verificar contraseña
    if not verify_password(password, user.hashed_password):
        return {"success": False, "message": "Contraseña incorrecta"}
    
    # Buscar cliente
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        return {"success": False, "message": "Cliente no encontrado"}
    
    client_name = f"{client.name} {client.paterno} {client.materno or ''}"
    
    # Eliminar archivos de documentos
    documents = db.query(ClientDocument).filter(ClientDocument.client_id == client_id).all()
    for doc in documents:
        if doc.file_url:
            file_path = doc.file_url
            if file_path.startswith("/"):
                file_path = file_path[1:]
            file_path = file_path.replace("/", os.sep)
            if os.path.exists(file_path):
                os.remove(file_path)
    
    # Obtener contratos del cliente
    contracts = db.query(Contract).filter(Contract.client_id == client_id).all()
    
    for contract in contracts:
        # Eliminar pagos del contrato
        db.query(Payment).filter(Payment.contract_id == contract.id).delete()
        
        # Eliminar expediente del contrato
        court_case = db.query(CourtCase).filter(CourtCase.contract_id == contract.id).first()
        if court_case:
            # Eliminar actuaciones
            actuaciones = db.query(Actuacion).filter(Actuacion.court_case_id == court_case.id).all()
            for act in actuaciones:
                if act.pdf_url:
                    file_path = act.pdf_url
                    if file_path.startswith("/"):
                        file_path = file_path[1:]
                    file_path = file_path.replace("/", os.sep)
                    if os.path.exists(file_path):
                        os.remove(file_path)
            db.query(Actuacion).filter(Actuacion.court_case_id == court_case.id).delete()
            
            # Eliminar expediente
            if court_case.acuse_pdf_url:
                file_path = court_case.acuse_pdf_url
                if file_path.startswith("/"):
                    file_path = file_path[1:]
                file_path = file_path.replace("/", os.sep)
                if os.path.exists(file_path):
                    os.remove(file_path)
            db.delete(court_case)
    
    # Eliminar contratos
    db.query(Contract).filter(Contract.client_id == client_id).delete()
    
    # Eliminar documentos
    db.query(ClientDocument).filter(ClientDocument.client_id == client_id).delete()
    
    # Eliminar cliente
    db.delete(client)
    
    # Registrar en bitácora
    log = ActivityLog(
        firm_id=user.firm_id,
        user_id=user.id,
        action="delete",
        entity="Cliente",
        entity_id=client_id,
        description=f"{user.full_name} eliminó al cliente {client_name} y toda su información"
    )
    db.add(log)
    
    db.commit()
    
    return {
        "success": True,
        "message": f"Cliente {client_name} eliminado completamente"
    }

# --- Regenerar constancia del cliente ---
@router.get("/constancia/{client_id}")
async def regenerate_constancia(client_id: int, db: Session = Depends(get_db)):
    """Regenera y devuelve el PDF de la constancia del cliente"""
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(404, "Cliente no encontrado")
    
    # Generar PDF
    pdf_bytes = generate_client_pdf(client)
    
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f"inline; filename=constancia_{client.folio_registro}.pdf"}
    )
    
    # --- Subir documento desde móvil (vía QR) ---
@router.post("/upload-mobile/{client_temp_id}")
async def upload_mobile_document(
    client_temp_id: str,
    file: UploadFile = File(...),
    doc_type: str = Form(...)
):
    """
    Endpoint para subir documentos desde el teléfono.
    Recibe: client_temp_id (temporal), file (imagen o PDF), doc_type (CURP, INE, DOMICILIO)
    """
    try:
        # Guardar archivo temporalmente
        upload_dir = f"uploads/temp/{client_temp_id}"
        os.makedirs(upload_dir, exist_ok=True)
        
        filename = f"{doc_type}_{uuid.uuid4().hex[:8]}.pdf"
        file_path = os.path.join(upload_dir, filename)
        
        content = await file.read()
        file_ext = file.filename.split('.')[-1].lower() if file.filename else 'pdf'
        
        # Si es imagen, convertir a PDF
        if file_ext in ['jpg', 'jpeg', 'png']:
            pdf_bytes = convert_image_to_pdf(content)
            with open(file_path, "wb") as f:
                f.write(pdf_bytes)
        else:
            # Guardar PDF directamente
            with open(file_path, "wb") as f:
                f.write(content)
        
        # Devolver URL del archivo para que el frontend lo use
        return {
            "success": True,
            "file_url": "/" + file_path.replace("\\", "/"),
            "filename": filename,
            "doc_type": doc_type,
            "message": f"📄 {doc_type} subido correctamente desde móvil"
        }
        
    except Exception as e:
        return {"success": False, "error": str(e)}
    
    # --- Verificar si ya se subió un archivo desde móvil ---
@router.get("/check-upload/{temp_id}")
async def check_upload(temp_id: str):
    folder = f"uploads/temp/{temp_id}"
    if os.path.exists(folder):
        files = os.listdir(folder)
        if files:
            # Tomar el primer archivo
            filename = files[0]
            # Moverlo a la carpeta permanente (será movido por el frontend)
            return {
                "success": True,
                "file_url": f"/uploads/temp/{temp_id}/{filename}",
                "filename": filename
            }
    return {"success": False}


# --- Página móvil para subir documentos ---
@router.get("/upload-mobile-page")
async def upload_mobile_page(
    temp_id: str,
    doc_type: str = "DOCUMENTO",
    db: Session = Depends(get_db)
):
    """Sirve la página HTML para que el usuario suba el documento desde su teléfono"""
    with open("app/templates/upload_mobile.html", "r", encoding="utf-8") as f:
        html = f.read()
    # Reemplazar los placeholders con los parámetros
    html = html.replace('<!-- TEMP_ID -->', temp_id)
    html = html.replace('<!-- DOC_TYPE -->', doc_type)
    return HTMLResponse(content=html)
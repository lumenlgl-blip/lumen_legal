from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session
from sqlalchemy import or_, func
from app.database import get_db
from app.models.core import Client, ClientDocument, Contract, Payment, CourtCase
import uuid, os
from datetime import datetime
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, HRFlowable
import io
import aiofiles

router = APIRouter(prefix="/clients", tags=["Clients"])

UPLOAD_DIR = "uploads/client_docs"
os.makedirs(UPLOAD_DIR, exist_ok=True)

CONSTANCIA_DIR = "uploads/constancias"
os.makedirs(CONSTANCIA_DIR, exist_ok=True)

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
    buffer = io.BytesIO()
    pdf_doc = SimpleDocTemplate(buffer, pagesize=letter)
    styles = getSampleStyleSheet()
    
    title_style = ParagraphStyle('TitleStyle', parent=styles['Title'], fontSize=18, alignment=1, spaceAfter=20)
    subtitle_style = ParagraphStyle('SubtitleStyle', parent=styles['Heading2'], fontSize=14, alignment=1, spaceAfter=30)
    body_style = ParagraphStyle('BodyStyle', parent=styles['BodyText'], fontSize=11, leading=16, spaceAfter=6)
    section_style = ParagraphStyle('SectionStyle', parent=styles['Heading3'], fontSize=12, spaceBefore=15, spaceAfter=10)
    
    story = []
    
    story.append(Paragraph("LUMEN LEGAL", title_style))
    story.append(Paragraph("CONSTANCIA DE REGISTRO DE CLIENTE", subtitle_style))
    story.append(HRFlowable(width="100%", thickness=1, spaceAfter=20))
    
    story.append(Paragraph("DATOS DE REGISTRO", section_style))
    story.append(Paragraph(f"<b>Folio de Registro:</b> {client_data.folio_registro}", body_style))
    anio = client_data.created_at.strftime('%Y')
    story.append(Paragraph(f"<b>Expediente Interno:</b> {client_data.expediente_interno}/{anio}", body_style))
    story.append(Paragraph(f"<b>Fecha de Registro:</b> {client_data.created_at.strftime('%d/%m/%Y %H:%M')}", body_style))
    
    story.append(Paragraph("DATOS PERSONALES", section_style))
    story.append(Paragraph(f"<b>Nombre(s):</b> {client_data.name}", body_style))
    story.append(Paragraph(f"<b>Apellido Paterno:</b> {client_data.paterno}", body_style))
    story.append(Paragraph(f"<b>Apellido Materno:</b> {client_data.materno or 'N/A'}", body_style))
    story.append(Paragraph(f"<b>CURP:</b> {client_data.curp}", body_style))
    story.append(Paragraph(f"<b>Teléfono:</b> {client_data.phone}", body_style))
    story.append(Paragraph(f"<b>Email:</b> {client_data.email or 'N/A'}", body_style))
    story.append(Paragraph(f"<b>Domicilio:</b> {client_data.address}", body_style))
    story.append(Paragraph(f"<b>Ocupación:</b> {client_data.occupation}", body_style))
    
    story.append(Paragraph("DOCUMENTOS PRESENTADOS", section_style))
    if client_data.documents:
        for document in client_data.documents:
            doc_type_map = {
                "CURP": "CURP",
                "INE": "INE / Identificación Oficial",
                "DOMICILIO": "Comprobante de Domicilio"
            }
            doc_label = doc_type_map.get(document.doc_type, document.doc_type)
            story.append(Paragraph(f"• {doc_label}", body_style))
    else:
        story.append(Paragraph("• Ninguno", body_style))
    
    story.append(Spacer(1, 30))
    story.append(HRFlowable(width="100%", thickness=1, spaceBefore=10, spaceAfter=10))
    story.append(Paragraph("Aviso de Privacidad: Tus datos serán tratados conforme a la ley...", styles['BodyText']))
    
    pdf_doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()

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
    
    # Generar constancia PDF
    pdf_bytes = generate_client_pdf(new_client)
    
    constancia_path = os.path.join(CONSTANCIA_DIR, f"constancia_{folio}.pdf")
    with open(constancia_path, "wb") as f:
        f.write(pdf_bytes)
    
    anio = new_client.created_at.strftime('%Y')
    
    return {
        "success": True,
        "message": "Cliente registrado exitosamente",
        "folio_registro": new_client.folio_registro,
        "expediente_interno": f"{new_client.expediente_interno}/{anio}",
        "nombre_completo": f"{new_client.name} {new_client.paterno} {new_client.materno}",
        "constancia_url": f"/api/clients/constancia/{folio}"
    }

@router.get("/constancia/{folio}")
async def download_constancia(folio: str):
    constancia_path = os.path.join(CONSTANCIA_DIR, f"constancia_{folio}.pdf")
    if not os.path.exists(constancia_path):
        raise HTTPException(404, "Constancia no encontrada")
    with open(constancia_path, "rb") as f:
        pdf_bytes = f.read()
    return Response(content=pdf_bytes, media_type="application/pdf", headers={"Content-Disposition": f"attachment; filename=constancia_{folio}.pdf"})

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
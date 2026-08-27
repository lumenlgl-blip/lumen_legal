from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session
from sqlalchemy import or_, func
from app.database import get_db
from app.models.core import Client, Contract, Payment
import os, uuid, io
from datetime import datetime
import aiofiles
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, HRFlowable

router = APIRouter(prefix="/payments", tags=["Payments"])

UPLOAD_DIR = "uploads/payment_docs"
RECEIPT_DIR = "uploads/receipts"
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(RECEIPT_DIR, exist_ok=True)

SERVICE_TYPE_MAP = {
    "asesoria": "Asesoría Jurídica",
    "juicio": "Inicio de Juicio",
    "escrito": "Elaboración de Escrito",
    "contestacion": "Contestación de Demanda",
    "diligencia": "Diligenciar Oficio/Exhorto"
}

PAYMENT_METHOD_MAP = {
    "efectivo": "Efectivo",
    "deposito": "Depósito",
    "transferencia": "Transferencia"
}

def get_service_label(service_type):
    return SERVICE_TYPE_MAP.get(service_type, service_type)

def get_payment_method_label(method):
    return PAYMENT_METHOD_MAP.get(method, method)

async def save_upload_file(upload_file: UploadFile, folder: str, filename: str) -> str:
    os.makedirs(folder, exist_ok=True)
    file_path = os.path.join(folder, filename)
    async with aiofiles.open(file_path, "wb") as out_file:
        content = await upload_file.read()
        await out_file.write(content)
    # Devolver ruta pública absoluta
    return "/" + file_path.replace("\\", "/")

def generate_payment_receipt_pdf(client, contract, payment, total_pagado, saldo_restante):
    """Genera el PDF de recibo de pago usando ReportLab"""
    buffer = io.BytesIO()
    pdf_doc = SimpleDocTemplate(buffer, pagesize=letter)
    styles = getSampleStyleSheet()

    title_style = ParagraphStyle('TitleStyle', parent=styles['Title'], fontSize=18, alignment=1, spaceAfter=20)
    subtitle_style = ParagraphStyle('SubtitleStyle', parent=styles['Heading2'], fontSize=14, alignment=1, spaceAfter=30)
    body_style = ParagraphStyle('BodyStyle', parent=styles['BodyText'], fontSize=11, leading=16, spaceAfter=6)
    section_style = ParagraphStyle('SectionStyle', parent=styles['Heading3'], fontSize=12, spaceBefore=15, spaceAfter=10)

    story = []

    story.append(Paragraph("LUMEN LEGAL", title_style))
    story.append(Paragraph("RECIBO DE PAGO / ABONO", subtitle_style))
    story.append(HRFlowable(width="100%", thickness=1, spaceAfter=20))

    # Datos del cliente
    story.append(Paragraph("DATOS DEL CLIENTE", section_style))
    story.append(Paragraph(f"<b>Cliente:</b> {client.name} {client.paterno} {client.materno or ''}", body_style))
    story.append(Paragraph(f"<b>CURP:</b> {client.curp}", body_style))
    story.append(Paragraph(f"<b>Teléfono:</b> {client.phone}", body_style))
    story.append(Paragraph(f"<b>Folio de Registro:</b> {client.folio_registro}", body_style))
    anio = client.created_at.strftime('%Y') if client.created_at else datetime.utcnow().strftime('%Y')
    story.append(Paragraph(f"<b>Expediente Interno:</b> {client.expediente_interno}/{anio}", body_style))

    # Datos del contrato / servicio
    story.append(Paragraph("DATOS DEL CONTRATO", section_style))
    service_label = get_service_label(contract.service_type)
    story.append(Paragraph(f"<b>Servicio:</b> {service_label}", body_style))
    if contract.tipo_juicio:
        tipo_juicio = contract.tipo_juicio
        if contract.tipo_juicio_otro:
            tipo_juicio = contract.tipo_juicio_otro
        story.append(Paragraph(f"<b>Tipo de Juicio:</b> {tipo_juicio}", body_style))
    if contract.specific_detail:
        story.append(Paragraph(f"<b>Detalle:</b> {contract.specific_detail}", body_style))
    story.append(Paragraph(f"<b>Fecha de Contratación:</b> {contract.created_at.strftime('%d/%m/%Y')}", body_style))
    story.append(Paragraph(f"<b>Costo Total:</b> ${float(contract.total_cost):,.2f}", body_style))

    total_anterior = float(total_pagado) - float(payment.amount)
    story.append(Paragraph(f"<b>Total Pagado Anterior:</b> ${total_anterior:,.2f}", body_style))
    story.append(Paragraph(f"<b>Estatus del Contrato:</b> {contract.status.upper()}", body_style))

    # Datos del pago
    story.append(Paragraph("DATOS DEL PAGO", section_style))
    story.append(Paragraph(f"<b>Monto del Abono:</b> ${float(payment.amount):,.2f}", body_style))
    method_label = get_payment_method_label(payment.method)
    story.append(Paragraph(f"<b>Forma de Pago:</b> {method_label}", body_style))
    story.append(Paragraph(f"<b>Fecha de Pago:</b> {payment.payment_date.strftime('%d/%m/%Y %H:%M')}", body_style))
    if payment.receiver_name:
        story.append(Paragraph(f"<b>Recibió:</b> {payment.receiver_name}", body_style))
    story.append(Paragraph(f"<b>Total Pagado Ahora:</b> ${float(total_pagado):,.2f}", body_style))
    story.append(Paragraph(f"<b>Saldo Restante:</b> ${float(saldo_restante):,.2f}", body_style))
    if contract.status == "liquidado":
        story.append(Paragraph("<b>ESTATUS:</b> LIQUIDADO ✓", body_style))

    story.append(Spacer(1, 30))
    story.append(HRFlowable(width="100%", thickness=1, spaceBefore=10, spaceAfter=10))
    story.append(Paragraph("Aviso de Privacidad: Tus datos serán tratados conforme a la ley...", styles['BodyText']))

    pdf_doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()

# --- RUTAS PARA ABONOS Y LIQUIDACIÓN (Feature 6) ---

@router.get("/register", response_class=HTMLResponse)
async def show_payment_form():
    try:
        with open("app/templates/register_payment.html", "r", encoding="utf-8") as f:
            html_content = f.read()
        return HTMLResponse(content=html_content)
    except FileNotFoundError:
        return HTMLResponse(content="<h1>Error: Template no encontrado</h1>")

@router.post("/search-client")
async def search_client_with_debt(
    search_term: str = Form(...),
    db: Session = Depends(get_db)
):
    """Busca clientes con contratos pendientes (para abonos)"""
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
        contracts = db.query(Contract).filter(
            Contract.client_id == c.id,
            Contract.status == "pendiente"
        ).all()

        if not contracts:
            continue  # Solo mostramos clientes con deuda

        contracts_data = []
        for contract in contracts:
            total_pagado = db.query(func.sum(Payment.amount)).filter(
                Payment.contract_id == contract.id
            ).scalar() or 0
            total_pagado = float(total_pagado)
            saldo = float(contract.total_cost) - total_pagado

            service_label = get_service_label(contract.service_type)
            if contract.specific_detail:
                service_label += " - " + contract.specific_detail

            contracts_data.append({
                "id": contract.id,
                "servicio": service_label,
                "total": float(contract.total_cost),
                "pagado": total_pagado,
                "saldo": saldo,
                "status": contract.status
            })

        if contracts_data:
            result.append({
                "id": c.id,
                "nombre_completo": f"{c.name} {c.paterno} {c.materno or ''}",
                "curp": c.curp,
                "telefono": c.phone,
                "expediente_interno": c.expediente_interno,
                "folio_registro": c.folio_registro,
                "contratos": contracts_data
            })

    if not result:
        raise HTTPException(404, "No se encontraron clientes con deudas pendientes")

    return result

@router.get("/contract/{contract_id}")
async def get_contract_details(contract_id: int, db: Session = Depends(get_db)):
    contract = db.query(Contract).filter(Contract.id == contract_id).first()
    if not contract:
        raise HTTPException(404, "Contrato no encontrado")

    client = db.query(Client).filter(Client.id == contract.client_id).first()

    total_pagado = db.query(func.sum(Payment.amount)).filter(
        Payment.contract_id == contract_id
    ).scalar() or 0
    total_pagado = float(total_pagado)

    service_label = get_service_label(contract.service_type)
    if contract.specific_detail:
        service_label += " - " + contract.specific_detail

    return {
        "id": contract.id,
        "cliente": f"{client.name} {client.paterno} {client.materno or ''}",
        "servicio": service_label,
        "total": float(contract.total_cost),
        "pagado": total_pagado,
        "saldo": float(contract.total_cost) - total_pagado,
        "status": contract.status
    }

@router.post("/register/{contract_id}")
async def register_payment(
    contract_id: int,
    amount: float = Form(...),
    payment_method: str = Form(...),
    receiver_name: str = Form(None),
    receipt_file: UploadFile = File(None),
    db: Session = Depends(get_db)
):
    contract = db.query(Contract).filter(Contract.id == contract_id).first()
    if not contract:
        raise HTTPException(404, "Contrato no encontrado")

    if contract.status == "liquidado":
        raise HTTPException(400, "Este contrato ya está liquidado")

    if amount <= 0:
        raise HTTPException(400, "El monto del abono debe ser mayor a cero")

    total_pagado = db.query(func.sum(Payment.amount)).filter(
        Payment.contract_id == contract_id
    ).scalar() or 0
    total_pagado = float(total_pagado)

    if amount + total_pagado > float(contract.total_cost):
        saldo = float(contract.total_cost) - total_pagado
        raise HTTPException(400, f"El abono excede el saldo restante. Saldo disponible: ${saldo:.2f}")

    receipt_url = None
    if receipt_file and receipt_file.filename:
        filename = f"pago_{contract_id}_{uuid.uuid4().hex[:8]}.pdf"
        receipt_url = await save_upload_file(receipt_file, RECEIPT_DIR, filename)

    new_payment = Payment(
        contract_id=contract_id,
        firm_id=1,  # Temporal
        amount=amount,
        method=payment_method,
        receiver_name=receiver_name,
        receipt_pdf_url=receipt_url,
        payment_date=datetime.utcnow()
    )
    db.add(new_payment)

    nuevo_total_pagado = total_pagado + amount
    if nuevo_total_pagado >= float(contract.total_cost):
        contract.status = "liquidado"

    db.commit()
    db.refresh(new_payment)
    db.refresh(contract)

    client = db.query(Client).filter(Client.id == contract.client_id).first()

    saldo_restante = float(contract.total_cost) - nuevo_total_pagado
    pdf_bytes = generate_payment_receipt_pdf(
        client, contract, new_payment, nuevo_total_pagado, saldo_restante
    )

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=recibo_pago_{client.folio_registro}_{new_payment.id}.pdf"}
    )

# --- RUTAS PARA CONSULTA DE PAGOS (Feature 7) ---

@router.get("/consult", response_class=HTMLResponse)
async def show_consult_payments():
    """Muestra el formulario de consulta de pagos"""
    try:
        with open("app/templates/consult_payments.html", "r", encoding="utf-8") as f:
            html_content = f.read()
        return HTMLResponse(content=html_content)
    except FileNotFoundError:
        return HTMLResponse(content="<h1>Error: Template no encontrado</h1>")

@router.post("/search-client-all")
async def search_client_all(
    search_term: str = Form(...),
    db: Session = Depends(get_db)
):
    """Busca clientes sin importar si tienen deuda (para consulta general)"""
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
        total_contracts = len(contracts)
        
        total_pagado_total = 0
        total_debt = 0
        
        for contract in contracts:
            total_pagado = db.query(func.sum(Payment.amount)).filter(
                Payment.contract_id == contract.id
            ).scalar() or 0
            total_pagado = float(total_pagado)
            
            total_pagado_total += total_pagado
            total_debt += float(contract.total_cost) - total_pagado

        result.append({
            "id": c.id,
            "nombre_completo": f"{c.name} {c.paterno} {c.materno or ''}",
            "curp": c.curp,
            "telefono": c.phone,
            "expediente_interno": c.expediente_interno,
            "folio_registro": c.folio_registro,
            "contratos_count": total_contracts,
            "total_pagado": total_pagado_total,
            "deuda_total": total_debt
        })

    return result

@router.get("/client/{client_id}")
async def get_client_payments(client_id: int, db: Session = Depends(get_db)):
    """Obtiene todos los contratos y pagos de un cliente"""
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(404, "Cliente no encontrado")

    contracts = db.query(Contract).filter(Contract.client_id == client_id).all()

    total_pagado_general = 0
    total_deuda_general = 0
    
    result = {
        "id": client.id,
        "nombre_completo": f"{client.name} {client.paterno} {client.materno or ''}",
        "curp": client.curp,
        "telefono": client.phone,
        "expediente_interno": client.expediente_interno,
        "folio_registro": client.folio_registro,
        "total_pagado_general": 0,
        "total_deuda_general": 0,
        "contratos": []
    }

    for contract in contracts:
        payments = db.query(Payment).filter(Payment.contract_id == contract.id).all()
        total_pagado = sum(p.amount for p in payments) if payments else 0
        total_pagado = float(total_pagado)
        
        saldo = float(contract.total_cost) - total_pagado
        
        total_pagado_general += total_pagado
        total_deuda_general += saldo

        payments_data = []
        for p in payments:
            receipt = p.receipt_pdf_url
            if receipt and not receipt.startswith("/"):
                receipt = "/" + receipt.replace("\\", "/")
            payments_data.append({
                "id": p.id,
                "monto": float(p.amount),
                "fecha": p.payment_date.strftime("%d/%m/%Y %H:%M"),
                "metodo": p.method,
                "recibio": p.receiver_name or "N/A",
                "comprobante": receipt
            })

        service_label = get_service_label(contract.service_type)
        if contract.specific_detail:
            service_label += " - " + contract.specific_detail

        result["contratos"].append({
            "id": contract.id,
            "servicio": service_label,
            "total": float(contract.total_cost),
            "pagado": total_pagado,
            "saldo": saldo,
            "status": contract.status,
            "pagos": payments_data
        })
    
    result["total_pagado_general"] = total_pagado_general
    result["total_deuda_general"] = total_deuda_general

   
    return result
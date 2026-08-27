from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session
from sqlalchemy import or_
from app.database import get_db
from app.models.core import Client, Contract, Payment
import os, uuid, io
from datetime import datetime
import aiofiles
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, HRFlowable

router = APIRouter(prefix="/contracts", tags=["Contracts"])

UPLOAD_DIR = "uploads/contract_docs"
RECEIPT_DIR = "uploads/receipts"
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(RECEIPT_DIR, exist_ok=True)

async def save_upload_file(upload_file: UploadFile, folder: str, filename: str) -> str:
    os.makedirs(folder, exist_ok=True)
    file_path = os.path.join(folder, filename)
    async with aiofiles.open(file_path, "wb") as out_file:
        content = await upload_file.read()
        await out_file.write(content)
    return file_path

def generate_payment_receipt(client, contract, payment):
    buffer = io.BytesIO()
    pdf_doc = SimpleDocTemplate(buffer, pagesize=letter)
    styles = getSampleStyleSheet()
    
    title_style = ParagraphStyle('TitleStyle', parent=styles['Title'], fontSize=18, alignment=1, spaceAfter=20)
    subtitle_style = ParagraphStyle('SubtitleStyle', parent=styles['Heading2'], fontSize=14, alignment=1, spaceAfter=30)
    body_style = ParagraphStyle('BodyStyle', parent=styles['BodyText'], fontSize=11, leading=16, spaceAfter=6)
    section_style = ParagraphStyle('SectionStyle', parent=styles['Heading3'], fontSize=12, spaceBefore=15, spaceAfter=10)
    
    story = []
    story.append(Paragraph("LUMEN LEGAL", title_style))
    story.append(Paragraph("CONSTANCIA DE CONTRATACIÓN Y PAGO", subtitle_style))
    story.append(HRFlowable(width="100%", thickness=1, spaceAfter=20))
    
    story.append(Paragraph("DATOS DEL CLIENTE", section_style))
    story.append(Paragraph(f"<b>Cliente:</b> {client.name} {client.paterno} {client.materno}", body_style))
    story.append(Paragraph(f"<b>CURP:</b> {client.curp}", body_style))
    story.append(Paragraph(f"<b>Teléfono:</b> {client.phone}", body_style))
    story.append(Paragraph(f"<b>Folio Registro:</b> {client.folio_registro}", body_style))
    story.append(Paragraph(f"<b>Expediente Interno:</b> {client.expediente_interno}", body_style))
    
    story.append(Paragraph("DATOS DEL CONTRATO", section_style))
    
    # Mostrar tipo de servicio
    service_type_map = {
        "asesoria": "Asesoría Jurídica",
        "juicio": "Inicio de Juicio",
        "escrito": "Elaboración de Escrito",
        "contestacion": "Contestación de Demanda",
        "diligencia": "Diligenciar Oficio/Exhorto"
    }
    service_label = service_type_map.get(contract.service_type, contract.service_type)
    story.append(Paragraph(f"<b>Servicio:</b> {service_label}", body_style))
    
    # Mostrar tipo de juicio si aplica
    if contract.tipo_juicio:
        tipo_juicio_display = contract.tipo_juicio
        if contract.tipo_juicio == "Convenio" and contract.tipo_juicio_otro:
            tipo_juicio_display = contract.tipo_juicio_otro
        elif contract.tipo_juicio == "Otro" and contract.tipo_juicio_otro:
            tipo_juicio_display = contract.tipo_juicio_otro
        story.append(Paragraph(f"<b>Tipo de Juicio:</b> {tipo_juicio_display}", body_style))
    
    if contract.specific_detail:
        story.append(Paragraph(f"<b>Detalle:</b> {contract.specific_detail}", body_style))
    
    story.append(Paragraph(f"<b>Costo Total:</b> ${float(contract.total_cost):,.2f}", body_style))
    story.append(Paragraph(f"<b>Estatus:</b> {contract.status.upper()}", body_style))
    story.append(Paragraph(f"<b>Fecha de Contratación:</b> {contract.created_at.strftime('%d/%m/%Y %H:%M')}", body_style))
    
    if payment:
        story.append(Paragraph("DATOS DEL PAGO", section_style))
        story.append(Paragraph(f"<b>Monto Pagado:</b> ${float(payment.amount):,.2f}", body_style))
        story.append(Paragraph(f"<b>Forma de Pago:</b> {payment.method}", body_style))
        story.append(Paragraph(f"<b>Fecha de Pago:</b> {payment.payment_date.strftime('%d/%m/%Y %H:%M')}", body_style))
        story.append(Paragraph(f"<b>Recibió:</b> {payment.receiver_name or 'No especificado'}", body_style))
    else:
        story.append(Paragraph("DATOS DEL PAGO", section_style))
        story.append(Paragraph("<b>Monto Pagado:</b> $0.00 (Pago pendiente)", body_style))
    
    story.append(Spacer(1, 30))
    story.append(HRFlowable(width="100%", thickness=1, spaceBefore=10, spaceAfter=10))
    story.append(Paragraph("Aviso de Privacidad: Tus datos serán tratados conforme a la ley...", styles['BodyText']))
    
    pdf_doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()

@router.get("/register", response_class=HTMLResponse)
async def show_contract_form():
    with open("app/templates/register_contract.html", "r", encoding="utf-8") as f:
        html_content = f.read()
    return HTMLResponse(content=html_content)

@router.post("/search-client")
async def search_client(search_term: str = Form(...), db: Session = Depends(get_db)):
    try:
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
            result.append({
                "id": c.id,
                "nombre_completo": f"{c.name} {c.paterno} {c.materno}",
                "curp": c.curp,
                "telefono": c.phone,
                "expediente_interno": c.expediente_interno,
                "folio_registro": c.folio_registro
            })
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Error al buscar: {str(e)}")

@router.get("/client/{client_id}")
async def get_client_info(client_id: int, db: Session = Depends(get_db)):
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(404, "Cliente no encontrado")
    
    return {
        "id": client.id,
        "nombre_completo": f"{client.name} {client.paterno} {client.materno}",
        "curp": client.curp,
        "telefono": client.phone,
        "expediente_interno": client.expediente_interno,
        "folio_registro": client.folio_registro
    }

@router.post("/register/{client_id}")
async def register_contract(
    client_id: int,
    service_type: str = Form(...),  # asesoria, juicio, escrito, contestacion, diligencia
    tipo_juicio: str = Form(None),  # Divorcio Bilateral, etc.
    tipo_juicio_otro: str = Form(None),  # Para Convenio u Otro
    specific_detail: str = Form(None),
    total_cost: float = Form(...),
    payment_amount: float = Form(0.0),
    payment_method: str = Form(...),
    receiver_name: str = Form(None),
    receipt_file: UploadFile = File(None),
    db: Session = Depends(get_db)
):
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(404, "Cliente no encontrado")
    
    new_contract = Contract(
        client_id=client_id,
        firm_id=1,
        service_type=service_type,
        tipo_juicio=tipo_juicio,
        tipo_juicio_otro=tipo_juicio_otro,
        specific_detail=specific_detail,
        total_cost=total_cost,
        status="pendiente" if payment_amount < total_cost else "liquidado"
    )
    db.add(new_contract)
    db.commit()
    db.refresh(new_contract)
    
    payment = None
    if payment_amount > 0:
        receipt_url = None
        if receipt_file:
            filename = f"recibo_{client.folio_registro}_{uuid.uuid4().hex[:8]}.pdf"
            receipt_url = await save_upload_file(receipt_file, RECEIPT_DIR, filename)
        
        payment = Payment(
            contract_id=new_contract.id,
            firm_id=1,
            amount=payment_amount,
            method=payment_method,
            receiver_name=receiver_name,
            receipt_pdf_url=receipt_url
        )
        db.add(payment)
        db.commit()
        db.refresh(payment)
    
    db.refresh(new_contract)
    db.refresh(client)
    
    pdf_bytes = generate_payment_receipt(client, new_contract, payment)
    
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=contrato_{client.folio_registro}.pdf"}
    )
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session
from sqlalchemy import or_
from app.database import get_db
from app.models.core import Client, Contract, Payment
import os, uuid, io
from datetime import datetime
import aiofiles


router = APIRouter(prefix="/contracts", tags=["Contracts"])

UPLOAD_DIR = "uploads/contract_docs"
os.makedirs(UPLOAD_DIR, exist_ok=True)

async def save_upload_file(upload_file: UploadFile, folder: str, filename: str) -> str:
    os.makedirs(folder, exist_ok=True)
    file_path = os.path.join(folder, filename)
    async with aiofiles.open(file_path, "wb") as out_file:
        content = await upload_file.read()
        await out_file.write(content)
    return file_path

def generate_payment_receipt(client, contract, payment):
    from jinja2 import Environment, FileSystemLoader
    from weasyprint import HTML
    import base64
    import os
    from datetime import datetime
    
    env = Environment(loader=FileSystemLoader("app/templates"))
    template = env.get_template("pdf/contrato_servicio.html")
    
    # Ruta del logo
    logo_path = os.path.join(os.getcwd(), "app", "static", "img", "logo.jpeg")
    
    logo_base64 = ""
    if os.path.exists(logo_path):
        with open(logo_path, "rb") as f:
            logo_base64 = base64.b64encode(f.read()).decode('utf-8')
    
    service_type_map = {
        "asesoria": "Asesoría Jurídica",
        "juicio": "Inicio de Juicio",
        "escrito": "Elaboración de Escrito",
        "contestacion": "Contestación de Demanda",
        "diligencia": "Diligenciar Oficio/Exhorto"
    }
    
    forma_pago_map = {
        "efectivo": "Efectivo",
        "deposito": "Depósito",
        "transferencia": "Transferencia"
    }
    
    tipo_juicio_display = ""
    if contract.tipo_juicio:
        tipo_juicio_display = contract.tipo_juicio
        if contract.tipo_juicio_otro:
            tipo_juicio_display = contract.tipo_juicio_otro
    
    total_cost = float(contract.total_cost)
    monto_pagado = float(payment.amount) if payment else 0
    saldo_restante = total_cost - monto_pagado
    
    html_content = template.render(
        nombre_completo=f"{client.name} {client.paterno} {client.materno or ''}",
        curp=client.curp,
        telefono=client.phone,
        folio=client.folio_registro,
        contrato_id=contract.id,
        servicio=service_type_map.get(contract.service_type, contract.service_type),
        tipo_juicio=tipo_juicio_display or None,
        detalle=contract.specific_detail or None,
        costo_total=f"{total_cost:,.2f}",
        monto_pagado=f"{monto_pagado:,.2f}" if payment else None,
        forma_pago=forma_pago_map.get(payment.method, "") if payment else "",
        fecha_pago=payment.payment_date.strftime('%d/%m/%Y %H:%M') if payment else "",
        recibio=payment.receiver_name or "" if payment else "",
        saldo_restante=f"{saldo_restante:,.2f}",
        estatus="LIQUIDADO" if contract.status == "liquidado" else "PENDIENTE",
        badge_class="badge-liquidado" if contract.status == "liquidado" else "badge-pendiente",
        fecha_contratacion=contract.created_at.strftime('%d/%m/%Y %H:%M') if contract.created_at else "",
        anio=datetime.now().strftime('%Y'),
        logo_base64=logo_base64
    )
    
    pdf_bytes = HTML(string=html_content).write_pdf()
    return pdf_bytes

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
    
    # Registrar en bitácora
    from app.models.core import ActivityLog
    log = ActivityLog(
        firm_id=1,
        user_id=1,
        action="create",
        entity="Contrato",
        entity_id=new_contract.id,
        description=f"Contratación para {client.name} {client.paterno}"
    )
    db.add(log)
    db.commit()
    
    payment = None
    if payment_amount > 0:
        receipt_url = None
    if receipt_file:
        filename = f"recibo_{client.folio_registro}_{uuid.uuid4().hex[:8]}.pdf"
        receipt_url = await save_upload_file(receipt_file, UPLOAD_DIR, filename)
        
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
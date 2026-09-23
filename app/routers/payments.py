from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session
from sqlalchemy import or_, func
from app.database import get_db
from app.models.core import Client, Contract, Payment, CourtCase
from app.storage import upload_fileobj, get_file_url
import uuid
from datetime import datetime
from fastapi import Request
from app.routers.auth import get_current_user

router = APIRouter(prefix="/payments", tags=["Payments"])

# ❌ Ya NO usamos UPLOAD_DIR ni save_upload_file: todo va a R2


# ============================================================
# MAPAS DE LABELS
# ============================================================
SERVICE_TYPE_MAP = {
    "asesoria": "Asesoría Jurídica",
    "juicio": "Inicio de Juicio",
    "escrito": "Elaboración de Escrito",
    "contestacion": "Contestación de Demanda",
    "diligencia": "Diligenciar Oficio/Exhorto",
}

PAYMENT_METHOD_MAP = {
    "efectivo": "Efectivo",
    "deposito": "Depósito",
    "transferencia": "Transferencia",
}


def get_service_label(service_type):
    return SERVICE_TYPE_MAP.get(service_type, service_type)


def get_payment_method_label(method):
    return PAYMENT_METHOD_MAP.get(method, method)


# ============================================================
# GENERADOR DE PDF DE RECIBO (sin cambios)
# ============================================================
def generate_payment_receipt_pdf(client, contract, payment, total_pagado, saldo_restante):
    from jinja2 import Environment, FileSystemLoader
    from weasyprint import HTML
    import base64
    import os

    env = Environment(loader=FileSystemLoader("app/templates"))
    template = env.get_template("pdf/recibo_pago.html")

    logo_path = os.path.join(os.getcwd(), "app", "static", "img", "logo.jpeg")
    logo_base64 = ""
    if os.path.exists(logo_path):
        with open(logo_path, "rb") as f:
            logo_base64 = base64.b64encode(f.read()).decode("utf-8")

    forma_pago_map = {
        "efectivo": "Efectivo",
        "deposito": "Depósito",
        "transferencia": "Transferencia",
    }

    html_content = template.render(
        nombre_completo=f"{client.name} {client.paterno} {client.materno or ''}",
        curp=client.curp,
        folio=client.folio_registro,
        recibo_id=payment.id,
        monto_pagado=f"{float(payment.amount):,.2f}",
        forma_pago=forma_pago_map.get(payment.method, payment.method),
        fecha_pago=payment.payment_date.strftime("%d/%m/%Y %H:%M")
        if payment.payment_date
        else "",
        recibio=payment.receiver_name or "",
        saldo_restante=f"{float(saldo_restante):,.2f}",
        estatus="LIQUIDADO" if contract.status == "liquidado" else "PENDIENTE",
        badge_class="badge-liquidado"
        if contract.status == "liquidado"
        else "badge-pendiente",
        logo_base64=logo_base64,
    )

    return HTML(string=html_content).write_pdf()


# ============================================================
# FORMULARIO DE ABONOS
# ============================================================
@router.get("/register", response_class=HTMLResponse)
async def show_payment_form():
    try:
        with open("app/templates/register_payment.html", "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except FileNotFoundError:
        return HTMLResponse(content="<h1>Error: Template no encontrado</h1>")


@router.post("/search-client")
async def search_client_with_debt(
    search_term: str = Form(...), db: Session = Depends(get_db)
):
    """Busca clientes con contratos pendientes (para abonos)."""
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

    if not clients:
        raise HTTPException(404, "No se encontraron clientes")

    result = []
    for c in clients:
        contracts = (
            db.query(Contract)
            .filter(Contract.client_id == c.id, Contract.status == "pendiente")
            .all()
        )
        if not contracts:
            continue

        contracts_data = []
        for contract in contracts:
            total_pagado = (
                db.query(func.sum(Payment.amount))
                .filter(Payment.contract_id == contract.id)
                .scalar()
                or 0
            )
            total_pagado = float(total_pagado)
            saldo = float(contract.total_cost) - total_pagado

            service_label = get_service_label(contract.service_type)
            if contract.specific_detail:
                service_label += " - " + contract.specific_detail

            # Buscar expediente relacionado
            court_case = (
                db.query(CourtCase)
                .filter(CourtCase.contract_id == contract.id)
                .first()
            )
            court_case_data = None
            if court_case:
                court_case_data = {
                    "num_exp_tribunal": court_case.num_exp_tribunal,
                    "tribunal": court_case.tribunal,
                }

            contracts_data.append(
                {
                    "id": contract.id,
                    "servicio": service_label,
                    "total": float(contract.total_cost),
                    "pagado": total_pagado,
                    "saldo": saldo,
                    "status": contract.status,
                    "court_case": court_case_data,
                }
            )

        if contracts_data:
            result.append(
                {
                    "id": c.id,
                    "nombre_completo": f"{c.name} {c.paterno} {c.materno or ''}",
                    "curp": c.curp,
                    "telefono": c.phone,
                    "expediente_interno": c.expediente_interno,
                    "folio_registro": c.folio_registro,
                    "contratos": contracts_data,
                }
            )

    if not result:
        raise HTTPException(404, "No se encontraron clientes con deudas pendientes")

    return result


@router.get("/contract/{contract_id}")
async def get_contract_details(contract_id: int, db: Session = Depends(get_db)):
    contract = db.query(Contract).filter(Contract.id == contract_id).first()
    if not contract:
        raise HTTPException(404, "Contrato no encontrado")

    client = db.query(Client).filter(Client.id == contract.client_id).first()

    total_pagado = (
        db.query(func.sum(Payment.amount))
        .filter(Payment.contract_id == contract_id)
        .scalar()
        or 0
    )
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
        "status": contract.status,
    }


# ============================================================
# REGISTRAR ABONO
# ============================================================
@router.post("/register/{contract_id}")
async def register_payment(
    request: Request,
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

    total_pagado = (
        db.query(func.sum(Payment.amount))
        .filter(Payment.contract_id == contract_id)
        .scalar()
        or 0
    )
    total_pagado = float(total_pagado)

    if amount + total_pagado > float(contract.total_cost):
        saldo = float(contract.total_cost) - total_pagado
        raise HTTPException(
            400, f"El abono excede el saldo restante. Saldo disponible: ${saldo:.2f}"
        )

    # ------------------------------------------------------------
    # SUBIR COMPROBANTE A R2
    # ------------------------------------------------------------
    receipt_key = None
    if receipt_file and receipt_file.filename:
        receipt_key = f"pagos/{contract_id}/comprobante_{uuid.uuid4().hex[:8]}.pdf"
        folder = receipt_key.rsplit("/", 1)[0]
        filename = receipt_key.rsplit("/", 1)[-1]
        await receipt_file.seek(0)
        upload_fileobj(receipt_file.file, folder, filename)

    current_user = get_current_user(request, db)
    if not current_user:
        raise HTTPException(401, "No autenticado")

    new_payment = Payment(
        contract_id=contract_id,
        firm_id=current_user.firm_id,
        amount=amount,
        method=payment_method,
        receiver_name=receiver_name,
        receipt_pdf_url=receipt_key,
        payment_date=datetime.utcnow(),
    )
    db.add(new_payment)

    nuevo_total_pagado = total_pagado + amount
    if nuevo_total_pagado >= float(contract.total_cost):
        contract.status = "liquidado"

    db.commit()
    db.refresh(new_payment)

    # Bitácora
    from app.models.core import ActivityLog
    db.add(
        ActivityLog(
            firm_id=current_user.firm_id,
            user_id=current_user.id,
            action="create",
            entity="Pago",
            entity_id=new_payment.id,
            description=f"Registró abono de ${amount} para contrato #{contract_id}",
        )
    )
    db.commit()
    db.refresh(contract)

    client = db.query(Client).filter(Client.id == contract.client_id).first()
    saldo_restante = float(contract.total_cost) - nuevo_total_pagado

    pdf_bytes = generate_payment_receipt_pdf(
        client, contract, new_payment, nuevo_total_pagado, saldo_restante
    )

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename=recibo_pago_{client.folio_registro}_{new_payment.id}.pdf"
        },
    )


# ============================================================
# CONSULTA DE PAGOS
# ============================================================
@router.get("/consult", response_class=HTMLResponse)
async def show_consult_payments():
    try:
        with open("app/templates/consult_payments.html", "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except FileNotFoundError:
        return HTMLResponse(content="<h1>Error: Template no encontrado</h1>")


@router.post("/search-client-all")
async def search_client_all(
    search_term: str = Form(...), db: Session = Depends(get_db)
):
    """Busca clientes sin importar si tienen deuda."""
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

    if not clients:
        raise HTTPException(404, "No se encontraron clientes")

    result = []
    for c in clients:
        contracts = db.query(Contract).filter(Contract.client_id == c.id).all()
        total_pagado_total = 0
        total_debt = 0

        for contract in contracts:
            total_pagado = (
                db.query(func.sum(Payment.amount))
                .filter(Payment.contract_id == contract.id)
                .scalar()
                or 0
            )
            total_pagado = float(total_pagado)
            total_pagado_total += total_pagado
            total_debt += float(contract.total_cost) - total_pagado

        result.append(
            {
                "id": c.id,
                "nombre_completo": f"{c.name} {c.paterno} {c.materno or ''}",
                "curp": c.curp,
                "telefono": c.phone,
                "expediente_interno": c.expediente_interno,
                "folio_registro": c.folio_registro,
                "contratos_count": len(contracts),
                "total_pagado": total_pagado_total,
                "deuda_total": total_debt,
            }
        )

    return result


@router.get("/client/{client_id}")
async def get_client_payments(client_id: int, db: Session = Depends(get_db)):
    """Obtiene todos los contratos y pagos de un cliente."""
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
        "contratos": [],
    }

    for contract in contracts:
        payments = db.query(Payment).filter(Payment.contract_id == contract.id).all()
        total_pagado = float(sum(p.amount for p in payments)) if payments else 0.0
        saldo = float(contract.total_cost) - total_pagado

        total_pagado_general += total_pagado
        total_deuda_general += saldo

        payments_data = []
        for p in payments:
            # ------------------------------------------------------------
            # Comprobante: generamos URL prefirmada de R2 (1 hora)
            # ------------------------------------------------------------
            comprobante_url = (
                get_file_url(p.receipt_pdf_url, expires_in=3600)
                if p.receipt_pdf_url
                else None
            )
            payments_data.append(
                {
                    "id": p.id,
                    "monto": float(p.amount),
                    "fecha": p.payment_date.strftime("%d/%m/%Y %H:%M"),
                    "metodo": p.method,
                    "recibio": p.receiver_name or "N/A",
                    "comprobante": comprobante_url,  # URL prefirmada o None
                }
            )

        service_label = get_service_label(contract.service_type)
        if contract.specific_detail:
            service_label += " - " + contract.specific_detail

        # Buscar expediente relacionado al contrato
        court_case = (
            db.query(CourtCase)
            .filter(CourtCase.contract_id == contract.id)
            .first()
        )
        court_case_data = None
        if court_case:
            court_case_data = {
                "num_exp_tribunal": court_case.num_exp_tribunal,
                "tribunal": court_case.tribunal,
            }

        result["contratos"].append(
            {
                "id": contract.id,
                "servicio": service_label,
                "total": float(contract.total_cost),
                "pagado": total_pagado,
                "saldo": saldo,
                "status": contract.status,
                "court_case": court_case_data,
                "pagos": payments_data,
            }
        )

    result["total_pagado_general"] = total_pagado_general
    result["total_deuda_general"] = total_deuda_general

    return result


# ============================================================
# REIMPRIMIR RECIBO
# ============================================================
@router.get("/receipt-pdf/{payment_id}")
async def reprint_payment_receipt(payment_id: int, db: Session = Depends(get_db)):
    """Regenera y devuelve el PDF del recibo de un pago específico."""
    payment = db.query(Payment).filter(Payment.id == payment_id).first()
    if not payment:
        raise HTTPException(404, "Pago no encontrado")

    contract = db.query(Contract).filter(Contract.id == payment.contract_id).first()
    if not contract:
        raise HTTPException(404, "Contrato no encontrado")

    client = db.query(Client).filter(Client.id == contract.client_id).first()
    if not client:
        raise HTTPException(404, "Cliente no encontrado")

    total_pagado = (
        db.query(func.sum(Payment.amount))
        .filter(Payment.contract_id == contract.id)
        .scalar()
        or 0
    )
    total_pagado = float(total_pagado)
    saldo_restante = float(contract.total_cost) - total_pagado

    pdf_bytes = generate_payment_receipt_pdf(
        client, contract, payment, total_pagado, saldo_restante
    )

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"inline; filename=recibo_pago_{client.folio_registro}_{payment.id}.pdf"
        },
    )
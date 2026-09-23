from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session
from sqlalchemy import or_, func
from app.database import get_db
from app.models.core import Client, ClientDocument, Contract, Payment, CourtCase
from app.routers.auth import get_current_user
from app.storage import (
    upload_fileobj, upload_bytes, delete_file, get_file_url,
    s3_client, R2_BUCKET,
)
import uuid
from datetime import datetime

router = APIRouter(prefix="/clients", tags=["Clients"])

# ❌ Ya NO usamos UPLOAD_DIR ni save_upload_file: todo va a R2


# ============================================================
# HELPERS
# ============================================================
def generate_folio():
    return f"CL-{uuid.uuid4().hex[:8].upper()}"


def get_next_internal_expediente(firm_id: int, db: Session):
    max_exp = (
        db.query(Client.expediente_interno)
        .filter(Client.firm_id == firm_id)
        .order_by(Client.expediente_interno.desc())
        .first()
    )
    return (max_exp[0] + 1) if max_exp else 1


# ------------------------------------------------------------
# CONVERSIÓN IMAGEN → PDF (ahora todo en memoria, sin tocar disco)
# ------------------------------------------------------------
def convert_image_to_pdf(image_bytes: bytes) -> bytes:
    """Convierte JPG/PNG a PDF. Todo en memoria (BytesIO)."""
    try:
        import io
        from PIL import Image
        from reportlab.pdfgen import canvas
        from reportlab.lib.pagesizes import letter

        img = Image.open(io.BytesIO(image_bytes))
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")

        buffer = io.BytesIO()
        c = canvas.Canvas(buffer, pagesize=letter)
        width, height = letter

        img_width, img_height = img.size
        scale = min(width / img_width, height / img_height) * 0.9
        new_width = img_width * scale
        new_height = img_height * scale
        x = (width - new_width) / 2
        y = (height - new_height) / 2

        # ReportLab necesita un ImageReader, no un archivo en disco
        from reportlab.lib.utils import ImageReader
        c.drawImage(ImageReader(img), x, y, new_width, new_height)
        c.save()

        buffer.seek(0)
        return buffer.getvalue()

    except Exception as e:
        print(f"❌ Error al convertir imagen a PDF: {e}")
        return image_bytes


# ------------------------------------------------------------
# PDF DE CONSTANCIA (Jinja2 + WeasyPrint)
# ------------------------------------------------------------
def generate_client_pdf(client_data) -> bytes:
    from jinja2 import Environment, FileSystemLoader
    from weasyprint import HTML
    import base64
    import os
    from datetime import datetime

    env = Environment(loader=FileSystemLoader("app/templates"))
    template = env.get_template("pdf/constancia_cliente.html")

    logo_path = os.path.join(os.getcwd(), "app", "static", "img", "logo.jpeg")
    logo_base64 = ""
    if os.path.exists(logo_path):
        with open(logo_path, "rb") as f:
            logo_base64 = base64.b64encode(f.read()).decode("utf-8")

    anio = (
        client_data.created_at.strftime("%Y")
        if client_data.created_at
        else datetime.now().strftime("%Y")
    )

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
        fecha_registro=client_data.created_at.strftime("%d/%m/%Y %H:%M")
        if client_data.created_at
        else datetime.now().strftime("%d/%m/%Y %H:%M"),
        logo_base64=logo_base64,
    )
    return HTML(string=html_content).write_pdf()


# ============================================================
# HELPERS DE R2 PARA EL SISTEMA QR MÓVIL
# ============================================================
def _qr_is_used(temp_id: str) -> bool:
    """Verifica si el QR ya fue utilizado (existe el objeto .used en R2)."""
    try:
        s3_client.head_object(Bucket=R2_BUCKET, Key=f"temp/{temp_id}/.used")
        return True
    except Exception:
        return False


def _mark_qr_used(temp_id: str, doc_type: str):
    """Crea un objeto marcador .used en R2."""
    s3_client.put_object(
        Bucket=R2_BUCKET,
        Key=f"temp/{temp_id}/.used",
        Body=f"Usado el {datetime.now().isoformat()} para {doc_type}".encode(),
        ContentType="text/plain",
    )


def _list_qr_files(temp_id: str):
    """Lista las keys de archivos subidos para este temp_id (excluye .used)."""
    response = s3_client.list_objects_v2(
        Bucket=R2_BUCKET, Prefix=f"temp/{temp_id}/"
    )
    files = []
    for obj in response.get("Contents", []):
        key = obj["Key"]
        if not key.endswith("/.used"):
            files.append(key)
    return files


# ============================================================
# REGISTRO DE CLIENTE
# ============================================================
@router.get("/register", response_class=HTMLResponse)
async def show_register_form():
    with open("app/templates/register_client.html", "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())


@router.post("/register")
async def register_client(
    request: Request,
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
        anio = existing.created_at.strftime("%Y")
        return {
            "success": False,
            "message": "Cliente ya registrado con ese CURP",
            "existing_client": {
                "folio_registro": existing.folio_registro,
                "expediente_interno": f"{existing.expediente_interno}/{anio}",
                "nombre_completo": f"{existing.name} {existing.paterno} {existing.materno}",
            },
        }

    # Obtener el usuario autenticado (viene de la cookie de sesión)
    current_user = get_current_user(request, db)
    if not current_user:
        raise HTTPException(401, "No autenticado")
    
    firm_id = current_user.firm_id
    
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
        occupation=occupation,
    )
    db.add(new_client)
    db.commit()
    db.refresh(new_client)

  
    # Registrar en bitácora
    from app.models.core import ActivityLog
    log = ActivityLog(
        firm_id=current_user.firm_id,
        user_id=current_user.id,
        action="create",
        entity="Cliente",
        entity_id=new_client.id,
        description=f"Registró al cliente {new_client.name} {new_client.paterno}"
    )
    db.add(log)

    # ------------------------------------------------------------
    # SUBIR DOCUMENTOS A R2
    # ------------------------------------------------------------
    docs = [
        ("CURP", curp_file),
        ("INE", ine_file),
        ("DOMICILIO", domicilio_file),
    ]
    for doc_type, file in docs:
        # Key única: clientes/{folio}/{doc_type}_{uuid}.pdf
        key = f"clientes/{folio}/{doc_type}_{uuid.uuid4().hex[:8]}.pdf"
        folder = key.rsplit("/", 1)[0]
        filename = key.rsplit("/", 1)[-1]

        # Subir el archivo tal cual (PDF subido desde el navegador)
        await file.seek(0)
        upload_fileobj(file.file, folder, filename)

        db.add(
            ClientDocument(
                client_id=new_client.id,
                doc_type=doc_type,
                file_url=key,  # 🔑 guardamos la KEY, no la URL
            )
        )

    db.commit()
    db.refresh(new_client)

    # Devolver constancia en PDF
    pdf_bytes = generate_client_pdf(new_client)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=constancia_{folio}.pdf"},
    )


# ============================================================
# CONSULTA DE CLIENTE
# ============================================================
@router.get("/consult", response_class=HTMLResponse)
async def show_consult_client():
    try:
        with open("app/templates/consult_client.html", "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except FileNotFoundError:
        return HTMLResponse(content="<h1>Error: Template no encontrado</h1>")


@router.post("/search-client-consult")
async def search_client_for_consult(
    search_term: str = Form(...), db: Session = Depends(get_db)
):
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
        total_debt = 0
        for contract in contracts:
            if contract.status == "pendiente":
                total_pagado = (
                    db.query(func.sum(Payment.amount))
                    .filter(Payment.contract_id == contract.id)
                    .scalar()
                    or 0
                )
                total_debt += float(contract.total_cost) - float(total_pagado)

        has_case = (
            db.query(CourtCase)
            .join(Contract)
            .filter(Contract.client_id == c.id)
            .first()
            is not None
        )

        result.append(
            {
                "id": c.id,
                "nombre_completo": f"{c.name} {c.paterno} {c.materno or ''}",
                "curp": c.curp,
                "telefono": c.phone,
                "email": c.email or "N/A",
                "expediente_interno": c.expediente_interno,
                "folio_registro": c.folio_registro,
                "total_contratos": len(contracts),
                "deuda_total": total_debt,
                "has_case": has_case,
            }
        )
    return result


@router.get("/consult-detail/{client_id}")
async def get_client_full_detail(client_id: int, db: Session = Depends(get_db)):
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(404, "Cliente no encontrado")

    # ------------------------------------------------------------
    # Documentos: generamos URLs prefirmadas temporales de R2
    # ------------------------------------------------------------
    documents = (
        db.query(ClientDocument).filter(ClientDocument.client_id == client_id).all()
    )
    docs_data = []
    for doc in documents:
        docs_data.append(
            {
                "id": doc.id,
                "tipo": doc.doc_type,
                "url": get_file_url(doc.file_url, expires_in=3600),  # 1 hora
                "fecha": doc.uploaded_at.strftime("%d/%m/%Y %H:%M"),
            }
        )

    contracts = db.query(Contract).filter(Contract.client_id == client_id).all()
    contracts_data = []
    for contract in contracts:
        payments = db.query(Payment).filter(Payment.contract_id == contract.id).all()
        total_pagado = float(sum(p.amount for p in payments)) if payments else 0.0

        court_case = (
            db.query(CourtCase).filter(CourtCase.contract_id == contract.id).first()
        )
        case_data = None
        if court_case:
            case_data = {
                "id": court_case.id,
                "num_exp_tribunal": court_case.num_exp_tribunal,
                "tribunal": court_case.tribunal,
                "status": court_case.status,
                "actor_nombre": court_case.actor_nombre or "",
                "demandado_nombre": court_case.demandado_nombre or "",
            }

        last_payment = None
        if payments:
            last_payment = {
                "monto": float(payments[-1].amount),
                "fecha": payments[-1].payment_date.strftime("%d/%m/%Y"),
            }

        contracts_data.append(
            {
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
                "total_payments": len(payments),
            }
        )

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
        "contratos": contracts_data,
    }


# ============================================================
# ACTUALIZAR CLIENTE
# ============================================================
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
    db: Session = Depends(get_db),
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
        "nombre": f"{client.name} {client.paterno} {client.materno}",
    }


# ============================================================
# REEMPLAZAR DOCUMENTO
# ============================================================
@router.post("/replace-doc/{doc_id}")
async def replace_client_document(
    doc_id: int, pdf_file: UploadFile = File(...), db: Session = Depends(get_db)
):
    doc = db.query(ClientDocument).filter(ClientDocument.id == doc_id).first()
    if not doc:
        raise HTTPException(404, "Documento no encontrado")

    # 1. Subir el nuevo archivo a R2
    new_key = f"clientes/{doc.client.folio_registro}/{doc.doc_type}_{uuid.uuid4().hex[:8]}.pdf"
    folder = new_key.rsplit("/", 1)[0]
    filename = new_key.rsplit("/", 1)[-1]
    await pdf_file.seek(0)
    upload_fileobj(pdf_file.file, folder, filename)

    # 2. Eliminar el archivo anterior de R2
    if doc.file_url:
        delete_file(doc.file_url)

    # 3. Actualizar la BD
    doc.file_url = new_key
    db.commit()

    return {"message": "Documento reemplazado correctamente"}


# ============================================================
# ELIMINAR CLIENTE COMPLETO (solo admin)
# ============================================================
@router.delete("/delete/{client_id}")
async def delete_client(
    client_id: int,
    request: Request,
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    from app.routers.auth import get_current_user, verify_password
    from app.models.core import ActivityLog, Actuacion, AgendaEvent, CourtCase

    # ============================================================
    # 1. Autenticación y permisos
    # ============================================================
    user = get_current_user(request, db)
    if not user:
        return {"success": False, "message": "No autenticado"}
    if user.role != "admin":
        return {"success": False, "message": "Solo administradores pueden eliminar clientes"}
    if not verify_password(password, user.hashed_password):
        return {"success": False, "message": "Contraseña incorrecta"}

    # ============================================================
    # 2. Cargar cliente
    # ============================================================
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        return {"success": False, "message": "Cliente no encontrado"}

    client_name = f"{client.name} {client.paterno} {client.materno or ''}"
    client_folio = client.folio_registro

    # Contadores para bitácora
    stats = {
        "documentos": 0,
        "contratos": 0,
        "expedientes": 0,
        "actuaciones": 0,
        "pagos": 0,
        "agenda_events": 0,
        "archivos_r2": 0,
    }

    # ============================================================
    # 3. DOCUMENTOS del cliente (R2 + BD)
    # ============================================================
    for doc in db.query(ClientDocument).filter(ClientDocument.client_id == client_id).all():
        if doc.file_url and delete_file(doc.file_url):
            stats["archivos_r2"] += 1
        db.delete(doc)
        stats["documentos"] += 1

    # ============================================================
    # 4. CONTRATOS → PAGOS → EXPEDIENTES → ACTUACIONES → AGENDA
    # ============================================================
    contracts = db.query(Contract).filter(Contract.client_id == client_id).all()
    for contract in contracts:
        # 4.1 Pagos (R2 + BD) — ⚠️ incluye receipt_pdf_url de R2
        for payment in db.query(Payment).filter(Payment.contract_id == contract.id).all():
            if payment.receipt_pdf_url and delete_file(payment.receipt_pdf_url):
                stats["archivos_r2"] += 1
            db.delete(payment)
            stats["pagos"] += 1

        # 4.2 Expediente del contrato
        court_case = db.query(CourtCase).filter(CourtCase.contract_id == contract.id).first()
        if court_case:
            # 4.2.1 Actuaciones (R2 + BD)
            for act in db.query(Actuacion).filter(Actuacion.court_case_id == court_case.id).all():
                if act.pdf_url and delete_file(act.pdf_url):
                    stats["archivos_r2"] += 1
                db.delete(act)
                stats["actuaciones"] += 1

            # 4.2.2 Agenda events vinculados al expediente
            for ev in db.query(AgendaEvent).filter(AgendaEvent.court_case_id == court_case.id).all():
                db.delete(ev)
                stats["agenda_events"] += 1

            # 4.2.3 Acuse PDF (R2)
            if court_case.acuse_pdf_url and delete_file(court_case.acuse_pdf_url):
                stats["archivos_r2"] += 1

            # 4.2.4 Expediente (BD)
            db.delete(court_case)
            stats["expedientes"] += 1

        # 4.3 Contrato (BD)
        db.delete(contract)
        stats["contratos"] += 1

    # ============================================================
    # 5. Cliente (BD)
    # ============================================================
    db.delete(client)

    # ============================================================
    # 6. Bitácora con resumen
    # ============================================================
    db.add(
        ActivityLog(
            firm_id=user.firm_id,
            user_id=user.id,
            action="delete",
            entity="Cliente",
            entity_id=client_id,
            description=(
                f"{user.full_name} eliminó al cliente {client_name} (folio {client_folio}). "
                f"Eliminados: {stats['contratos']} contratos, "
                f"{stats['expedientes']} expedientes, "
                f"{stats['actuaciones']} actuaciones, "
                f"{stats['pagos']} pagos, "
                f"{stats['documentos']} documentos, "
                f"{stats['agenda_events']} eventos de agenda, "
                f"{stats['archivos_r2']} archivos de R2."
            ),
        )
    )

    # ============================================================
    # 7. Commit único (todo o nada)
    # ============================================================
    db.commit()

    return {
        "success": True,
        "message": f"Cliente {client_name} eliminado completamente",
        "resumen": stats,
    }


# ============================================================
# REGENERAR CONSTANCIA
# ============================================================
@router.get("/constancia/{client_id}")
async def regenerate_constancia(client_id: int, db: Session = Depends(get_db)):
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(404, "Cliente no encontrado")

    pdf_bytes = generate_client_pdf(client)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f"inline; filename=constancia_{client.folio_registro}.pdf"},
    )


# ============================================================
# SISTEMA QR — SUBIDA DESDE MÓVIL
# ============================================================
@router.post("/upload-mobile/{client_temp_id}")
async def upload_mobile_document(
    client_temp_id: str,
    file: UploadFile = File(...),
    doc_type: str = Form(...),
):
    """El móvil sube un archivo; se guarda en R2 bajo temp/{temp_id}/."""
    try:
        # 1. Verificar si el QR ya fue usado
        if _qr_is_used(client_temp_id):
            return {
                "success": False,
                "error": "Este código QR ya ha sido utilizado. Genera uno nuevo desde el sistema.",
            }

        # 2. Leer el contenido
        content = await file.read()
        file_ext = file.filename.split(".")[-1].lower() if file.filename else "pdf"

        # 3. Si es imagen, convertir a PDF
        if file_ext in ("jpg", "jpeg", "png"):
            content = convert_image_to_pdf(content)

        # 4. Subir a R2
        filename = f"{doc_type}_{uuid.uuid4().hex[:8]}.pdf"
        key = f"temp/{client_temp_id}/{filename}"
        folder = key.rsplit("/", 1)[0]
        filename_only = key.rsplit("/", 1)[-1]
        upload_bytes(content, folder, filename_only, content_type="application/pdf")

        # 5. Marcar el QR como usado
        _mark_qr_used(client_temp_id, doc_type)

        return {
            "success": True,
            "file_url": get_file_url(key, expires_in=600),
            "key": key,
            "filename": filename_only,
            "doc_type": doc_type,
            "message": f"📄 {doc_type} subido correctamente. Este QR ya no es válido.",
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.get("/check-upload/{temp_id}")
async def check_upload(temp_id: str):
    """Consulta si el móvil ya subió algo a temp/{temp_id}/."""
    files = _list_qr_files(temp_id)
    if files:
        key = files[0]
        filename = key.rsplit("/", 1)[-1]
        return {
            "success": True,
            "file_url": get_file_url(key, expires_in=600),
            "filename": filename,
        }
    return {"success": False}


@router.get("/upload-mobile-page")
async def upload_mobile_page(
    temp_id: str, doc_type: str = "DOCUMENTO", db: Session = Depends(get_db)
):
    with open("app/templates/upload_mobile.html", "r", encoding="utf-8") as f:
        html = f.read()
    html = html.replace("<!-- TEMP_ID -->", temp_id)
    html = html.replace("<!-- DOC_TYPE -->", doc_type)
    return HTMLResponse(content=html)


@router.get("/check-qr-status/{temp_id}")
async def check_qr_status(temp_id: str):
    """Verifica si un QR ya fue utilizado."""
    if _qr_is_used(temp_id):
        return {"used": True, "message": "Este QR ya fue utilizado"}

    files = _list_qr_files(temp_id)
    if files:
        return {
            "used": False,
            "has_files": True,
            "files": [f.rsplit("/", 1)[-1] for f in files],
        }
    return {"used": False, "has_files": False}
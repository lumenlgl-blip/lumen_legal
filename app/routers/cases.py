from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Request
from app.routers.auth import get_current_user
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session
from sqlalchemy import or_
from app.database import get_db
from app.models.core import Client, Contract, CourtCase, Actuacion, User
from app.storage import upload_fileobj, upload_bytes, delete_file, get_file_url, s3_client, R2_BUCKET
import uuid
from datetime import datetime

router = APIRouter(prefix="/cases", tags=["Cases"])

# ❌ UPLOAD_DIR y save_upload_file eliminados


# ============================================================
# HELPERS
# ============================================================
def get_cases_for_client(client_id: int, db: Session):
    """Obtiene los casos del cliente (con partes del juicio)."""
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
                    "fecha_presentacion": court_case.fecha_presentacion.strftime("%d/%m/%Y"),
                    "contract_id": contract.id,
                    # 👇 NUEVOS
                    "actor_nombre": court_case.actor_nombre or "",
                    "demandado_nombre": court_case.demandado_nombre or "",
                }
            )
    return cases_data


# ============================================================
# FORMULARIO RELACIONAR EXPEDIENTE
# ============================================================
@router.get("/relate", response_class=HTMLResponse)
async def show_relate_form():
    with open("app/templates/relate_case.html", "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())


# ============================================================
# BUSCAR CLIENTES CON CONTRATOS PENDIENTES DE RELACIONAR
# ============================================================
@router.post("/search-client")
async def search_client(search_term: str = Form(...), db: Session = Depends(get_db)):
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
        has_pending_contract = (
            db.query(Contract)
            .filter(
                Contract.client_id == c.id,
                Contract.service_type.in_(["juicio", "contestacion"]),
                ~Contract.court_case.has(),
            )
            .first()
            is not None
        )

        if has_pending_contract:
            result.append(
                {
                    "id": c.id,
                    "nombre_completo": f"{c.name} {c.paterno} {c.materno or ''}",
                    "curp": c.curp,
                    "telefono": c.phone,
                    "expediente_interno": c.expediente_interno,
                    "folio_registro": c.folio_registro,
                }
            )

    if not result:
        raise HTTPException(
            404, "No se encontraron clientes con contrataciones pendientes de relacionar"
        )

    return result


# ============================================================
# OBTENER CLIENTE CON CONTRATOS PENDIENTES
# ============================================================
@router.get("/client/{client_id}")
async def get_client(client_id: int, db: Session = Depends(get_db)):
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(404, "Cliente no encontrado")

    contracts = (
        db.query(Contract)
        .filter(
            Contract.client_id == client_id,
            Contract.service_type.in_(["juicio", "contestacion"]),
            ~Contract.court_case.has(),
        )
        .all()
    )

    if not contracts:
        raise HTTPException(
            404,
            "Este cliente no tiene contrataciones de juicio pendientes de relacionar",
        )

    contracts_data = []
    for contract in contracts:
        service_type_map = {
            "juicio": "Inicio de Juicio",
            "contestacion": "Contestación de Demanda",
        }
        service_label = service_type_map.get(contract.service_type, contract.service_type)

        tipo_juicio_display = contract.tipo_juicio or ""
        if contract.tipo_juicio_otro:
            tipo_juicio_display = contract.tipo_juicio_otro

        contracts_data.append(
            {
                "id": contract.id,
                "servicio": service_label,
                "tipo_juicio": tipo_juicio_display,
                "detalle": contract.specific_detail or "",
                "costo_total": float(contract.total_cost),
                "estatus": contract.status,
            }
        )

    return {
        "id": client.id,
        "nombre_completo": f"{client.name} {client.paterno} {client.materno or ''}",
        "curp": client.curp,
        "telefono": client.phone,
        "expediente_interno": client.expediente_interno,
        "folio_registro": client.folio_registro,
        "domicilio": client.address,
        "ocupacion": client.occupation,
        "contratos": contracts_data,
    }


# ============================================================
# RELACIONAR EXPEDIENTE (sube acuse a R2)
# ============================================================
@router.post("/relate/{contract_id}")
async def relate_case(
    contract_id: int,
    request: Request,
    tribunal: str = Form(...),
    secretaria: str = Form(...),
    num_exp_tribunal: str = Form(...),
    folio_tribunal: str = Form(...),
    fecha_presentacion: str = Form(...),
    tipo_juicio: str = Form(None),
    demandado_nombre: str = Form(None),
    actor_nombre: str = Form(None),
    actor2_nombre: str = Form(None),
    acuse_file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    # 1. Buscar el contrato (el frontend manda contract_id en la URL)
    contract = db.query(Contract).filter(Contract.id == contract_id).first()
    if not contract:
        raise HTTPException(404, "Contrato no encontrado")

    # 2. Verificar que sea de tipo juicio/contestación y sin expediente aún
    if contract.service_type not in ("juicio", "contestacion"):
        raise HTTPException(
            400, "Este contrato no es de tipo juicio ni contestación"
        )

    existing_case = (
        db.query(CourtCase).filter(CourtCase.contract_id == contract.id).first()
    )
    if existing_case:
        raise HTTPException(400, "Este contrato ya tiene un expediente relacionado")

    # 3. Obtener el cliente dueño del contrato
    client = db.query(Client).filter(Client.id == contract.client_id).first()
    if not client:
        raise HTTPException(404, "Cliente no encontrado")

    # 4. Usuario autenticado
    current_user = get_current_user(request, db)
    if not current_user:
        raise HTTPException(401, "No autenticado")

    # 5. Leer el archivo una sola vez (se reutiliza para acuse y actuación)
    await acuse_file.seek(0)
    acuse_content = await acuse_file.read()

    # 6. Subir acuse a R2 (key para el expediente)
    acuse_key = f"expedientes/{client.folio_registro}/acuse_{uuid.uuid4().hex[:8]}.pdf"
    acuse_folder = acuse_key.rsplit("/", 1)[0]
    acuse_filename = acuse_key.rsplit("/", 1)[-1]
    upload_bytes(acuse_content, acuse_folder, acuse_filename, content_type="application/pdf")

    # Combinar dos actores si es divorcio bilateral
    actor_final = actor_nombre
    if actor2_nombre:
        actor_final = f"{actor_nombre} / {actor2_nombre}" if actor_nombre else actor2_nombre

    # 7. Crear expediente
    new_case = CourtCase(
        contract_id=contract.id,
        firm_id=current_user.firm_id,
        tribunal=tribunal,
        secretaria=secretaria,
        num_exp_tribunal=num_exp_tribunal,
        folio_tribunal=folio_tribunal,
        fecha_presentacion=datetime.strptime(fecha_presentacion, "%Y-%m-%d").date(),
        acuse_pdf_url=acuse_key,
        demandado_nombre=demandado_nombre,
        actor_nombre=actor_final,
        status="iniciado",
    )
    db.add(new_case)
    db.commit()
    db.refresh(new_case)

    # 8. Subir el mismo acuse como actuación "Demanda Inicial" (key independiente)
    actuacion_key = f"expedientes/{new_case.id}/actuacion_{uuid.uuid4().hex[:8]}.pdf"
    actuacion_folder = actuacion_key.rsplit("/", 1)[0]
    actuacion_filename = actuacion_key.rsplit("/", 1)[-1]
    upload_bytes(acuse_content, actuacion_folder, actuacion_filename, content_type="application/pdf")

    new_actuacion = Actuacion(
        court_case_id=new_case.id,
        firm_id=current_user.firm_id,
        tipo="demanda",
        fecha_actuacion=new_case.fecha_presentacion,
        descripcion=f"Demanda inicial — Expediente {num_exp_tribunal}",
        pdf_url=actuacion_key,
        uploaded_by=current_user.id,
        uploaded_at=datetime.utcnow(),
    )
    db.add(new_actuacion)

    # 9. Bitácora
    from app.models.core import ActivityLog
    db.add(
        ActivityLog(
            firm_id=current_user.firm_id,
            user_id=current_user.id,
            action="create",
            entity="Expediente",
            entity_id=new_case.id,
            description=(
                f"Relacionó expediente {num_exp_tribunal} "
                f"al cliente {client.name} {client.paterno} "
                f"(actuación inicial: Demanda)"
            ),
        )
    )
    db.commit()

    return {
        "message": "Expediente relacionado exitosamente",
        "cliente": f"{client.name} {client.paterno}",
        "expediente_tribunal": num_exp_tribunal,
        "folio": folio_tribunal,
    }


# ============================================================
# ACTUALIZAR ESTATUS
# ============================================================
@router.get("/status", response_class=HTMLResponse)
async def show_status_form():
    try:
        with open("app/templates/update_status.html", "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except FileNotFoundError:
        return HTMLResponse(content="<h1>Error: Template no encontrado</h1>")


@router.post("/search-client-status")
async def search_client_for_status(
    search_term: str = Form(...), db: Session = Depends(get_db)
):
    court_cases_by_exp = (
        db.query(CourtCase)
        .filter(CourtCase.num_exp_tribunal.ilike(f"%{search_term}%"))
        .all()
    )

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

    for court_case in court_cases_by_exp:
        contract = db.query(Contract).filter(Contract.id == court_case.contract_id).first()
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


@router.put("/status/{court_case_id}")
async def update_case_status(
    court_case_id: int, new_status: str = Form(...), db: Session = Depends(get_db)
):
    court_case = db.query(CourtCase).filter(CourtCase.id == court_case_id).first()
    if not court_case:
        raise HTTPException(404, "Expediente no encontrado")

    valid_statuses = [
        "iniciado", "terminado", "inactivo", "desistido",
        "caducado", "revocado", "cliente_no_contesta", "otro",
    ]
    if new_status not in valid_statuses:
        raise HTTPException(
            400, f"Estatus no válido. Opciones: {', '.join(valid_statuses)}"
        )

    court_case.status = new_status
    db.commit()
    db.refresh(court_case)

    return {
        "message": "Estatus actualizado exitosamente",
        "id": court_case.id,
        "nuevo_estatus": court_case.status,
        "expediente": court_case.num_exp_tribunal,
    }


# ============================================================
# CONSULTAR EXPEDIENTE
# ============================================================
@router.get("/consult", response_class=HTMLResponse)
async def show_consult_case():
    try:
        with open("app/templates/consult_case.html", "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except FileNotFoundError:
        return HTMLResponse(content="<h1>Error: Template no encontrado</h1>")


@router.post("/search-client-consult")
async def search_client_for_consult(
    search_term: str = Form(...), db: Session = Depends(get_db)
):
    court_cases_by_exp = (
        db.query(CourtCase)
        .filter(CourtCase.num_exp_tribunal.ilike(f"%{search_term}%"))
        .all()
    )

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

    for court_case in court_cases_by_exp:
        contract = db.query(Contract).filter(Contract.id == court_case.contract_id).first()
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


@router.get("/consult-detail/{court_case_id}")
async def get_case_full_detail(court_case_id: int, db: Session = Depends(get_db)):
    court_case = db.query(CourtCase).filter(CourtCase.id == court_case_id).first()
    if not court_case:
        raise HTTPException(404, "Expediente no encontrado")

    contract = db.query(Contract).filter(Contract.id == court_case.contract_id).first()
    client = db.query(Client).filter(Client.id == contract.client_id).first()

    actuaciones = (
        db.query(Actuacion)
        .filter(Actuacion.court_case_id == court_case_id)
        .order_by(Actuacion.fecha_actuacion)
        .all()
    )

    actuaciones_data = []
    for act in actuaciones:
        # URL prefirmada de R2
        pdf_url = get_file_url(act.pdf_url, expires_in=3600) if act.pdf_url else None
        actuaciones_data.append(
            {
                "id": act.id,
                "tipo": act.tipo,
                "fecha": act.fecha_actuacion.strftime("%d/%m/%Y"),
                "descripcion": act.descripcion or "",
                "pdf_url": pdf_url,
                "subido_en": act.uploaded_at.strftime("%d/%m/%Y %H:%M"),
            }
        )

    # Acuse: URL prefirmada
    acuse_url = (
        get_file_url(court_case.acuse_pdf_url, expires_in=3600)
        if court_case.acuse_pdf_url
        else None
    )

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
            "folio_registro": client.folio_registro,
        },
        "contrato": {
            "id": contract.id,
            "service_type": contract.service_type,
            "specific_detail": contract.specific_detail or "",
            "tipo_juicio": contract.tipo_juicio or "",
            "tipo_juicio_otro": contract.tipo_juicio_otro or "",
            "total_cost": float(contract.total_cost),
            "status": contract.status,
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
            "acuse_pdf_url": acuse_url,  # 🔑 URL prefirmada
        },
        "actuaciones": actuaciones_data,
    }


# ============================================================
# VERIFICAR CONTRASEÑA
# ============================================================
@router.post("/verify-password")
async def verify_user_password(
    request: Request, password: str = Form(...), db: Session = Depends(get_db)
):
    from app.routers.auth import get_current_user, verify_password

    user = get_current_user(request, db)
    if not user:
        raise HTTPException(401, "No autenticado")

    if not verify_password(password, user.hashed_password):
        raise HTTPException(401, "Contraseña incorrecta")

    return {"valid": True}


# ============================================================
# PDF UNIFICADO DEL EXPEDIENTE (sin cambios — todo en memoria)
# ============================================================
@router.get("/full-case-pdf/{court_case_id}")
async def get_full_case_pdf(court_case_id: int, db: Session = Depends(get_db)):
    """
    Genera un PDF unificado del expediente:
      1. Portada con datos generales + índice
      2. Cada actuación (PDF descargado de R2) pegada en orden cronológico
    """
    from jinja2 import Environment, FileSystemLoader
    from weasyprint import HTML
    from pypdf import PdfWriter, PdfReader
    import base64
    import io
    import os
    from datetime import datetime

    court_case = db.query(CourtCase).filter(CourtCase.id == court_case_id).first()
    if not court_case:
        raise HTTPException(404, "Expediente no encontrado")

    contract = db.query(Contract).filter(Contract.id == court_case.contract_id).first()
    client = db.query(Client).filter(Client.id == contract.client_id).first()

    # Orden cronológico: más viejo → más nuevo, desempate por id
    actuaciones = (
        db.query(Actuacion)
        .filter(Actuacion.court_case_id == court_case_id)
        .order_by(Actuacion.fecha_actuacion.asc(), Actuacion.id.asc())
        .all()
    )

    # ------------------------------------------------------------
    # 1. Generar la portada (índice) con WeasyPrint
    # ------------------------------------------------------------
    logo_path = os.path.join(os.getcwd(), "app", "static", "img", "logo.jpeg")
    logo_base64 = ""
    if os.path.exists(logo_path):
        with open(logo_path, "rb") as f:
            logo_base64 = base64.b64encode(f.read()).decode("utf-8")

    env = Environment(loader=FileSystemLoader("app/templates"))
    template = env.get_template("pdf/expediente_completo.html")

    actuaciones_data = [
        {
            "num": idx + 1,
            "tipo": act.tipo.upper(),
            "fecha": act.fecha_actuacion.strftime("%d/%m/%Y"),
            "descripcion": act.descripcion or "",
            "subido_en": act.uploaded_at.strftime("%d/%m/%Y %H:%M")
            if act.uploaded_at
            else "",
        }
        for idx, act in enumerate(actuaciones)
    ]

    html_content = template.render(
        logo_base64=logo_base64,
        num_expediente=court_case.num_exp_tribunal,
        folio=court_case.folio_tribunal,
        tribunal=court_case.tribunal,
        secretaria=court_case.secretaria,
        fecha_presentacion=court_case.fecha_presentacion.strftime("%d/%m/%Y"),
        estatus=court_case.status.upper(),
        actor=court_case.actor_nombre or "N/A",
        demandado=court_case.demandado_nombre or "N/A",
        nombre_completo=f"{client.name} {client.paterno} {client.materno or ''}",
        curp=client.curp,
        telefono=client.phone,
        domicilio=client.address,
        total_actuaciones=len(actuaciones),
        actuaciones=actuaciones_data,
        anio=datetime.now().strftime("%Y"),
    )

    portada_bytes = HTML(string=html_content).write_pdf()

    # ------------------------------------------------------------
    # 2. Fusionar: portada + cada PDF de actuación (descargado de R2)
    # ------------------------------------------------------------
    writer = PdfWriter()
    writer.append(PdfReader(io.BytesIO(portada_bytes)))

    adjuntadas = 0
    omitidas = 0

    for act in actuaciones:
        if not act.pdf_url:
            omitidas += 1
            continue

        try:
            # Descargar de R2
            response = s3_client.get_object(Bucket=R2_BUCKET, Key=act.pdf_url)
            pdf_data = response["Body"].read()

            # Adjuntar al PDF final
            writer.append(PdfReader(io.BytesIO(pdf_data)))
            adjuntadas += 1

        except Exception as e:
            print(f"⚠️ No se pudo adjuntar actuación #{act.id} ({act.pdf_url}): {e}")
            omitidas += 1
            continue

    # ------------------------------------------------------------
    # 3. Escribir el resultado final
    # ------------------------------------------------------------
    output = io.BytesIO()
    writer.write(output)
    writer.close()
    output.seek(0)

    print(f"📄 PDF expediente {court_case.num_exp_tribunal}: "
          f"{adjuntadas} actuaciones adjuntadas, {omitidas} omitidas")

    return Response(
        content=output.getvalue(),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"inline; filename=expediente_completo_{court_case.num_exp_tribunal}.pdf"
        },
    )
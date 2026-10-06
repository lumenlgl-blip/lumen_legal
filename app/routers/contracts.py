from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session
from sqlalchemy import or_
from app.database import get_db
from app.utils.search import safe_int
from app.models.core import Client, Contract, Payment
from app.storage import upload_fileobj
from app.routers.auth import get_current_user
import uuid

router = APIRouter(prefix="/contracts", tags=["Contracts"])

# ❌ UPLOAD_DIR y save_upload_file eliminados


# ============================================================
# GENERADOR DE PDF DE CONTRATO (sin cambios — todo en memoria)
# ============================================================
def generate_payment_receipt(client, contract, payment):
    from jinja2 import Environment, FileSystemLoader
    from weasyprint import HTML
    import base64
    import os
    from datetime import datetime

    env = Environment(loader=FileSystemLoader("app/templates"))
    template = env.get_template("pdf/contrato_servicio.html")

    logo_path = os.path.join(os.getcwd(), "app", "static", "img", "logo.jpeg")
    logo_base64 = ""
    if os.path.exists(logo_path):
        with open(logo_path, "rb") as f:
            logo_base64 = base64.b64encode(f.read()).decode("utf-8")

    service_type_map = {
        "asesoria": "Asesoría Jurídica",
        "juicio": "Inicio de Juicio",
        "escrito": "Elaboración de Escrito",
        "contestacion": "Contestación de Demanda",
        "diligencia": "Diligenciar Oficio/Exhorto",
    }
    forma_pago_map = {
        "efectivo": "Efectivo",
        "deposito": "Depósito",
        "transferencia": "Transferencia",
    }

    tipo_juicio_display = ""
    if contract.tipo_juicio:
        tipo_juicio_display = contract.tipo_juicio
        if contract.tipo_juicio_otro:
            tipo_juicio_display = contract.tipo_juicio_otro

    total_cost = float(contract.total_cost)
    monto_pagado = float(payment.amount) if payment else 0
    saldo_restante = total_cost - monto_pagado

    # Datos del abogado asignado (si existe)
    abogado_nombre = ""
    abogado_cedula = ""
    abogado_especialidad = ""
    abogado_telefono = ""
    abogado_domicilio = ""
    if contract.assigned_lawyer:
        abogado_nombre = contract.assigned_lawyer.nombre_completo
        abogado_cedula = contract.assigned_lawyer.cedula_profesional
        abogado_especialidad = contract.assigned_lawyer.especialidad or ""
        abogado_telefono = contract.assigned_lawyer.telefono or ""
        abogado_domicilio = contract.assigned_lawyer.domicilio_profesional or ""

    html_content = template.render(
        nombre_completo=f"{client.name} {client.paterno} {client.materno or ''}",
        curp=client.curp,
        telefono=client.phone,
        folio=client.folio_registro,
        contrato_id=contract.id,
        servicio=service_type_map.get(contract.service_type, contract.service_type),
        tipo_juicio=tipo_juicio_display or None,
        detalle=contract.specific_detail or None,
        # ── Abogado asignado ──
        abogado_nombre=abogado_nombre,
        abogado_cedula=abogado_cedula,
        abogado_especialidad=abogado_especialidad,
        abogado_telefono=abogado_telefono,
        abogado_domicilio=abogado_domicilio,
        expediente_interno=client.expediente_interno,
        costo_total=f"{total_cost:,.2f}",
        monto_pagado=f"{monto_pagado:,.2f}" if payment else None,
        forma_pago=forma_pago_map.get(payment.method, "") if payment else "",
        fecha_pago=payment.payment_date.strftime("%d/%m/%Y %H:%M") if payment else "",
        recibio=payment.receiver_name or "" if payment else "",
        saldo_restante=f"{saldo_restante:,.2f}",
        estatus="LIQUIDADO" if contract.status == "liquidado" else "PENDIENTE",
        badge_class=(
            "badge-liquidado" if contract.status == "liquidado" else "badge-pendiente"
        ),
        fecha_contratacion=(
            contract.created_at.strftime("%d/%m/%Y %H:%M")
            if contract.created_at
            else ""
        ),
        # ── Ciudad y estado por defecto (Sinaloa) ──
        ciudad=(contract.ciudad_suscripcion or "").strip() or "Mazatlán",
        estado="Sinaloa",
        anio=datetime.now().strftime("%Y"),
        logo_base64=logo_base64,
    )
    return HTML(string=html_content).write_pdf()


# ============================================================
# FORMULARIO
# ============================================================
@router.get("/register", response_class=HTMLResponse)
async def show_contract_form():
    with open("app/templates/register_contract.html", "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())


# ============================================================
# BUSCAR CLIENTES
# ============================================================
@router.post("/search-client")
async def search_client(search_term: str = Form(...), db: Session = Depends(get_db)):
    try:
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
                    Client.expediente_interno == (safe_int(search_term) or -1),
                )
            )
            .all()
        )

        if not clients:
            raise HTTPException(404, "No se encontraron clientes")

        return [
            {
                "id": c.id,
                "nombre_completo": f"{c.name} {c.paterno} {c.materno or ''}",
                "curp": c.curp,
                "telefono": c.phone,
                "expediente_interno": c.expediente_interno,
                "folio_registro": c.folio_registro,
            }
            for c in clients
        ]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Error al buscar: {str(e)}")


# ============================================================
# INFO DEL CLIENTE
# ============================================================
@router.get("/client/{client_id}")
async def get_client_info(client_id: int, db: Session = Depends(get_db)):
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(404, "Cliente no encontrado")

    return {
        "id": client.id,
        "nombre_completo": f"{client.name} {client.paterno} {client.materno or ''}",
        "curp": client.curp,
        "telefono": client.phone,
        "expediente_interno": client.expediente_interno,
        "folio_registro": client.folio_registro,
    }


# ============================================================
# REGISTRAR CONTRATO (sube comprobante a R2)
# ============================================================
@router.post("/register/{client_id}")
async def register_contract(
    request: Request,
    client_id: int,
    service_type: str = Form(...),
    tipo_juicio: str = Form(None),
    tipo_juicio_otro: str = Form(None),
    specific_detail: str = Form(None),
    assigned_lawyer_id: int = Form(None),
    ciudad_suscripcion: str = Form(None),
    total_cost: float = Form(...),
    payment_amount: float = Form(0.0),
    payment_method: str = Form(...),
    receiver_name: str = Form(None),
    modalidad_pago_saldo: str = Form("audiencia"),
    # ── Datos de contestación (opcionales) ──
    num_expediente_contestar: str = Form(None),
    tribunal_contestar: str = Form(None),
    actor_nombre: str = Form(None),
    actor2_nombre: str = Form(None),
    demandado_nombre: str = Form(None),
    fecha_notificacion_demanda: str = Form(None),
    fecha_vencimiento_plazo: str = Form(None),
    requiere_reconvencion: str = Form(None),
    receipt_file: UploadFile = File(None),
    db: Session = Depends(get_db),
):
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(404, "Cliente no encontrado")

    current_user = get_current_user(request, db)
    if not current_user:
        raise HTTPException(401, "No autenticado")

    # ── Validar abogado asignado ──
    if not assigned_lawyer_id:
        raise HTTPException(400, "Selecciona el abogado que llevará el caso")

    from app.models.core import Abogado

    abogado = (
        db.query(Abogado)
        .filter(
            Abogado.id == assigned_lawyer_id,
            Abogado.firm_id == current_user.firm_id,
            Abogado.is_active == True,
        )
        .first()
    )
    if not abogado:
        raise HTTPException(
            400, "El abogado seleccionado no es válido o está inactivo"
        )

    # Validar modalidad de pago del saldo (SOLO aplica para contestación)
    if (service_type or "").strip().lower() == "contestacion":
        modalidad = (modalidad_pago_saldo or "").strip().lower()
        if modalidad not in ("audiencia", "abonos"):
            modalidad = "audiencia"
    else:
        # Para cualquier otro servicio, no aplica modalidad
        modalidad = None

    # ── Convertir fechas de contestación ──
    from datetime import datetime as _dt

    def _parse_date(s):
        if not s or not str(s).strip():
            return None
        for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
            try:
                return _dt.strptime(str(s).strip(), fmt).date()
            except ValueError:
                continue
        return None

    fecha_notif_demanda = _parse_date(fecha_notificacion_demanda)
    fecha_venc_plazo = _parse_date(fecha_vencimiento_plazo)
    req_reconv = str(requiere_reconvencion or "").strip().lower() in (
        "true",
        "1",
        "on",
        "si",
        "sí",
        "yes",
    )

    # ── Divorcio bilateral: no hay actor/demandado, hay 2 promoventes ──
    # ── Divorcio bilateral: Promovente 1 = cliente registrado, Promovente 2 = aval ──
    _tj = f"{tipo_juicio or ''} {tipo_juicio_otro or ''}".lower()
    es_bilateral = "bilateral" in _tj

    # Nombre completo del cliente (siempre es el Promovente 1 / firmante)
    cliente_nombre_completo = (
        " ".join(p for p in [client.name, client.paterno, client.materno] if p)
        .strip()
        .upper()
    )

    if es_bilateral:
        # Promovente 1 SIEMPRE es el cliente del sistema (ignora lo que mande el form)
        actor_final = cliente_nombre_completo
        # Promovente 2 = aval (lo captura el usuario)
        actor2_final = (actor2_nombre or "").strip().upper() or None
        demandado_final = None

        if not actor2_final:
            raise HTTPException(
                400, "Debes capturar el nombre del segundo promovente (aval)."
            )
    else:
        actor_final = (actor_nombre or "").strip().upper() or None
        actor2_final = None
        demandado_final = (demandado_nombre or "").strip().upper() or None

    new_contract = Contract(
        client_id=client_id,
        firm_id=current_user.firm_id,
        service_type=service_type,
        tipo_juicio=tipo_juicio,
        tipo_juicio_otro=tipo_juicio_otro,
        specific_detail=specific_detail,
        assigned_lawyer_id=abogado.id,
        ciudad_suscripcion=(ciudad_suscripcion or "").strip() or None,
        total_cost=total_cost,
        modalidad_pago_saldo=modalidad,
        num_expediente_contestar=(num_expediente_contestar or "").strip() or None,
        tribunal_contestar=(tribunal_contestar or "").strip() or None,
        actor_nombre=actor_final,
        actor2_nombre=actor2_final,
        demandado_nombre=demandado_final,
        fecha_notificacion_demanda=fecha_notif_demanda,
        fecha_vencimiento_plazo=fecha_venc_plazo,
        requiere_reconvencion=req_reconv,
        status="pendiente" if payment_amount < total_cost else "liquidado",
    )
    db.add(new_contract)
    db.commit()
    db.refresh(new_contract)

    # Bitácora
    from app.models.core import ActivityLog

    db.add(
        ActivityLog(
            firm_id=current_user.firm_id,
            user_id=current_user.id,
            action="create",
            entity="Contrato",
            entity_id=new_contract.id,
            description=(
                f"Contratación para {client.name} {client.paterno} "
                f"— Abogado asignado: {abogado.nombre_completo}"
            ),
        )
    )
    db.commit()

    payment = None
    if payment_amount > 0:
        receipt_key = None

        if receipt_file and receipt_file.filename:
            receipt_key = (
                f"contratos/{client.folio_registro}/recibo_{uuid.uuid4().hex[:8]}.pdf"
            )
            folder = receipt_key.rsplit("/", 1)[0]
            filename = receipt_key.rsplit("/", 1)[-1]
            await receipt_file.seek(0)
            upload_fileobj(receipt_file.file, folder, filename)

        payment = Payment(
            contract_id=new_contract.id,
            firm_id=current_user.firm_id,
            amount=payment_amount,
            method=payment_method,
            receiver_name=receiver_name,
            receipt_pdf_url=receipt_key,
        )
        db.add(payment)
        db.commit()
        db.refresh(payment)

    db.refresh(new_contract)
    db.refresh(client)

    # Forzar carga de la relación assigned_lawyer ANTES de generar el PDF
    _ = new_contract.assigned_lawyer

    # Después de crear el contrato, devolver el JSON con el ID.
    # El frontend solicitará /api/contracts/legal-docs/{id} que genera
    # los 4 documentos juntos (Constancia + Contrato + Reconocimiento + Pagaré).
    return {
        "ok": True,
        "contract_id": new_contract.id,
        "folio": client.folio_registro,
        "message": "Contrato registrado. Descargando documentos legales...",
    }


# ============================================================
# GENERAR DOCUMENTOS LEGALES DEL CONTRATO (Contrato + Reconocimiento + Pagaré)
# ============================================================
# Servicios que SÍ generan el paquete completo (contrato + recon. + pagaré)
FULL_DOCS_SERVICES = {"juicio", "contestacion"}


@router.get("/legal-docs/{contract_id}")
async def generate_legal_docs(
    contract_id: int,
    request: Request,
    mode: str = "full",  # "full" | "constancia"
    db: Session = Depends(get_db),
):
    """Genera los documentos del contrato.

    - mode="full"       → Constancia + Contrato + Reconocimiento + Pagaré
    - mode="constancia" → Solo la Constancia de Contratación y Pago
    - Si el service_type NO está en FULL_DOCS_SERVICES, siempre se ignora
      "full" y se devuelve solo la constancia.
    """
    from jinja2 import Environment, FileSystemLoader
    from weasyprint import HTML
    import pikepdf
    import io
    import os
    import base64
    from datetime import datetime, timedelta, timezone
    from app.services.numero_a_letra import numero_a_letra, fecha_a_letra
    from app.models.core import ActivityLog

    # ── Autenticación ──
    current_user = get_current_user(request, db)
    if not current_user:
        raise HTTPException(401, "No autenticado")

    # ── Contrato ──
    contract = (
        db.query(Contract)
        .filter(
            Contract.id == contract_id,
            Contract.firm_id == current_user.firm_id,
        )
        .first()
    )
    if not contract:
        raise HTTPException(404, "Contrato no encontrado")

    # ── Cliente titular ──
    client = db.query(Client).filter(Client.id == contract.client_id).first()
    if not client:
        raise HTTPException(404, "Cliente no encontrado")

    # ── Abogado asignado (con fallback para contratos viejos) ──
    abogado = contract.assigned_lawyer
    if not abogado:
        from app.models.core import Abogado

        abogado = db.query(Abogado).filter(Abogado.user_id == current_user.id).first()
        if not abogado:
            # Fallback: usar datos del usuario como profesionista provisional
            class _TempAbogado:
                pass

            abogado = _TempAbogado()
            abogado.nombre_completo = current_user.full_name
            abogado.cedula_profesional = "____________________"
            abogado.telefono = ""
            abogado.especialidad = ""
            abogado.domicilio_profesional = ""

    # ── Primer pago del contrato (anticipo) ──
    first_payment = (
        db.query(Payment)
        .filter(Payment.contract_id == contract.id)
        .order_by(Payment.payment_date.asc())
        .first()
    )

    # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    # FECHAS
    # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    # Zona horaria de Sinaloa (UTC-7). Se usa offset fijo para
    # evitar dependencia de tzdata en Windows.
    tz_sinaloa = timezone(timedelta(hours=-7))
    ahora = datetime.now(tz_sinaloa)

    # Fecha de hoy en letras y corta
    fecha_letra = fecha_a_letra(ahora)
    fecha_letra_corta = ahora.strftime("%d/%m/%Y %H:%M")

    # Fecha de firma del contrato (usa created_at, que viene en UTC)
    fecha_firma = (
        contract.created_at.replace(tzinfo=timezone.utc).astimezone(tz_sinaloa)
        if contract.created_at
        else ahora
    )
    fecha_firma_letra = fecha_a_letra(fecha_firma)
    fecha_firma_corta = fecha_firma.strftime("%d/%m/%Y")

    # Fecha del primer pago
    fecha_pago_corta = ""
    if first_payment and first_payment.payment_date:
        fp = first_payment.payment_date.replace(tzinfo=timezone.utc).astimezone(
            tz_sinaloa
        )
        fecha_pago_corta = fp.strftime("%d/%m/%Y")

    # NOTA: el pagaré es "a la vista" y su exigibilidad se remite a la
    # cláusula sexta del contrato, por lo que NO se calcula fecha_vencimiento.

    # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    # LOGO
    # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    logo_path = os.path.join(os.getcwd(), "app", "static", "img", "logo.jpeg")
    logo_base64 = ""
    if os.path.exists(logo_path):
        with open(logo_path, "rb") as f:
            logo_base64 = base64.b64encode(f.read()).decode("utf-8")

    # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    # BANDERAS CONDICIONALES Y SUPUESTO DE EXIGIBILIDAD
    # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    tipo_juicio_raw = (
        f"{contract.tipo_juicio or ''} {contract.tipo_juicio_otro or ''}"
    ).lower()
    es_divorcio = "divorcio" in tipo_juicio_raw
    es_contestacion = (contract.service_type or "").lower() == "contestacion"

    if es_divorcio:
        supuesto_exigibilidad_texto = (
            "la celebración de la audiencia de divorcio prevista por la "
            "legislación aplicable (Cláusula Sexta, fracción II del contrato relacionado)"
        )
    else:
        supuesto_exigibilidad_texto = (
            "la conclusión del procedimiento contratado "
            "(Cláusula Sexta, fracción I del contrato relacionado)"
        )

    # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    # TOTALES
    # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    total = float(contract.total_cost)
    anticipo = (
        float(first_payment.amount)
        if first_payment
        else float(contract.monto_anticipo or 0)
    )
    saldo = total - anticipo

    # Pago en dos parcialidades por defecto, si hay anticipo y saldo
    pago_dos_parcialidades = anticipo > 0 and saldo > 0

    # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    # CONTEXTO PARA TEMPLATES
    # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    ctx = {
        "logo_base64": logo_base64,
        "contrato_id": contract.id,
        "ciudad": (contract.ciudad_suscripcion or "").strip() or "Mazatlán",
        "estado": "Sinaloa",
        "fecha_letra": fecha_letra,
        "fecha_corta": fecha_letra_corta,
        "fecha_firma_letra": fecha_firma_letra,
        "fecha_firma_corta": fecha_firma_corta,
        "fecha_pago_corta": fecha_pago_corta,
        "anio": ahora.year,
        # Cliente
        "cliente_nombre": f"{client.name} {client.paterno} {client.materno or ''}".strip(),
        "cliente_curp": client.curp,
        "cliente_telefono": client.phone,
        "cliente_email": client.email or "",
        "cliente_domicilio": client.address,
        # Profesionista
        "profesionista_nombre": abogado.nombre_completo,
        "profesionista_cedula": abogado.cedula_profesional,
        "profesionista_domicilio": getattr(abogado, "domicilio_profesional", "") or "",
        "profesionista_telefono": getattr(abogado, "telefono", "") or "",
        # Procedimiento
        "tipo_juicio": contract.tipo_juicio_otro
        or contract.tipo_juicio
        or "Divorcio Bilateral",
        "numero_expediente": None,  # se llena cuando exista CourtCase
        # ── Divorcio bilateral: cliente + aval (2° promovente) ──
        "es_bilateral": (
            "bilateral"
            in f"{contract.tipo_juicio or ''} {contract.tipo_juicio_otro or ''}".lower()
        ),
        "aval_nombre": (contract.actor2_nombre or "").strip(),
        # Montos
        "honorarios_numero": f"{total:,.2f}",
        "honorarios_letra": numero_a_letra(total),
        "anticipo_numero": f"{anticipo:,.2f}",
        "anticipo_letra": numero_a_letra(anticipo),
        "saldo_numero": f"{saldo:,.2f}",
        "saldo_letra": numero_a_letra(saldo),
        "monto_pagado": f"{anticipo:,.2f}",
        "monto_pagado_letra": numero_a_letra(anticipo),
        # Exigibilidad
        "supuesto_exigibilidad": supuesto_exigibilidad_texto,
        # Pago
        # Pago
        "medio_pago_anticipo": first_payment.method if first_payment else None,
        "domicilio_pago": getattr(abogado, "domicilio_profesional", "") or "",
        "pago_dos_parcialidades": pago_dos_parcialidades,
        # Modalidad de pago del saldo
        "modalidad_pago_saldo": (contract.modalidad_pago_saldo or "audiencia"),
        "es_pago_audiencia": (contract.modalidad_pago_saldo or "audiencia")
        == "audiencia",
        "es_pago_abonos": (contract.modalidad_pago_saldo or "audiencia") == "abonos",
        # Monto del primer abono (50% del saldo) si es modalidad "abonos"
        "monto_abono_uno": f"{saldo / 2:,.2f}" if saldo > 0 else "0.00",
        "monto_abono_uno_letra": (
            numero_a_letra(saldo / 2) if saldo > 0 else numero_a_letra(0)
        ),
        "monto_abono_dos": f"{saldo / 2:,.2f}" if saldo > 0 else "0.00",
        "monto_abono_dos_letra": (
            numero_a_letra(saldo / 2) if saldo > 0 else numero_a_letra(0)
        ),
        # Banderas condicionales para cláusulas
        "es_divorcio": es_divorcio,
        "es_contestacion": es_contestacion,
        # Datos específicos de contestación
        "num_expediente_contestar": contract.num_expediente_contestar or "",
        "tribunal_contestar": contract.tribunal_contestar or "",
        "actor_nombre": contract.actor_nombre or "",
        "demandado_nombre": contract.demandado_nombre or "",
        "fecha_notificacion_demanda": (
            contract.fecha_notificacion_demanda.strftime("%d/%m/%Y")
            if contract.fecha_notificacion_demanda
            else ""
        ),
        "fecha_vencimiento_plazo": (
            contract.fecha_vencimiento_plazo.strftime("%d/%m/%Y")
            if contract.fecha_vencimiento_plazo
            else ""
        ),
        "requiere_reconvencion": bool(contract.requiere_reconvencion),
        # Campos de ratificación notarial (se llenan después, si aplica)
        "ratificado": False,
        "fecha_ratificacion": "",
        "numero_notario": "",
        "nombre_notario": "",
        "numero_instrumento": "",
    }

    # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    # RENDERIZADO DE LOS 4 PDFS
    # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    env = Environment(loader=FileSystemLoader("app/templates"))

    # 1. Constancia de Contratación y Pago (SIEMPRE se genera)
    pdf_constancia = generate_payment_receipt(client, contract, first_payment)

    # ── Decidir si se genera el paquete completo ──
    service_lower = (contract.service_type or "").lower()
    generar_completo = (mode == "full") and (service_lower in FULL_DOCS_SERVICES)

    # Si es solo constancia → devolver directo, sin tocar templates pesados
    if not generar_completo:
        db.add(
            ActivityLog(
                firm_id=current_user.firm_id,
                user_id=current_user.id,
                action="download",
                entity="Contract",
                entity_id=contract.id,
                description=f"Descargó constancia de contratación del contrato {contract.id}",
            )
        )
        db.commit()
        return Response(
            content=pdf_constancia,
            media_type="application/pdf",
            headers={
                "Content-Disposition": (
                    f"inline; filename=constancia_contrato_{contract.id}.pdf"
                )
            },
        )

    # 2. Contrato de prestación de servicios profesionales
    pdf_contrato = HTML(
        string=env.get_template("pdf/contrato_prestacion_servicios.html").render(**ctx)
    ).write_pdf()

    # 3. Reconocimiento de saldo y obligación de pago
    pdf_reconocimiento = HTML(
        string=env.get_template("pdf/reconocimiento_saldo.html").render(**ctx)
    ).write_pdf()

    # 4. Pagaré
    pdf_pagare = HTML(
        string=env.get_template("pdf/pagare.html").render(**ctx)
    ).write_pdf()

    # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    # FUSIÓN DE LOS 4 PDFS EN UN SOLO BUNDLE
    # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    pdf_final = pikepdf.Pdf.new()
    for pdf_bytes in (pdf_constancia, pdf_contrato, pdf_reconocimiento, pdf_pagare):
        pdf_final.pages.extend(pikepdf.open(io.BytesIO(pdf_bytes)).pages)

    out = io.BytesIO()
    pdf_final.save(out)
    out.seek(0)

    # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    # BITÁCORA
    # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    db.add(
        ActivityLog(
            firm_id=current_user.firm_id,
            user_id=current_user.id,
            action="download",
            entity="Contract",
            entity_id=contract.id,
            description=f"Descargó documentos legales del contrato {contract.id}",
        )
    )
    db.commit()

    # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    # RESPUESTA
    # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    return Response(
        content=out.getvalue(),
        media_type="application/pdf",
        headers={
            "Content-Disposition": (
                f"inline; filename=documentos_legales_contrato_{contract.id}.pdf"
            )
        },
    )

    # ============================================================


# SUBIR DOCUMENTO LEGAL FIRMADO (contrato / reconocimiento / pagaré)
# ============================================================
SIGNED_DOC_FIELDS = {
    "contrato": "contrato_firmado_key",
    "reconocimiento": "reconocimiento_firmado_key",
    "pagare": "pagare_firmado_key",
    "constancia_actualizacion": "constancia_actualizacion_firmado_key",
}

# Orden de progreso del estado documental
ESTADO_PROGRESO = [
    "sin_contrato",
    "contrato_generado",
    "contrato_firmado",
    "reconocimiento_generado",
    "reconocimiento_firmado",
    "pagare_generado",
    "pagare_firmado",
    "liquidado",
]


@router.post("/upload-signed/{contract_id}")
async def upload_signed_document(
    contract_id: int,
    request: Request,
    doc_type: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """Sube un PDF firmado (contrato, reconocimiento o pagaré) a R2."""
    from app.storage import upload_bytes
    from app.models.core import ActivityLog

    current_user = get_current_user(request, db)
    if not current_user:
        raise HTTPException(401, "No autenticado")

    if doc_type not in SIGNED_DOC_FIELDS:
        raise HTTPException(400, "Tipo de documento inválido")

    contract = (
        db.query(Contract)
        .filter(Contract.id == contract_id, Contract.firm_id == current_user.firm_id)
        .first()
    )
    if not contract:
        raise HTTPException(404, "Contrato no encontrado")

    # Leer y validar
    content = await file.read()
    if not content:
        raise HTTPException(400, "Archivo vacío")
    if len(content) > 25 * 1024 * 1024:
        raise HTTPException(400, "El archivo excede 25 MB")
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, "Solo se aceptan PDFs")

    # Subir a R2
    key = f"contratos/{contract.id}/firmados/{doc_type}_{uuid.uuid4().hex[:8]}.pdf"
    folder, filename = key.rsplit("/", 1)
    upload_bytes(content, folder, filename, content_type="application/pdf")

    # Borrar anterior si existía
    field_name = SIGNED_DOC_FIELDS[doc_type]
    old_key = getattr(contract, field_name, None)
    if old_key:
        try:
            from app.storage import delete_file

            delete_file(old_key)
        except Exception:
            pass

    setattr(contract, field_name, key)

    # Actualizar estado_documental
    firmados = [
        ("contrato", contract.contrato_firmado_key),
        ("reconocimiento", contract.reconocimiento_firmado_key),
        ("pagare", contract.pagare_firmado_key),
        ("constancia_actualizacion", contract.constancia_actualizacion_firmado_key),
    ]
    todos_firmados = all(k for _, k in firmados)
    if todos_firmados:
        contract.estado_documental = "liquidado"
    else:
        contract.estado_documental = f"{doc_type}_firmado"

    db.add(
        ActivityLog(
            firm_id=current_user.firm_id,
            user_id=current_user.id,
            action="upload",
            entity="Contract",
            entity_id=contract.id,
            description=f"Subió {doc_type} firmado del contrato {contract.id}",
        )
    )
    db.commit()

    return {
        "ok": True,
        "doc_type": doc_type,
        "estado_documental": contract.estado_documental,
        "key": key,
    }

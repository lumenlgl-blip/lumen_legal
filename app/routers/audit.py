from fastapi import APIRouter, Depends, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from sqlalchemy import or_, and_, func
from app.database import get_db
from app.models.core import (
    Client, Contract, CourtCase, Payment, AgendaEvent,
    Actuacion, ActivityLog,
)
from datetime import datetime, date, timedelta

router = APIRouter(prefix="/audit", tags=["Audit"])

@router.get("/", response_class=HTMLResponse)
async def show_audit(request: Request):
    with open("app/templates/audit.html", "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())

@router.get("/juicios")
async def get_juicios(
    db: Session = Depends(get_db),
    fecha_inicio: str = None,
    fecha_fin: str = None,
    tipo_juicio: str = None,
    estatus: str = None,
    tribunal: str = None,
    orden: str = "asc"
):
    query = db.query(CourtCase, Contract, Client).join(
        Contract, CourtCase.contract_id == Contract.id
    ).join(
        Client, Contract.client_id == Client.id
    )
    
    # Filtros
    if fecha_inicio:
        query = query.filter(CourtCase.fecha_presentacion >= datetime.strptime(fecha_inicio, "%Y-%m-%d").date())
    if fecha_fin:
        query = query.filter(CourtCase.fecha_presentacion <= datetime.strptime(fecha_fin, "%Y-%m-%d").date())
    if estatus:
        query = query.filter(CourtCase.status == estatus)
    if tribunal:
        query = query.filter(CourtCase.tribunal.ilike(f"%{tribunal}%"))
    
    # Ordenar
    if orden == "asc":
        query = query.order_by(CourtCase.fecha_presentacion.asc())
    else:
        query = query.order_by(CourtCase.fecha_presentacion.desc())
    
    results = query.all()
    
    juicios_data = []
    for case, contract, client in results:
        # Total pagado
        total_pagado = db.query(func.sum(Payment.amount)).filter(
            Payment.contract_id == contract.id
        ).scalar() or 0
        
        # Contar actuaciones
        total_actuaciones = db.query(func.count(AgendaEvent.id)).filter(
            AgendaEvent.court_case_id == case.id
        ).scalar() or 0
        
        juicios_data.append({
            "id": case.id,
            "num_expediente": case.num_exp_tribunal,
            "tribunal": case.tribunal,
            "secretaria": case.secretaria,
            "fecha_presentacion": case.fecha_presentacion.strftime("%d/%m/%Y"),
            "estatus": case.status,
            "cliente": f"{client.name} {client.paterno} {client.materno or ''}",
            "curp": client.curp,
            "telefono": client.phone,
            "tipo_juicio": contract.tipo_juicio or "",
            "tipo_juicio_otro": contract.tipo_juicio_otro or "",
            "servicio": contract.service_type,
            "costo_total": float(contract.total_cost),
            "total_pagado": float(total_pagado),
            "saldo": float(contract.total_cost) - float(total_pagado),
            "estatus_contrato": contract.status,
            "total_actuaciones": total_actuaciones,
            "actor_nombre": case.actor_nombre or "",
            "demandado_nombre": case.demandado_nombre or "",
        })
    
    return juicios_data

@router.get("/contratos")
async def get_contratos(
    db: Session = Depends(get_db),
    fecha_inicio: str = None,
    fecha_fin: str = None,
    servicio: str = None,
    estatus: str = None,
    orden: str = "asc"
):
    query = db.query(Contract, Client).join(
        Client, Contract.client_id == Client.id
    )
    
    if fecha_inicio:
        query = query.filter(Contract.created_at >= datetime.strptime(fecha_inicio, "%Y-%m-%d"))
    if fecha_fin:
        query = query.filter(Contract.created_at <= datetime.strptime(fecha_fin, "%Y-%m-%d"))
    if servicio:
        query = query.filter(Contract.service_type == servicio)
    if estatus:
        query = query.filter(Contract.status == estatus)
    
    if orden == "asc":
        query = query.order_by(Contract.created_at.asc())
    else:
        query = query.order_by(Contract.created_at.desc())
    
    results = query.all()
    
    contratos_data = []
    for contract, client in results:
        total_pagado = db.query(func.sum(Payment.amount)).filter(
            Payment.contract_id == contract.id
        ).scalar() or 0
        
        court_case = db.query(CourtCase).filter(CourtCase.contract_id == contract.id).first()
        
        case_info = None
        if court_case:
            case_info = {
                "num_exp_tribunal": court_case.num_exp_tribunal,
                "tribunal": court_case.tribunal,
            }
        
        contratos_data.append({
            "id": contract.id,
            "cliente": f"{client.name} {client.paterno} {client.materno or ''}",
            "curp": client.curp,
            "folio": client.folio_registro,
            "servicio": contract.service_type,
            "tipo_juicio": contract.tipo_juicio or "",
            "tipo_juicio_otro": contract.tipo_juicio_otro or "",
            "detalle": contract.specific_detail or "",
            "costo_total": float(contract.total_cost),
            "total_pagado": float(total_pagado),
            "saldo": float(contract.total_cost) - float(total_pagado),
            "estatus": contract.status,
            "fecha_contratacion": contract.created_at.strftime("%d/%m/%Y"),
            "tiene_expediente": court_case is not None,
            "court_case": case_info,
        })
    
    return contratos_data

@router.get("/resumen")
async def get_resumen(db: Session = Depends(get_db)):
    total_juicios = db.query(func.count(CourtCase.id)).scalar() or 0
    juicios_iniciados = db.query(func.count(CourtCase.id)).filter(CourtCase.status == "iniciado").scalar() or 0
    juicios_terminados = db.query(func.count(CourtCase.id)).filter(CourtCase.status == "terminado").scalar() or 0
    
    total_contratos = db.query(func.count(Contract.id)).scalar() or 0
    contratos_pendientes = db.query(func.count(Contract.id)).filter(Contract.status == "pendiente").scalar() or 0
    contratos_liquidados = db.query(func.count(Contract.id)).filter(Contract.status == "liquidado").scalar() or 0
    
    ingresos_totales = db.query(func.sum(Payment.amount)).scalar() or 0
    
    return {
        "total_juicios": total_juicios,
        "juicios_iniciados": juicios_iniciados,
        "juicios_terminados": juicios_terminados,
        "total_contratos": total_contratos,
        "contratos_pendientes": contratos_pendientes,
        "contratos_liquidados": contratos_liquidados,
        "ingresos_totales": float(ingresos_totales)
    }
    
    # ============================================================
# DETALLE COMPLETO DE UN CONTRATO (para edición)
# ============================================================
@router.get("/contract-full/{contract_id}")
async def get_contract_full(contract_id: int, db: Session = Depends(get_db)):
    contract = db.query(Contract).filter(Contract.id == contract_id).first()
    if not contract:
        raise HTTPException(404, "Contrato no encontrado")

    client = db.query(Client).filter(Client.id == contract.client_id).first()
    court_case = (
        db.query(CourtCase).filter(CourtCase.contract_id == contract.id).first()
    )

    case_data = None
    if court_case:
        case_data = {
            "id": court_case.id,
            "tribunal": court_case.tribunal,
            "secretaria": court_case.secretaria,
            "num_exp_tribunal": court_case.num_exp_tribunal,
            "folio_tribunal": court_case.folio_tribunal,
            "fecha_presentacion": court_case.fecha_presentacion.strftime("%Y-%m-%d"),
            "actor_nombre": court_case.actor_nombre or "",
            "demandado_nombre": court_case.demandado_nombre or "",
            "status": court_case.status,
        }

    return {
        "contract": {
            "id": contract.id,
            "service_type": contract.service_type,
            "tipo_juicio": contract.tipo_juicio or "",
            "tipo_juicio_otro": contract.tipo_juicio_otro or "",
            "specific_detail": contract.specific_detail or "",
            "total_cost": float(contract.total_cost),
            "status": contract.status,
        },
        "client": {
            "id": client.id,
            "nombre_completo": f"{client.name} {client.paterno} {client.materno or ''}",
        },
        "court_case": case_data,
    }


# ============================================================
# EDITAR CONTRATO + EXPEDIENTE (con password admin)
# ============================================================
@router.put("/contracts/{contract_id}")
async def edit_contract(
    contract_id: int,
    request: Request,
    admin_password: str = Form(...),
    service_type: str = Form(...),
    tipo_juicio: str = Form(None),
    tipo_juicio_otro: str = Form(None),
    specific_detail: str = Form(None),
    total_cost: float = Form(...),
    status: str = Form(...),
    # Campos del expediente (opcionales, solo si existe)
    tribunal: str = Form(None),
    secretaria: str = Form(None),
    num_exp_tribunal: str = Form(None),
    folio_tribunal: str = Form(None),
    fecha_presentacion: str = Form(None),
    actor_nombre: str = Form(None),
    actor2_nombre: str = Form(None),
    demandado_nombre: str = Form(None),
    db: Session = Depends(get_db),
):
    from app.routers.auth import get_current_user, verify_password

    # 1. Autenticación + rol admin
    user = get_current_user(request, db)
    if not user:
        raise HTTPException(401, "No autenticado")
    if user.role != "admin":
        raise HTTPException(403, "Solo administradores pueden editar contratos")
    if not verify_password(admin_password, user.hashed_password):
        raise HTTPException(400, "Contraseña de administrador incorrecta")

    # 2. Contrato
    contract = db.query(Contract).filter(Contract.id == contract_id).first()
    if not contract:
        raise HTTPException(404, "Contrato no encontrado")

    # Normalizar a minúsculas para mantener consistencia con el resto del sistema
    contract.service_type = (service_type or "").strip().lower()
    contract.tipo_juicio = tipo_juicio
    contract.tipo_juicio_otro = tipo_juicio_otro
    contract.specific_detail = specific_detail
    contract.total_cost = total_cost
    contract.status = (status or "").strip().lower()

    # 3. Expediente (si existe y se mandaron datos)
    court_case = (
        db.query(CourtCase).filter(CourtCase.contract_id == contract.id).first()
    )
    if court_case and tribunal:
        court_case.tribunal = tribunal
        court_case.secretaria = secretaria
        court_case.num_exp_tribunal = num_exp_tribunal
        court_case.folio_tribunal = folio_tribunal
        if fecha_presentacion:
            court_case.fecha_presentacion = datetime.strptime(
                fecha_presentacion, "%Y-%m-%d"
            ).date()

        # Combinar dos actores si es bilateral
        actor_final = actor_nombre
        if actor2_nombre:
            actor_final = (
                f"{actor_nombre} / {actor2_nombre}" if actor_nombre else actor2_nombre
            )
        court_case.actor_nombre = actor_final
        court_case.demandado_nombre = demandado_nombre

    # 4. Bitácora
    db.add(
        ActivityLog(
            firm_id=user.firm_id,
            user_id=user.id,
            action="update",
            entity="Contrato",
            entity_id=contract.id,
            description=f"{user.full_name} editó el contrato #{contract.id}",
        )
    )

    db.commit()

    return {"message": "Contrato actualizado correctamente"}


# ============================================================
# ELIMINAR CONTRATO (con password admin)
# ============================================================
@router.delete("/contracts/{contract_id}")
async def delete_contract(
    contract_id: int,
    request: Request,
    admin_password: str = Form(...),
    db: Session = Depends(get_db),
):
    from app.routers.auth import get_current_user, verify_password
    from app.storage import delete_file

    # 1. Autenticación + rol admin
    user = get_current_user(request, db)
    if not user:
        raise HTTPException(401, "No autenticado")
    if user.role != "admin":
        raise HTTPException(403, "Solo administradores pueden eliminar contratos")
    if not verify_password(admin_password, user.hashed_password):
        raise HTTPException(400, "Contraseña de administrador incorrecta")

    # 2. Contrato
    contract = db.query(Contract).filter(Contract.id == contract_id).first()
    if not contract:
        raise HTTPException(404, "Contrato no encontrado")

    # 3. Expediente relacionado → borrar acuse, actuaciones y eventos
    court_case = (
        db.query(CourtCase).filter(CourtCase.contract_id == contract.id).first()
    )
    if court_case:
        # Actuaciones
        for act in db.query(Actuacion).filter(Actuacion.court_case_id == court_case.id).all():
            if act.pdf_url:
                delete_file(act.pdf_url)
            db.delete(act)

        # Eventos de agenda
        for ev in db.query(AgendaEvent).filter(AgendaEvent.court_case_id == court_case.id).all():
            db.delete(ev)

        # Acuse
        if court_case.acuse_pdf_url:
            delete_file(court_case.acuse_pdf_url)

        db.delete(court_case)

    # 4. Pagos → borrar comprobantes de R2
    for payment in db.query(Payment).filter(Payment.contract_id == contract.id).all():
        if payment.receipt_pdf_url:
            delete_file(payment.receipt_pdf_url)
        db.delete(payment)

    # 5. Contrato
    db.delete(contract)

    # 6. Bitácora
    db.add(
        ActivityLog(
            firm_id=user.firm_id,
            user_id=user.id,
            action="delete",
            entity="Contrato",
            entity_id=contract_id,
            description=f"{user.full_name} eliminó el contrato #{contract_id}",
        )
    )

    db.commit()

    return {"message": "Contrato eliminado correctamente"}
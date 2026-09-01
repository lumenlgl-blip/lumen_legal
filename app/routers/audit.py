from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from sqlalchemy import or_, and_, func
from app.database import get_db
from app.models.core import Client, Contract, CourtCase, Payment, AgendaEvent
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
            "total_actuaciones": total_actuaciones
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
        
        has_case = db.query(CourtCase).filter(CourtCase.contract_id == contract.id).first() is not None
        
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
            "tiene_expediente": has_case
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